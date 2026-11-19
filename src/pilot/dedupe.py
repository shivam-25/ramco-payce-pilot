"""Box 5 — dedupe. Gate G3: one client, one case.

The Head of Support's complaint is repeat tickets: the same customer raises
the same thing three times in a morning — email, then the widget, then a
phone call — and three agents work it.

Two tickets merge when all three hold:
  * same customer
  * same canonical category
  * the same calendar day

A rolling 24-hour window was tried first and rejected on the data. C-1001
raises Integration tickets steadily from 9 to 11 September; a rolling window
chains them into one case spanning three days, because each ticket falls
inside 24 hours of the last. That merges Wednesday's problem into Monday's.
A calendar day is the unit the support floor actually works in, and it is the
unit the brief names: "2 more tickets from C-1003 TODAY, merged into 1 case".

The cost of the stricter rule is that an incident running across midnight
becomes two cases. That is the conservative error — splitting costs a second
triage, merging loses a customer's problem — and the count is reported.

Deliberately NOT merged: two different categories from one customer on one
day. A customer with a payroll failure and a login problem has two problems,
and merging them loses one. The gate tests both directions.

This is plain code. A prompt asked "are these the same issue?" would obey
"maybe"; a human waiting on a P1 needs it to obey always.
"""
from __future__ import annotations
from collections import defaultdict
from .taxonomy import canonical_category


def case_key(ticket) -> tuple | None:
    cat = canonical_category(ticket.raw_category)
    if not (ticket.customer_id and cat and ticket.received_at):
        return None
    return (ticket.customer_id, cat)


def assign_cases(tickets: list) -> dict[str, str]:
    """Return {ticket_id: case_id}.

    Tickets with no key — no customer, no parseable category, no timestamp —
    get their own case. Never merge on missing data: that is how two
    customers' problems end up in one thread.
    """
    buckets: dict[tuple, list] = defaultdict(list)
    singles: dict[str, str] = {}

    for t in tickets:
        key = case_key(t)
        if key is None:
            singles[t.ticket_id] = f"CASE-{t.ticket_id}"
        else:
            buckets[(key[0], key[1], t.received_at.date())].append(t)

    out = dict(singles)
    for (cid, cat, day), group in buckets.items():
        case_id = f"CASE-{cid}-{cat.replace(' ', '')[:8]}-{day:%Y%m%d}"
        for t in group:
            out[t.ticket_id] = case_id
    return out


def midnight_splits(tickets: list, cases: dict[str, str]) -> int:
    """How many cases are a continuation of one four hours either side of
    midnight — the price of the calendar-day rule, counted rather than
    assumed."""
    from collections import defaultdict
    by_key = defaultdict(list)
    for t in tickets:
        k = case_key(t)
        if k:
            by_key[k].append(t)
    splits = 0
    for group in by_key.values():
        group.sort(key=lambda t: t.received_at)
        for a, b in zip(group, group[1:]):
            if (a.received_at.date() != b.received_at.date()
                    and (b.received_at - a.received_at).total_seconds() <= 4 * 3600):
                splits += 1
    return splits


def summarise(tickets: list, cases: dict[str, str]) -> dict:
    sizes = defaultdict(int)
    for tid in cases.values():
        sizes[tid] += 1
    merged = sum(n for n in sizes.values() if n > 1)
    return {
        "tickets": len(tickets),
        "cases": len(sizes),
        "clusters": sum(1 for n in sizes.values() if n > 1),
        "tickets_in_clusters": merged,
        "reduction": 1 - len(sizes) / len(tickets) if tickets else 0.0,
    }
