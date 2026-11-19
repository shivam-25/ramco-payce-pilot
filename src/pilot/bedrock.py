"""Every Bedrock call goes through here, so every call is counted.

Gate G7 needs a real USD figure per draft. That is only possible if token
usage is recorded at the point of the call rather than estimated afterwards,
so each helper returns usage alongside the text and `Meter` accumulates it.
"""
from __future__ import annotations
import json, threading, time
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
        gap = 60.0 / (RPM.get(model, _DEFAULT_RPM) * _SAFETY)
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


_embed_cache: dict[str, list[float]] = {}


def embed(text: str) -> tuple[list[float], Usage]:
    """Identical text gives an identical vector, so the bake-off does not pay
    to embed the same 35 questions sixteen times over."""
    key = text[:8000]
    if key in _embed_cache:
        return _embed_cache[key], Usage(EMBED_MODEL, 0, 0, 0.0)
    v, u = _embed_uncached(text)
    _embed_cache[key] = v
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
