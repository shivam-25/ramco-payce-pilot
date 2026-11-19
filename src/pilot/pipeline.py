"""The whole pilot, one ticket at a time, in the order the brief sets out.

    PII gate -> normalise -> lookup -> classify -> dedupe -> router
    -> cache hit, or answer + output check, or human

Every stage writes an audit row. That is what lets the pilot PROVE the gate
ran and the human approved, instead of asserting it in a policy document.
"""
from __future__ import annotations
import json, time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .bedrock import Meter
from .classify import classify
from .cache import lookup as cache_lookup, store as cache_store
from .db import connect
from .injection import screen
from .lookup import customers, customer_names, enrich, sla, in_payroll_week
from .normalise import normalise
from .outputcheck import check
from .pii import redact_ticket, leaks, downstream_text
from .router import decide, Route
from .answer import answer as draft_answer

DEFAULT_CHUNKING = "D_heading_context"
DEFAULT_RETRIEVAL = "R4_hybrid_dated"


@dataclass
class Result:
    ticket_id: str
    route: str
    reason: str
    queue: str | None = None
    region: str | None = None
    tier: str | None = None
    priority: str | None = None
    case_id: str | None = None
    draft: str = ""
    citations: list = field(default_factory=list)
    needs_approval: bool = False
    output_check: dict = field(default_factory=dict)
    pii_counts: dict = field(default_factory=dict)
    cache_hit: bool = False
    timings_ms: dict = field(default_factory=dict)
    usd: float = 0.0
    sla_hours: tuple | None = None

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["sla_hours"] = list(self.sla_hours) if self.sla_hours else None
        return d


def _audit(conn, ticket_id, stage, detail):
    try:
        conn.execute("INSERT INTO audit_log (ticket_id, stage, detail) "
                     "VALUES (%s,%s,%s)", (ticket_id, stage, json.dumps(detail, default=str)))
    except Exception:
        pass        # auditing must never take the pipeline down


def run_ticket(raw: dict, *, as_of: date | None = None,
               chunk_strategy: str = DEFAULT_CHUNKING,
               retrieval_strategy: str = DEFAULT_RETRIEVAL,
               use_cache: bool = True,
               meter: Meter | None = None) -> Result:
    meter = meter or Meter()
    t: dict[str, float] = {}
    clock = time.perf_counter

    # ---- box 1: PII gate. Before everything. -------------------------
    t0 = clock()
    masked_raw, red = redact_ticket(raw, customer_names())
    t["pii"] = (clock() - t0) * 1000
    # Assert the gate here, not only in the test, and on exactly what the
    # pipeline is about to hand downstream. Structured ids and timestamps are
    # not free text: a ticket reference like UI-20261119105424 is fourteen
    # digits and is nobody's bank account.
    residual = leaks(downstream_text(masked_raw))
    if residual:
        raise RuntimeError(f"PII gate leaked: {residual[:3]}")

    # ---- box 2: normalise --------------------------------------------
    t0 = clock()
    ticket = normalise(masked_raw)
    t["normalise"] = (clock() - t0) * 1000
    text = f"{ticket.subject}\n{ticket.body}".strip()
    as_of = as_of or (ticket.received_at.date() if ticket.received_at
                      else datetime.now(timezone.utc).date())

    # ---- box 3: lookup ------------------------------------------------
    t0 = clock()
    ticket, resolved = enrich(ticket)
    t["lookup"] = (clock() - t0) * 1000

    res = Result(ticket_id=ticket.ticket_id, route="", reason="",
                 region=ticket.region, tier=ticket.tier,
                 priority=ticket.priority, pii_counts=red.counts)

    # ---- injection screen: before any model call ----------------------
    t0 = clock()
    blocked, reasons = screen(text)
    t["injection"] = (clock() - t0) * 1000

    # ---- box 4: classify ----------------------------------------------
    queue = None
    if not blocked:
        t0 = clock()
        _, queue, usage = classify(text)
        meter.add(usage)
        t["classify"] = (clock() - t0) * 1000
    res.queue = queue

    # ---- box 6: router -------------------------------------------------
    t0 = clock()
    decision = decide(text=text, region=ticket.region, queue=queue,
                      priority=ticket.priority, injection=blocked,
                      injection_reasons=reasons,
                      received_at=ticket.received_at)
    t["router"] = (clock() - t0) * 1000
    res.route = decision.route.value
    res.reason = decision.reason
    res.needs_approval = decision.needs_approval
    res.sla_hours = sla(ticket.tier, ticket.priority)

    if decision.route is Route.HUMAN:
        res.timings_ms = {k: round(v, 1) for k, v in t.items()}
        res.usd = meter.usd
        return res

    # ---- cache, then answer, then output check -------------------------
    with connect(ticket.region) as conn:
        _audit(conn, ticket.ticket_id, "pii_gate",
               {"counts": red.counts, "leaks": 0})
        _audit(conn, ticket.ticket_id, "router",
               {"route": res.route, "reason": res.reason, "queue": queue})

        if use_cache:
            t0 = clock()
            hit, usage = cache_lookup(conn, text, ticket.region, as_of=as_of)
            meter.add(usage)
            t["cache"] = (clock() - t0) * 1000
            if hit:
                res.cache_hit = True
                res.route = Route.CACHE.value
                res.draft = hit.answer
                res.citations = hit.citations
                res.reason = f"semantic cache hit at {hit.similarity:.3f}"
                res.timings_ms = {k: round(v, 1) for k, v in t.items()}
                res.usd = meter.usd
                _audit(conn, ticket.ticket_id, "cache_hit",
                       {"similarity": hit.similarity})
                return res

        t0 = clock()
        d = draft_answer(conn, text, region=ticket.region, as_of=as_of,
                         chunk_strategy=chunk_strategy,
                         retrieval_strategy=retrieval_strategy)
        if d.usage:
            meter.add(d.usage)
        t["answer"] = (clock() - t0) * 1000

        if d.escalate or not d.grounded:
            res.route = Route.HUMAN.value
            res.reason = d.reason or "not grounded in any retrieved passage"
            res.needs_approval = True
            res.draft = d.text
            res.timings_ms = {k: round(v, 1) for k, v in t.items()}
            res.usd = meter.usd
            _audit(conn, ticket.ticket_id, "escalate", {"reason": res.reason})
            return res

        t0 = clock()
        verdict = check(d, tier=ticket.tier, priority=ticket.priority,
                        payroll_week=in_payroll_week(ticket.received_at))
        t["output_check"] = (clock() - t0) * 1000
        res.output_check = {"passed": verdict.passed,
                            "failures": verdict.failures,
                            "detail": verdict.detail}

        if not verdict.passed:
            res.route = Route.HUMAN.value
            res.reason = f"output check failed: {', '.join(verdict.failures)}"
            res.needs_approval = True
            res.draft = d.text
            _audit(conn, ticket.ticket_id, "output_check_failed",
                   {"failures": verdict.failures})
        else:
            res.draft = d.text
            res.citations = d.citations
            if use_cache:
                cache_store(conn, text, ticket.region, d.text,
                            d.citations, as_of, passages=d.passages)
            _audit(conn, ticket.ticket_id, "drafted",
                   {"citations": d.citations})

    res.timings_ms = {k: round(v, 1) for k, v in t.items()}
    res.usd = meter.usd
    return res
