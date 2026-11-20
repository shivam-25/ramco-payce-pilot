"""The customer-facing portal: one organisation's own tickets, and nothing else.

Three things are worth stating plainly, because they are the whole security
argument for putting a customer-facing surface on this system at all.

**Scoping is a predicate, not a filter in the browser.** Every query here
takes `customer_id` in its `WHERE`, and the resolve statement takes it too,
so a caller holding another organisation's ticket id still updates zero rows.
The browser is never trusted to say which organisation it is; the session
cookie is read on the server and the id comes from there.

**The text served is the masked text.** `tickets.subject_masked` and
`tickets.body_masked` are written by the loader, which raises rather than
insert a row that still carries an identifier. The portal never reads a raw
column because there is no raw column to read.

**Resolving is the only write, and it is not reachable from the drafting
path.** The database user is the instance master -- "read only" in this
system has always meant that the drafting code contains no statement that
writes, not that a grant forbids it. That distinction matters now that one
write exists, so it is confined to this module, touches two columns of one
table, and is scoped by organisation. Money is still never touched: a
resolved ticket is a support record, not a payment.
"""
from __future__ import annotations
from datetime import datetime, timezone

from .db import connect

# The export carries four spellings of the same end state -- 'Closed',
# 'closed', 'CLOSED' and 'Resolved' -- plus NULL for everything still open.
# Comparing in SQL with lower() rather than listing spellings means a fifth
# spelling arriving tomorrow still lands on the right side of the filter.
_DONE = ("closed", "resolved", "done", "completed")

_COLS = ("ticket_id, subject_masked, canonical_category, priority, status, "
         "received_at, first_response_at, resolved_at, queue_final, case_id")


def _shape(r: dict) -> dict:
    done = (r.get("status") or "").strip().lower() in _DONE
    return {
        "ticket_id": r["ticket_id"],
        "subject": r.get("subject_masked") or "(no subject)",
        "category": r.get("canonical_category") or "Uncategorised",
        "priority": r.get("priority") or "P3",
        "state": "resolved" if done else "open",
        "status_raw": r.get("status"),
        "received_at": str(r.get("received_at") or ""),
        "first_response_at": str(r.get("first_response_at") or ""),
        "resolved_at": str(r.get("resolved_at") or ""),
        "queue": r.get("queue_final") or "",
        "case_id": r.get("case_id") or "",
    }


def organisation(customer_id: str, region: str) -> dict | None:
    with connect(region) as conn:
        row = conn.execute(
            "SELECT customer_id, name, tier, region FROM customers "
            "WHERE customer_id = %s", (customer_id,)).fetchone()
    return dict(row) if row else None


def tickets(customer_id: str, region: str, state: str = "all",
            limit: int = 50, offset: int = 0) -> dict:
    """One organisation's tickets, newest first, with the tab counts."""
    where = ["customer_id = %s"]
    args: list = [customer_id]
    if state == "open":
        where.append("(status IS NULL OR lower(btrim(status)) <> ALL(%s))")
        args.append(list(_DONE))
    elif state == "resolved":
        where.append("lower(btrim(status)) = ANY(%s)")
        args.append(list(_DONE))

    sql = (f"SELECT {_COLS} FROM tickets WHERE " + " AND ".join(where)
           + " ORDER BY received_at DESC NULLS LAST LIMIT %s OFFSET %s")
    args += [min(limit, 200), max(offset, 0)]

    with connect(region) as conn:
        rows = conn.execute(sql, args).fetchall()
        tally = conn.execute(
            "SELECT count(*) FILTER (WHERE status IS NULL "
            "         OR lower(btrim(status)) <> ALL(%s)) AS open, "
            "       count(*) FILTER (WHERE lower(btrim(status)) = ANY(%s)) AS resolved, "
            "       count(*) AS all "
            "  FROM tickets WHERE customer_id = %s",
            (list(_DONE), list(_DONE), customer_id)).fetchone()

    return {"tickets": [_shape(dict(r)) for r in rows],
            "counts": {k: int(tally[k]) for k in ("open", "resolved", "all")}}


def resolve(customer_id: str, region: str, ticket_id: str,
            resolved: bool = True) -> dict:
    """Mark one ticket resolved, or reopen it.

    Scoped by organisation in the statement itself: a ticket id belonging to
    someone else matches no row and the call reports that it changed nothing,
    rather than reporting a success it did not perform.
    """
    now = datetime.now(timezone.utc)
    with connect(region) as conn:
        row = conn.execute(
            "UPDATE tickets SET status = %s, resolved_at = %s "
            " WHERE ticket_id = %s AND customer_id = %s "
            f"RETURNING {_COLS}",
            ("Resolved" if resolved else None,
             now if resolved else None, ticket_id, customer_id)).fetchone()
    if not row:
        return {"changed": False,
                "reason": "no ticket with that id belongs to this organisation"}
    return {"changed": True, "ticket": _shape(dict(row))}


_FULL = (_COLS + ", body_masked, channel, region, tier, raw_category, "
         "queue_intake, pii_counts")


def ticket(customer_id: str, region: str, ticket_id: str) -> dict:
    """One ticket in full, for the detail view.

    Scoped by organisation like everything else here, and serving only the
    masked columns. `pii_counts` is carried deliberately: the detail view
    says how many identifiers the gate removed from this particular ticket,
    which is a stronger statement than a policy page claiming it masks.
    """
    with connect(region) as conn:
        row = conn.execute(
            f"SELECT {_FULL} FROM tickets "
            " WHERE ticket_id = %s AND customer_id = %s",
            (ticket_id, customer_id)).fetchone()
    if not row:
        return {"error": "no ticket with that id belongs to this organisation"}
    r = dict(row)
    out = _shape(r)
    counts = r.get("pii_counts")
    if isinstance(counts, str):
        import json as _j
        try: counts = _j.loads(counts)
        except ValueError: counts = {}
    out.update({
        "body": r.get("body_masked") or "",
        "channel": r.get("channel") or "",
        "region": r.get("region") or region,
        "tier": r.get("tier") or "",
        "raw_category": r.get("raw_category") or "",
        "queue_intake": r.get("queue_intake") or "",
        "pii_counts": counts or {},
        # Stated rather than left blank: the export carries no satisfaction
        # field, so there is nothing to show and inventing one would be the
        # single thing this pilot refuses to do.
        "csat": None,
    })
    return {"ticket": out}


def knowledge(region: str, strategy: str = "D_heading_context") -> dict:
    """What the pilot is allowed to answer from, counted rather than claimed.

    The honest part of this view is the empty rows. Twenty-four knowledge-base
    articles exist as index entries whose bodies point at Confluence, so they
    carry no text to retrieve. Showing them with a chunk count of zero is the
    difference between "coverage is 25%" as an excuse and as a measurement:
    the ceiling is in the corpus, and the corpus is on screen.
    """
    # "Usable" is the same test retrieval itself applies: a chunk whose text
    # carries the stub marker is an index entry pointing at Confluence, not an
    # answer, and the retriever excludes it. Counting it the same way here
    # means this page cannot drift from what the pipeline will actually find.
    from .chunking import STUB_MARKER
    stub_like = f"%{STUB_MARKER}%"
    with connect(region) as conn:
        rows = conn.execute(
            "SELECT source_type, source_id, count(*) AS chunks, "
            "       min(effective_from) AS from_date, max(effective_to) AS to_date, "
            "       count(*) FILTER (WHERE text NOT LIKE %s) AS usable "
            "  FROM doc_chunks WHERE strategy = %s "
            " GROUP BY source_type, source_id "
            " ORDER BY source_type, source_id", (stub_like, strategy)).fetchall()
        stubs = conn.execute(
            "SELECT count(DISTINCT source_id) AS n FROM doc_chunks "
            " WHERE strategy = %s AND source_type = 'kb' AND text LIKE %s",
            (strategy, stub_like)).fetchone()
        rules = conn.execute(
            "SELECT rule_id, name, new_value, effective_from "
            "  FROM statutory_rules ORDER BY effective_from").fetchall()

    docs = [{"type": r["source_type"], "id": r["source_id"],
             "chunks": int(r["chunks"]), "usable": int(r["usable"] or 0),
             "from": str(r["from_date"] or ""), "to": str(r["to_date"] or "")}
            for r in rows]
    return {
        "documents": docs,
        "indexed_chunks": sum(d["chunks"] for d in docs),
        "empty_kb_articles": int(stubs["n"]) if stubs else 0,
        "rules": [{"id": r["rule_id"], "name": r["name"],
                   "value": str(r["new_value"]),
                   "from": str(r["effective_from"])} for r in rules],
        "strategy": strategy,
    }
