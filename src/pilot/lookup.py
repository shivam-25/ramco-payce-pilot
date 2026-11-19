"""Box 3 — lookup.

Attach the facts the model must not guess: which region the customer's data
lives in, what tier they are on, and what the SLA clock actually is. The
vendor pilot answered "it may take 24-48 hours" to a P1 Enterprise salary
question because it could not see any of this (postmortem, item 5).

No eval gate: a join either finds the row or it does not.

The one number worth saying out loud: 67 of 1,367 tickets carry a customer_id
that is absent from customers.csv. They cannot be assigned a region, so they
cannot be answered from a regional database. They go to a human. That is a
data-quality finding, not a bug to paper over.
"""
from __future__ import annotations
import csv
from dataclasses import dataclass
from functools import lru_cache

from .config import DATA_ROOM

# From process/sla_contract_excerpt.md section 3.1. First-response and
# resolution clocks in ELAPSED hours from ticket creation, by tier and
# priority. The contract's tier names are Enterprise / Business / Standard and
# customers.csv agrees (12 / 18 / 30). Standard is "best effort" with no
# target and no credits, so it maps to None — a draft must not promise a
# Standard customer a time the contract does not give them.
SLA_HOURS: dict[tuple[str, str], tuple[int, int] | None] = {
    ("Enterprise", "P1"): (1, 8),
    ("Enterprise", "P2"): (4, 24),
    ("Enterprise", "P3"): (8, 72),
    ("Business", "P1"): (2, 12),
    ("Business", "P2"): (8, 48),
    ("Business", "P3"): (24, 96),
    ("Standard", "P1"): None,
    ("Standard", "P2"): None,
    ("Standard", "P3"): None,
}

# Payroll week is the 25th to the 3rd (payroll_week_runbook.md). During it,
# the first-response target for P1 payroll tickets is HALVED to 30 minutes.
PAYROLL_WEEK_P1_FIRST_RESPONSE_MINUTES = 30


def in_payroll_week(when) -> bool:
    return when is not None and (when.day >= 25 or when.day <= 3)


# SLA credit against the monthly subscription fee, by tier and priority
# (section 3.4). Multiplied by 1.5 when actual resolution exceeds twice the
# target. Template v3.2 has NO monthly cap.
CREDIT_PCT: dict[tuple[str, str], float] = {
    ("Enterprise", "P1"): 0.10, ("Enterprise", "P2"): 0.05, ("Enterprise", "P3"): 0.02,
    ("Business", "P1"): 0.05,   ("Business", "P2"): 0.03,   ("Business", "P3"): 0.01,
}
SEVERITY_MULTIPLIER = 1.5


@dataclass(frozen=True)
class Customer:
    customer_id: str
    name: str
    tier: str
    region: str
    monthly_fee_usd: float
    employees_paid: int


@lru_cache(maxsize=1)
def customers() -> dict[str, Customer]:
    path = DATA_ROOM / "data" / "customers.csv"
    out: dict[str, Customer] = {}
    with path.open() as fh:
        for row in csv.DictReader(fh):
            out[row["customer_id"]] = Customer(
                customer_id=row["customer_id"], name=row["name"],
                tier=row["tier"], region=row["region"],
                monthly_fee_usd=float(row["monthly_fee_usd"]),
                employees_paid=int(row["employees_paid"]),
            )
    return out


@lru_cache(maxsize=1)
def customer_names() -> tuple[str, ...]:
    """Literal names, handed to the PII gate so they are redacted by identity
    rather than by shape."""
    return tuple(c.name for c in customers().values())


def enrich(ticket) -> tuple[object, bool]:
    """Attach region and tier. Returns (ticket, resolved)."""
    c = customers().get(ticket.customer_id or "")
    if c is None:
        return ticket, False
    ticket.region = c.region
    ticket.tier = c.tier
    return ticket, True


def sla(tier: str | None, priority: str) -> tuple[int, int] | None:
    """(first_response_hours, resolution_hours), or None when the contract
    promises best effort. None is a real answer, not a missing one."""
    if not tier:
        return None
    return SLA_HOURS.get((tier, priority))


def credit_pct(tier: str | None, priority: str, actual_h: float,
               target_h: float) -> float:
    """Section 3.4. Returns the fraction of the monthly fee owed, 0 if none."""
    base = CREDIT_PCT.get((tier or "", priority))
    if not base or actual_h <= target_h:
        return 0.0
    return base * (SEVERITY_MULTIPLIER if actual_h > 2 * target_h else 1.0)
