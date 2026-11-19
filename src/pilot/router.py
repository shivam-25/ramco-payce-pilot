"""Box 6 — the router, and the pipeline that runs every box in order.

The router is plain code on purpose. A prompt obeys "maybe"; the rule that
money goes to a human has to obey always. It also adds a recurring token cost
and a vendor dependency for a decision that is nine if-statements.

Order, as the brief sets it out:
    PII gate -> normalise -> lookup -> classify -> dedupe -> router
    -> cache hit, or answer, or human

The router's decisions, in priority order:
  1. injection detected          -> human, and never call a model
  2. no resolvable region        -> human (we cannot pick a regional database)
  3. money, or a P1 that blocks pay -> human approval required
  4. classifier abstained        -> human
  5. everything else             -> answer from the vector store
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum

from .lookup import in_payroll_week

# Anything that moves money, or is a question about money that has already
# moved. "A missing salary of 1,000 dirhams is not for a model to decide."
MONEY_SIGNALS = re.compile(
    r"\b(refund|chargeback|credit note|invoice|billing|overcharg|"
    r"double charg|not credited|not received|salary not|unpaid|"
    r"underpaid|overpaid|reimburse|wrong amount|short paid|arrears|"
    r"payment failed|bounced)\b", re.I)


class Route(str, Enum):
    ANSWER = "answer"
    HUMAN = "human"
    CACHE = "cache"


@dataclass
class Decision:
    route: Route
    reason: str
    queue: str | None = None
    needs_approval: bool = False
    detail: dict = field(default_factory=dict)


def decide(*, text: str, region: str | None, queue: str | None,
           priority: str, injection: bool, injection_reasons: list[str],
           received_at: datetime | None = None) -> Decision:

    if injection:
        return Decision(Route.HUMAN,
                        "prompt injection detected before any model call",
                        queue=queue, needs_approval=True,
                        detail={"injection": injection_reasons})

    if region is None:
        return Decision(Route.HUMAN,
                        "customer not found, so no regional database can be "
                        "selected and no answer may be drafted",
                        queue=queue)

    if MONEY_SIGNALS.search(text or ""):
        return Decision(Route.HUMAN, "money is involved; a person approves",
                        queue=queue, needs_approval=True)

    if priority == "P1":
        pw = in_payroll_week(received_at)
        return Decision(Route.HUMAN,
                        "P1: payroll cannot run or people will not be paid"
                        + (" (payroll week: 30-minute first response)" if pw else ""),
                        queue=queue, needs_approval=True,
                        detail={"payroll_week": pw})

    if queue is None:
        return Decision(Route.HUMAN,
                        "classifier did not return one of the nine categories",
                        queue=None)

    return Decision(Route.ANSWER, "no money, no P1, region known", queue=queue)
