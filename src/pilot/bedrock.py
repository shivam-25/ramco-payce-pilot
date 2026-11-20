"""Every Bedrock call goes through here, so every call is counted.

Gate G7 needs a real USD figure per draft. That is only possible if token
usage is recorded at the point of the call rather than estimated afterwards,
so each helper returns usage alongside the text and `Meter` accumulates it.
"""
from __future__ import annotations
import json, os, threading, time
from collections import OrderedDict
from dataclasses import dataclass, field

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import AWS_REGION, EMBED_MODEL, EMBED_DIMS, PRICES

_cfg = Config(region_name=AWS_REGION, retries={"max_attempts": 8, "mode": "adaptive"},
              read_timeout=60, connect_timeout=10)

# On-demand Bedrock on this account throttles well below what the eval harness
# wants to push. botocore's adaptive mode handles most of it; this adds a
# bounded outer retry so one 1,300-call sweep does not die on the last ticket.
THROTTLE = {"ThrottlingException", "TooManyRequestsException",
            "ServiceQuotaExceededException", "ModelNotReadyException"}


# ---------------------------------------------------------------------------
# Account rate limits, requests per minute, read from Service Quotas on
# 19 Nov 2026 for account <account>:
#
#   apac.amazon.nova-micro-v1:0                    20 rpm
#   au.anthropic.claude-haiku-4-5-...              10 rpm
#   amazon.titan-embed-text-v2:0                   60 rpm
#
# These are free-plan on-demand ceilings, not architectural limits. They do
# not constrain the pilot in production — 15,000 tickets a month is about 1.4
# requests a minute, and roughly 4 at a 3x peak — but they absolutely
# constrain an eval sweep that fires 1,341 tickets as fast as it can. Without
# a limiter the sweep spends its life in backoff and eventually dies; with
# one it simply takes the time the arithmetic says it must.
RPM = {
    "apac.amazon.nova-micro-v1:0": 20,
    "au.anthropic.claude-haiku-4-5-20251001-v1:0": 10,
    "amazon.titan-embed-text-v2:0": 60,
}
_DEFAULT_RPM = 10
_SAFETY = 0.8          # stay under the ceiling; bursts still get a 429

# The limiter is eval scaffolding, and it is OFF unless a sweep asks for it.
#
# It was not, and that cost twice. In production it can only ever add delay:
# it protects against a free-plan quota that real traffic -- 1.4 requests a
# minute against a 10-20 rpm ceiling -- never approaches, so every sleep it
# takes is a customer waiting for nothing. Two tickets triaged back to back
# in the console hit a 7.5 s wait on the second, caused entirely by this
# code. `_with_backoff` already retries what AWS actually throttles, so
# turning the limiter off does not risk correctness; it moves the wait from
# "always, pre-emptively" to "only when AWS pushes back".
#
# It also corrupted a measurement. The drafter comparison reported p50 7.40 s
# for both Nova Lite and Nova Pro and I read that as "same latency". It was
# not a latency at all: neither model appears in the table above, so both got
# `_DEFAULT_RPM` = 10, and 60 / (10 * 0.8) is 7.5 s. The figure was this
# limiter's own sleep, identical across the arms because the arms shared a
# default. A model id missing from a dict became a published number.
#
# So the default is now explicit about what it is: a fallback for a model
# whose real quota nobody has looked up. When the limiter is on and this
# fires, it says so, because silence is how the 7.40 s happened.
# Read per call rather than captured at import, so a caller that sets the
# variable does not also have to import this module after setting it. The
# gates do set it first, but one of them importing `pilot` a line early
# should not silently turn the limiter off for a whole sweep.
_warned: set[str] = set()


def limiter_enabled() -> bool:
    return os.environ.get("PILOT_RATE_LIMIT", "") not in ("", "0", "false", "no")


class _Limiter:
    """One shared token bucket per model, across every thread in the process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}

    def wait(self, model: str) -> None:
        """Reserve the next slot for this model, then sleep until it is due.

        The reservation happens exactly once, under the lock, so concurrent
        callers queue up behind each other instead of all claiming `now`.
        """
        rpm = RPM.get(model)
        if rpm is None:
            rpm = _DEFAULT_RPM
            if model not in _warned:
                _warned.add(model)
                print(f"[rate-limit] {model} is not in RPM; assuming "
                      f"{_DEFAULT_RPM} rpm, so every call to it waits "
                      f"{60.0 / (_DEFAULT_RPM * _SAFETY):.1f}s. Any latency "
                      f"measured for this model is this wait, not the model.",
                      flush=True)
        gap = 60.0 / (rpm * _SAFETY)
        with self._lock:
            now = time.monotonic()
            due = max(self._next.get(model, 0.0), now)
            self._next[model] = due + gap
        delay = due - now
        if delay > 0:
            time.sleep(delay)
            _add_throttle(delay)

    def penalise(self, model: str, seconds: float) -> None:
        """AWS pushed back, so hold the whole bucket, not just this thread.

        Without this each thread backs off alone and the others keep firing,
        so the process never actually drops below the ceiling. The quota is
        per account, so the response to a 429 has to be per account too.
        """
        with self._lock:
            self._next[model] = max(self._next.get(model, 0.0),
                                    time.monotonic()) + seconds


_limiter = _Limiter()

# Time spent asleep waiting for quota, tallied per thread. The limiter sleeps
# inside the call, so every box timing carries its wait; without this, a
# latency measurement reports the account's rate limit rather than the work.
_throttle = threading.local()


def _add_throttle(seconds: float) -> None:
    _throttle.s = getattr(_throttle, "s", 0.0) + seconds


def take_throttle_s() -> float:
    """Read this thread's throttle tally and reset it."""
    was = getattr(_throttle, "s", 0.0)
    _throttle.s = 0.0
    return was


def _with_backoff(fn, model: str, attempts: int = 8):
    """Rate-limit first, then retry what still gets throttled."""
    import random
    for i in range(attempts):
        if limiter_enabled():
            _limiter.wait(model)
        try:
            return fn()
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in THROTTLE or i == attempts - 1:
                raise
            penalty = min(2 ** i, 45) * (0.5 + random.random())
            _limiter.penalise(model, penalty)
            time.sleep(penalty)
            _add_throttle(penalty)
    raise RuntimeError("unreachable")
_local = threading.local()


def _client(name: str):
    key = f"c_{name}"
    if not hasattr(_local, key):
        setattr(_local, key, boto3.client(name, config=_cfg))
    return getattr(_local, key)


@dataclass
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0

    @property
    def usd(self) -> float:
        p = PRICES.get(self.model)
        if not p:
            return 0.0
        return (self.input_tokens / 1000) * p["in"] + (self.output_tokens / 1000) * p["out"]


@dataclass
class Meter:
    """Accumulates usage so a replay can be priced from real token counts."""
    calls: list[Usage] = field(default_factory=list)

    def add(self, u: Usage) -> Usage:
        self.calls.append(u)
        return u

    @property
    def usd(self) -> float:
        return sum(c.usd for c in self.calls)

    def by_model(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for c in self.calls:
            d = out.setdefault(c.model, {"calls": 0, "in": 0, "out": 0, "usd": 0.0})
            d["calls"] += 1; d["in"] += c.input_tokens
            d["out"] += c.output_tokens; d["usd"] += c.usd
        return out


def converse(model: str, prompt: str, system: str | None = None,
             max_tokens: int = 512, temperature: float = 0.0) -> tuple[str, Usage]:
    """One turn, no tools. The pipeline deliberately gives the model no tool:
    a model that cannot act cannot be made to act by a prompt."""
    kwargs = {
        "modelId": model,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
    }
    if system:
        kwargs["system"] = [{"text": system}]
    t0 = time.perf_counter()
    r = _with_backoff(lambda: _client("bedrock-runtime").converse(**kwargs), model)
    dt = time.perf_counter() - t0
    text = r["output"]["message"]["content"][0]["text"]
    u = r.get("usage", {})
    return text, Usage(model, u.get("inputTokens", 0), u.get("outputTokens", 0), dt)



def converse_stream(model: str, prompt: str, system: str | None = None,
                    max_tokens: int = 512, temperature: float = 0.0):
    """The same one turn, yielding text as the model writes it.

    Yields ("text", chunk) repeatedly and finally ("done", (full, Usage)).
    The caller gets the identical string `converse` would have returned, so
    nothing downstream -- citation parsing, the output check -- has to know
    which path produced it. Retries wrap establishing the stream only: once
    tokens are flowing a retry would replay text the caller already has.
    """
    kwargs = {
        "modelId": model,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
    }
    if system:
        kwargs["system"] = [{"text": system}]
    t0 = time.perf_counter()
    r = _with_backoff(
        lambda: _client("bedrock-runtime").converse_stream(**kwargs), model)
    parts: list[str] = []
    tin = tout = 0
    for event in r["stream"]:
        if "contentBlockDelta" in event:
            piece = event["contentBlockDelta"]["delta"].get("text", "")
            if piece:
                parts.append(piece)
                yield "text", piece
        elif "metadata" in event:
            u = event["metadata"].get("usage", {})
            tin = u.get("inputTokens", 0)
            tout = u.get("outputTokens", 0)
    dt = time.perf_counter() - t0
    yield "done", ("".join(parts), Usage(model, tin, tout, dt))

# Content-addressed: a key is a text and its value is that same text's own
# vector, so a hit can only ever return what the caller asked to embed.
#
# Bounded, because the runtime's process is long-lived now that AgentCore
# sessions are reused. Unbounded was fine while every request got a fresh
# microVM that was thrown away afterwards; a microVM that serves an hour of
# traffic would grow this until it was killed for memory. 4,096 vectors at
# 512 float32 dimensions is a few megabytes, and the eval sweep's 35 golden
# questions and 1,341 tickets both fit inside it.
_EMBED_CACHE_MAX = 4096
_embed_cache: "OrderedDict[str, list[float]]" = OrderedDict()
_embed_lock = threading.Lock()


def embed(text: str) -> tuple[list[float], Usage]:
    """Identical text gives an identical vector, so the bake-off does not pay
    to embed the same 35 questions sixteen times over."""
    key = text[:8000]
    with _embed_lock:
        if key in _embed_cache:
            _embed_cache.move_to_end(key)
            return _embed_cache[key], Usage(EMBED_MODEL, 0, 0, 0.0)
    v, u = _embed_uncached(text)
    with _embed_lock:
        _embed_cache[key] = v
        _embed_cache.move_to_end(key)
        while len(_embed_cache) > _EMBED_CACHE_MAX:
            _embed_cache.popitem(last=False)
    return v, u


def _embed_uncached(text: str) -> tuple[list[float], Usage]:
    t0 = time.perf_counter()
    r = _with_backoff(lambda: _client("bedrock-runtime").invoke_model(
        modelId=EMBED_MODEL,
        body=json.dumps({"inputText": text[:8000], "dimensions": EMBED_DIMS,
                         "normalize": True}),
    ), EMBED_MODEL)
    dt = time.perf_counter() - t0
    body = json.loads(r["body"].read())
    tokens = body.get("inputTextTokenCount", 0)
    return body["embedding"], Usage(EMBED_MODEL, tokens, 0, dt)


def embed_many(texts: list[str]) -> tuple[list[list[float]], Usage]:
    """Titan has no batch endpoint, so this loops. Kept in one place so the
    cost of embedding the corpus is visible rather than scattered."""
    vectors, total = [], Usage(EMBED_MODEL)
    for t in texts:
        v, u = embed(t)
        vectors.append(v)
        total.input_tokens += u.input_tokens
        total.seconds += u.seconds
    return vectors, total
