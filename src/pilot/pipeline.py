"""The whole pilot, one ticket at a time, in the order the brief sets out.

    PII gate -> normalise -> lookup -> classify -> dedupe -> router
    -> cache hit, or answer + output check, or human

Every stage writes an audit row. That is what lets the pilot PROVE the gate
ran and the human approved, instead of asserting it in a policy document.
"""
from __future__ import annotations
import json, threading, time
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone

from .bedrock import Meter, embed, limiter_enabled
from .classify import classify
from .cache import lookup as cache_lookup, store as cache_store
from .db import connect
from .dedupe import case_id_for, record_and_count, INCIDENT_AT
from .injection import screen
from . import guardrail as gr
from .lookup import customers, customer_names, enrich, sla, in_payroll_week
from .normalise import normalise
from .outputcheck import check
from .pii import (redact_ticket, leaks, downstream_text,
                  TEXT_FIELDS as PII_TEXT_FIELDS)
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
    case_size: int = 0
    incident: bool = False
    draft: str = ""
    citations: list = field(default_factory=list)
    # The cited passages themselves. The brief's target is 100% of replies
    # carrying a *verifiable* citation, and a source id alone is not
    # verifiable — the reader cannot see what it said or, which caused the
    # incident this pilot exists to prevent, when it was true. Each entry
    # carries the window it was in force so the reader can check the answer
    # against the date they asked about.
    sources: list = field(default_factory=list)
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


def _warm_embed(text: str, meter: Meter) -> None:
    """Fill the embedding memo for this question, off the critical path.

    The usage is credited to the meter here, in the thread, rather than
    returned. It has to be: G7 prices every gate run from the token counts
    Bedrock actually reported, and an embedding computed in a thread whose
    result nobody collected would be a real call with no cost against it. The
    memo returns a zero-token Usage on a hit, so the later callers that find
    the vector already there add nothing and the total stays exact.

    `Meter.add` only appends to a list, which is atomic, so no lock is needed.

    A failure here is deliberately silent: this is a warm-up, and whoever
    actually needs the vector calls `embed` again and gets the real error.
    """
    try:
        _, usage = embed(text)
        meter.add(usage)
    except Exception:
        pass


def _audit(conn, ticket_id, stage, detail):
    try:
        conn.execute("INSERT INTO audit_log (ticket_id, stage, detail) "
                     "VALUES (%s,%s,%s)", (ticket_id, stage, json.dumps(detail, default=str)))
    except Exception:
        pass        # auditing must never take the pipeline down


def _pii_note(counts: dict) -> str:
    """What the gate removed, in the words the console shows an agent."""
    if not counts:
        return "nothing to remove"
    nice = {"AADHAAR": "Aadhaar", "PAN": "PAN", "IBAN": "bank account",
            "EMAIL": "email", "PHONE": "phone", "EMIRATES_ID": "Emirates ID"}
    bits = [f"{v} {nice.get(k, k.lower())}" for k, v in sorted(counts.items())]
    return ", ".join(bits) + " hidden"


def run_ticket(raw: dict, *, as_of: date | None = None,
               chunk_strategy: str = DEFAULT_CHUNKING,
               retrieval_strategy: str = DEFAULT_RETRIEVAL,
               use_cache: bool = True,
               meter: Meter | None = None,
               on_event=None) -> Result:
    """`on_event`, when given, is called as each box finishes and as the
    draft is written. It is the only difference between the streamed and the
    buffered path: there is one pipeline, and the console watching it live
    sees exactly the run that the batch evals score."""
    meter = meter or Meter()
    t: dict[str, float] = {}
    clock = time.perf_counter

    def emit(event: str, **kw) -> None:
        if on_event is not None:
            on_event({"event": event, **kw})

    # ---- box 1: PII gate. Before everything. -------------------------
    gr_failed = False          # defence 1.5 unavailable -> route to a person
    t0 = clock()
    masked_raw, red = redact_ticket(raw, customer_names())
    t["pii"] = (clock() - t0) * 1000
    emit("stage", box="pii gate", ms=round(t["pii"], 1),
         note=_pii_note(red.counts))
    # Assert the gate here, not only in the test, and on exactly what the
    # pipeline is about to hand downstream. Structured ids and timestamps are
    # not free text: a ticket reference like UI-20261119105424 is fourteen
    # digits and is nobody's bank account.
    residual = leaks(downstream_text(masked_raw))
    if residual:
        raise RuntimeError(f"PII gate leaked: {residual[:3]}")

    # Defence 1.5. The regex gate is deterministic about the identifiers it
    # knows and blind to the ones no pattern enumerates -- a person named in
    # free text who is not on the roster, a street address. Guardrails is
    # asked the same question about the text the gate just cleared, and what
    # it finds is masked in the same placeholder format.
    #
    # Fail closed: if the call errors, the ticket goes to a person. An
    # unavailable screen must not read as a clean screen.
    gv = gr.apply(downstream_text(masked_raw))
    t["guardrail_pii"] = gv.ms
    if gv.ran:
        emit("stage", box="guardrail pii", ms=round(gv.ms, 1),
             note=("error" if gv.error else
                   (", ".join(gv.pii) if gv.pii else "nothing further")))
    if gv.error:
        gr_failed = True
    elif gv.spans:
        # Re-mask field by field, so the shape of the ticket is preserved --
        # the same reason the regex gate re-redacts rather than splicing.
        for _f in PII_TEXT_FIELDS:
            if masked_raw.get(_f):
                masked_raw[_f] = gr.redact(str(masked_raw[_f]), gv.spans)
        for _e in (masked_raw.get("events") or masked_raw.get("history")
                   or masked_raw.get("activity_log") or []):
            _m = _e.get("meta") or _e.get("data") or _e.get("info") or {}
            if _m.get("note"):
                _m["note"] = gr.redact(str(_m["note"]), gv.spans)
        for kind in gv.pii:
            red.counts[kind] = red.counts.get(kind, 0) + 1

    # ---- box 2: normalise --------------------------------------------
    t0 = clock()
    ticket = normalise(masked_raw)
    t["normalise"] = (clock() - t0) * 1000
    emit("stage", box="normalise", ms=round(t["normalise"], 1),
         note=f"{ticket.channel} schema")
    text = f"{ticket.subject}\n{ticket.body}".strip()
    as_of = as_of or (ticket.received_at.date() if ticket.received_at
                      else datetime.now(timezone.utc).date())

    # ---- box 3: lookup ------------------------------------------------
    t0 = clock()
    ticket, resolved = enrich(ticket)
    t["lookup"] = (clock() - t0) * 1000
    emit("stage", box="lookup", ms=round(t["lookup"], 1),
         note=f"{ticket.region} \u00b7 {ticket.tier}" if ticket.region else "not on file")

    # The question's embedding depends on nothing the next two boxes produce,
    # and three later steps all need it: the cache lookup, retrieval, and the
    # cache store. Each calls `embed`, which memoises on the text, so
    # computing it once here makes all three free and takes its latency off
    # the critical path instead of adding it to classify's.
    #
    # It is started before the classifier and joined before the first use, so
    # the two Bedrock calls overlap rather than queue. Joining matters: `embed`
    # has no in-flight de-duplication, so an unjoined warm-up would simply be
    # a second call racing the first and would buy nothing.
    #
    # A ticket the router sends to a human never uses the vector, so on those
    # the warm-up is wasted -- 70% of the G7 peak-week sample. It is a
    # thousandth of a cent each and off the critical path either way, and the
    # alternative is not knowing the queue, which is what the classifier is
    # for. Paying it on every ticket is the cheaper side of that trade.
    #
    # Skipped when the rate limiter is on, which is to say during a sweep.
    # Two limited calls cannot overlap -- they queue in the same token bucket
    # -- so the thread would buy nothing there and would charge its sleep to
    # the wrong thread's throttle tally, which is the figure G7 subtracts.
    warm = None
    if not limiter_enabled():
        warm = threading.Thread(target=_warm_embed, args=(text, meter),
                                daemon=True)
        warm.start()

    res = Result(ticket_id=ticket.ticket_id, route="", reason="",
                 region=ticket.region, tier=ticket.tier,
                 priority=ticket.priority, pii_counts=red.counts)

    # ---- injection screen: before any model call ----------------------
    t0 = clock()
    blocked, reasons = screen(text)
    t["injection"] = (clock() - t0) * 1000
    emit("stage", box="injection", ms=round(t["injection"], 1),
         note="blocked" if blocked else "clean")

    # Defence 1.5 again, and only on what the patterns let through -- an
    # obviously hostile ticket is already refused and still costs nothing.
    # Guardrails catches the paraphrase that does not look like any pattern.
    if not blocked:
        iv = gr.apply(text)
        t["guardrail_injection"] = iv.ms
        if iv.ran:
            emit("stage", box="guardrail injection", ms=round(iv.ms, 1),
                 note=("error" if iv.error else
                       ("prompt attack" if iv.injection else "clean")))
        if iv.injection:
            blocked, reasons = True, ["guardrail_prompt_attack"]
        elif iv.error:
            gr_failed = True

    # ---- box 4: classify ----------------------------------------------
    queue = None
    if not blocked:
        t0 = clock()
        _, queue, usage = classify(text)
        meter.add(usage)
        t["classify"] = (clock() - t0) * 1000
        emit("stage", box="classify", ms=round(t["classify"], 1), note=queue or "")
    res.queue = queue

    # ---- box 6: router -------------------------------------------------
    t0 = clock()
    decision = decide(text=text, region=ticket.region, queue=queue,
                      priority=ticket.priority, injection=blocked,
                      injection_reasons=reasons,
                      received_at=ticket.received_at)
    t["router"] = (clock() - t0) * 1000
    emit("stage", box="router", ms=round(t["router"], 1), note=res.route)
    res.route = decision.route.value
    res.reason = decision.reason
    res.needs_approval = decision.needs_approval

    # Fail closed. If defence 1.5 could not be reached, this ticket has only
    # had the regex screens run on it, and those are blind to an off-roster
    # name and to a paraphrased injection. That is not a clean screen, it is
    # an absent one, so a person decides.
    if gr_failed and res.route != "human":
        res.route = "human"
        res.needs_approval = True
        res.reason = ("the managed screen for names and paraphrased injection "
                      "was unavailable, so this was not fully checked")
    res.sla_hours = sla(ticket.tier, ticket.priority)

    if decision.route is Route.HUMAN:
        # This ticket never uses the warmed vector, but the warm-up still
        # charged the meter for computing it, and reading meter.usd while that
        # thread is mid-flight makes the reported cost a coin toss. Join
        # before pricing.
        #
        # It costs very little: the warm-up started before the classifier,
        # which is the slower of the two, so by the time the router has
        # decided the embedding has almost always already landed. The timeout
        # is there so a hung Bedrock call cannot hold a human-routed ticket --
        # if it fires, the cost is under-reported by one embedding and the
        # ticket still goes to the queue.
        if warm is not None:
            warm.join(timeout=5)
        res.timings_ms = {k: round(v, 1) for k, v in t.items()}
        res.usd = meter.usd
        return res

    # ---- cache, then answer, then output check -------------------------
    with connect(ticket.region) as conn:
        # ---- box 5: dedupe ---------------------------------------------
        # This box is named in the architecture and was missing from the
        # live path: case_id was declared on the Result and never set, so
        # the deployed pilot had no dedupe and no incident signal, while
        # the batch gate G3 measured a dedupe that production never ran.
        #
        # It sits here rather than before the router because that is where
        # a connection exists. Routing does not yet consult case continuity
        # — a ticket joining an open case should follow it to whoever owns
        # it, and that is the next step, not something this does today.
        t0 = clock()
        res.case_id = case_id_for(ticket, queue=queue)
        res.case_size = record_and_count(conn, res.case_id, ticket.ticket_id)
        res.incident = res.case_size >= INCIDENT_AT
        t["dedupe"] = (clock() - t0) * 1000
        emit("stage", box="dedupe", ms=round(t["dedupe"], 1),
             note=f"{res.case_size} in case" if res.case_size else "")
        if res.incident:
            _audit(conn, ticket.ticket_id, "incident_declared",
                   {"case_id": res.case_id, "tickets": res.case_size})

        _audit(conn, ticket.ticket_id, "pii_gate",
               {"counts": red.counts, "leaks": 0})
        _audit(conn, ticket.ticket_id, "router",
               {"route": res.route, "reason": res.reason, "queue": queue})

        if warm is not None:
            warm.join(timeout=30)

        if use_cache:
            t0 = clock()
            hit, usage = cache_lookup(conn, text, ticket.region, as_of=as_of)
            meter.add(usage)
            t["cache"] = (clock() - t0) * 1000
            emit("stage", box="cache", ms=round(t["cache"], 1),
                 note="hit" if hit else "miss")
            if hit:
                res.cache_hit = True
                res.route = Route.CACHE.value
                res.draft = hit.answer
                res.citations = hit.citations
                res.sources = hit.sources or []
                res.reason = f"semantic cache hit at {hit.similarity:.3f}"
                res.timings_ms = {k: round(v, 1) for k, v in t.items()}
                res.usd = meter.usd
                _audit(conn, ticket.ticket_id, "cache_hit",
                       {"similarity": hit.similarity})
                return res

        t0 = clock()
        d = draft_answer(conn, text, region=ticket.region, as_of=as_of,
                         chunk_strategy=chunk_strategy,
                         retrieval_strategy=retrieval_strategy,
                         on_event=on_event)
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
        emit("stage", box="output check", ms=round(t["output_check"], 1),
             note="passed" if verdict.passed else ", ".join(verdict.failures))
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
            from .cache import passage_sources
            res.sources = passage_sources(d.passages, d.citations)
            if use_cache:
                cache_store(conn, text, ticket.region, d.text,
                            d.citations, as_of, passages=d.passages)
            _audit(conn, ticket.ticket_id, "drafted",
                   {"citations": d.citations})

    res.timings_ms = {k: round(v, 1) for k, v in t.items()}
    res.usd = meter.usd
    return res


def stream_ticket(raw: dict, **kw):
    """`run_ticket` as a generator of events, for a console watching live.

    The pipeline is unchanged and runs on a worker thread; this only turns
    the `on_event` callback into something a caller can iterate. Doing it
    this way rather than rewriting the pipeline as a generator keeps one
    implementation: the streamed run and the run the evals score are the
    same code, so the console cannot drift away from the measured pilot.

    The final event is always `result` (or `error`), so a consumer can stop
    on it without tracking which boxes it has seen.
    """
    import queue as _queue
    q: "_queue.Queue[dict | None]" = _queue.Queue()

    def worker() -> None:
        try:
            res = run_ticket(raw, on_event=q.put, **kw)
            q.put({"event": "result", "result": asdict(res)})
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            q.put({"event": "error", "error": str(exc)[:300]})
        finally:
            q.put(None)

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    while True:
        item = q.get()
        if item is None:
            break
        yield item
