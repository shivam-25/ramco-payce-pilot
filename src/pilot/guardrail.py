"""Defence 1.5 -- Bedrock Guardrails, beside the regex gate and screen.

Why this exists
---------------
Two honest limits of the code in `pii.py` and `injection.py`:

* `pii.py` says it itself: "Names are not regex-detectable in general." It
  redacts roster names by exact match, so a person named in free text who is
  not on the roster survives the gate.
* `injection.py` says it too: "a screen alone is a filter and filters can be
  walked around." The patterns catch known phrasings. A paraphrase does not
  have to look like any of them.

Guardrails is a managed classifier that sits outside the prompt, so ticket
text cannot talk its way past it the way it can talk past a model asked to
judge the same text. It raises recall on exactly the two unbounded cases.

What it is NOT
--------------
It is not the security boundary, and nothing here moves the boundary onto it.
The boundary is still the absence of capability -- no tool, read-only
database, forbidden capabilities tested as unreachable (see `injection.py`).
A probabilistic filter in front of a system that cannot act is defence in
depth. A probabilistic filter in front of a system that *can* act would be a
single point of failure wearing a safety label.

Ordering and cost
-----------------
The regex runs first and is free. Guardrails is only called on text the regex
let through, so a ticket that is obviously hostile still costs nothing.

Failure policy is FAIL CLOSED. If the call errors or times out, the ticket is
routed to a person rather than waved through -- an unavailable screen must
not read as a clean screen.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .config import AWS_REGION

GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "1")

# Entities whose presence means a leak got past the regex gate, rather than
# merely something the model should not dwell on.
_PII_LEAK = {
    "NAME", "ADDRESS", "EMAIL", "PHONE", "AGE", "USERNAME", "PASSWORD",
    "DRIVER_ID", "CREDIT_DEBIT_CARD_NUMBER", "SWIFT_CODE", "IP_ADDRESS",
    "INTERNATIONAL_BANK_ACCOUNT_NUMBER",
}


@dataclass
class Verdict:
    """What Guardrails made of one piece of text."""
    ran: bool = False               # False when unconfigured
    intervened: bool = False
    injection: bool = False         # PROMPT_ATTACK fired
    pii: list[str] = field(default_factory=list)   # entity/regex names found
    text: str | None = None         # anonymised text, when it masked something
    spans: list[tuple[str, str]] = field(default_factory=list)  # (kind, matched)
    error: str | None = None
    ms: float = 0.0

    @property
    def clean(self) -> bool:
        return self.ran and not self.intervened and not self.error


def configured() -> bool:
    return bool(GUARDRAIL_ID)


def apply(text: str, source: str = "INPUT") -> Verdict:
    """Screen one piece of text. `source` is INPUT for a ticket, OUTPUT for a
    draft. Never raises: a transport failure comes back as Verdict.error, and
    the caller is expected to treat that as 'send it to a person'."""
    from time import perf_counter

    if not text or not configured():
        return Verdict(ran=False)

    t0 = perf_counter()
    try:
        from .bedrock import _client
        out = _client("bedrock-runtime").apply_guardrail(
            guardrailIdentifier=GUARDRAIL_ID,
            guardrailVersion=GUARDRAIL_VERSION,
            source=source,
            content=[{"text": {"text": text[:20000]}}],
        )
    except Exception as exc:                       # noqa: BLE001 -- fail closed
        return Verdict(ran=True, error=f"{type(exc).__name__}: {exc}",
                       ms=(perf_counter() - t0) * 1000)

    v = Verdict(ran=True, ms=(perf_counter() - t0) * 1000)
    v.intervened = out.get("action") == "GUARDRAIL_INTERVENED"
    _matches: list[tuple[str, str | None]] = []

    for a in out.get("assessments", []):
        for f in a.get("contentPolicy", {}).get("filters", []):
            if f.get("type") == "PROMPT_ATTACK" and f.get("action") == "BLOCKED":
                v.injection = True
        sip = a.get("sensitiveInformationPolicy", {})
        for e in sip.get("piiEntities", []):
            if e.get("type") in _PII_LEAK:
                v.pii.append(str(e["type"]))
                _matches.append((str(e["type"]), e.get("match")))
        for r in sip.get("regexes", []):
            name = str(r.get("name", "REGEX"))
            v.pii.append(name)
            _matches.append((name, r.get("match")))

    # ApplyGuardrail reports findings; with source=INPUT it does NOT hand back
    # masked text (`output` is null even when it says ANONYMIZED). It does
    # return the matched span, so the masking is done here instead -- which is
    # better anyway, because it keeps one placeholder convention across both
    # gates. The matched spans are personal data: they are used in-process to
    # build the replacement and are never logged, counted into an audit row,
    # or returned to a caller.
    if not v.injection and _matches:
        masked = text
        for kind, span in _matches:
            if span and span in masked:
                masked = masked.replace(span, f"[REDACTED:{kind}]")
        if masked != text:
            v.text = masked
        v.spans = [(k, m) for k, m in _matches if m]

    # dedupe, keep order
    v.pii = list(dict.fromkeys(v.pii))
    return v


def redact(text: str, spans: list[tuple[str, str]]) -> str:
    """Apply spans Guardrails matched to one field, in the same placeholder
    format the regex gate uses. Longest span first, so a street address is
    replaced before a name inside it."""
    if not text or not spans:
        return text
    for kind, span in sorted(spans, key=lambda s: len(s[1]), reverse=True):
        if span and span in text:
            text = text.replace(span, f"[REDACTED:{kind}]")
    return text
