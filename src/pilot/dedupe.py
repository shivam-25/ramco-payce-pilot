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


# --------------------------------------------------- live, one ticket
INCIDENT_AT = 5          # the brief: "5th identical report -> incident"


def _today():
    from datetime import date as _date
    return _date.today()


def case_id_for(ticket, queue: str | None = None) -> str:
    """The same case id assign_cases would give this ticket, computed one
    ticket at a time. Live triage sees one ticket, not a batch, so the batch
    function cannot be used — but the two must agree, so the id is built
    here from the identical parts in the identical order.

    `queue` is the category the classifier decided. The batch path reads the
    category the customer picked, because historical tickets carry one; a
    live ticket from email or chat often does not, and keying on a missing
    field gives every ticket its own case, which silently disables the
    incident rule. What the classifier determined is also the better key:
    it is what the batch category is a noisy proxy for.
    """
    cid = ticket.customer_id
    cat = canonical_category(ticket.raw_category) or queue
    # A historical ticket carries its arrival time. A live one may not —
    # normalise only parses the export's timestamp formats — and a ticket
    # being triaged right now belongs to today's case either way. Falling
    # back to the batch rule here (no timestamp, so its own case) would mean
    # every live ticket is a singleton and the incident rule never fires,
    # which is how a feature ends up shipped and dead.
    day = ticket.received_at.date() if ticket.received_at else _today()
    if not (cid and cat):
        return f"CASE-{ticket.ticket_id}"
    return f"CASE-{cid}-{cat.replace(' ', '')[:8]}-{day:%Y%m%d}"


def record_and_count(conn, case_id: str, ticket_id: str) -> int:
    """Add this ticket to its case and return how many tickets the case now
    holds. This is what makes the incident rule automatic: the brief's
    target is 30 minutes from the fifth identical report to a declared
    incident, and the only way to beat that reliably is to count on arrival
    rather than wait for someone to notice a pattern.

    Failing to count must never fail a ticket, so a broken table costs the
    incident signal and nothing else.
    """
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS case_members ("
            " case_id text NOT NULL,"
            " ticket_id text NOT NULL,"
            " seen_at timestamptz NOT NULL DEFAULT now(),"
            " PRIMARY KEY (case_id, ticket_id))")
        conn.execute("INSERT INTO case_members (case_id, ticket_id) "
                     "VALUES (%s,%s) ON CONFLICT DO NOTHING",
                     (case_id, ticket_id))
        row = conn.execute("SELECT count(*) AS n FROM case_members "
                           "WHERE case_id = %s", (case_id,)).fetchone()
        return int(row["n"] if isinstance(row, dict) else row[0])
    except Exception:
        return 0
