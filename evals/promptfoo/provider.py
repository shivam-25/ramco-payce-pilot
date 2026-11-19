"""Promptfoo provider: runs the code-only part of the pipeline.

No database and no model. The three properties under test here — personal
data is removed, injections are refused, money goes to a human — are all
decided in code, which is exactly why they can be gated on every commit for
nothing. If one of them needed a model to hold, it would not be a rule.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from pilot.injection import screen                              # noqa: E402
from pilot.pii import downstream_text, redact_ticket            # noqa: E402
from pilot.router import Route, decide                          # noqa: E402


def call_api(prompt: str, options=None, context=None):
    text = prompt
    ticket = {"subject": "", "body": text}
    # A note inside an event history is the case the first version of the
    # gate missed, so the suite can address it directly.
    if text.startswith("__EVENT_NOTE__"):
        ticket = {"subject": "", "body": "",
                  "events": [{"meta": {"note": text.replace("__EVENT_NOTE__", "").strip()}}]}

    masked, red = redact_ticket(ticket)
    clean = downstream_text(masked)

    # screen() returns (blocked, reasons). Unpacking matters: the tuple
    # itself is always truthy, so `bool(screen(...))` marks every ticket as
    # an injection — which is exactly the sort of silent always-pass the
    # benign cases in this suite exist to catch.
    hit, reasons = screen(clean)
    d = decide(text=clean, region="IN", queue="General-L1", priority="P3",
               injection=hit, injection_reasons=list(reasons))

    return {"output": json.dumps({
        "downstream_text": clean,
        "pii_counts": dict(red.counts),
        "injection_hit": hit,
        "model_calls": 0,
        "route": d.route.value.lower(),
        "reason": d.reason,
        "needs_approval": bool(d.needs_approval),
    })}
