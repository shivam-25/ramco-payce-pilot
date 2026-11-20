"""The pilot as an HTTP service.

AgentCore Runtime requires /invocations (POST) and /ping (GET) on port 8080.
The same app runs locally with uvicorn, so what is tested locally is what is
deployed — there is no second code path for the cloud.
"""
from __future__ import annotations
import os
import re
from datetime import date, datetime

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .bedrock import Meter
from .pipeline import run_ticket, DEFAULT_CHUNKING, DEFAULT_RETRIEVAL

app = FastAPI(title="Ramco Payce support pilot", version="1.0")


class Invocation(BaseModel):
    # A raw ticket in any of the three export schemas, or the short form the
    # UI sends. The PII gate runs on whatever arrives.
    ticket: dict | None = None
    subject: str | None = None
    body: str | None = None
    customer_id: str | None = None
    as_of: str | None = None
    use_cache: bool = True

    # Retrieval sweep, used by the bake-off. The databases are private, so
    # the runtime inside the VPC is the only place this can run. It is not
    # reachable from the internet: the proxy Lambda in front of the public
    # endpoint filters the payload down to the five fields above, so this
    # one is dropped before it ever gets here. Reaching it requires AWS
    # credentials and a direct invoke-agent-runtime call.
    eval_retrieval: list[dict] | None = None
    eval_strategies: list[str] | None = None

    # A one-shot corpus migration, reachable only the same way the sweep is.
    admin: str | None = None

    # Draft the golden set and hand back what a judge needs. Same reachability
    # as the sweep: the databases are private, so drafting against the real
    # corpus can only happen in here, while scoring the drafts needs no
    # database and stays outside.
    eval_answer: list[dict] | None = None

    # Resolved engineering escalations to index as a "known issue" source.
    # Sent as a payload rather than read from disk because the deployment
    # artifact deliberately carries no case data, and reachable only by a
    # direct invoke with AWS credentials -- the public proxy drops this field
    # along with every other one it does not whitelist.
    ingest_known_issues: list[dict] | None = None
    eval_draft_model: str | None = None


@app.get("/ping")
def ping() -> dict:
    return {"status": "healthy", "service": "ramco-payce-pilot"}


SUPERSEDED = re.compile(r"\s*This replaced:[^.]*\.")


def _strip_superseded() -> dict:
    """Remove the superseded value from in-force statutory chunks.

    A chunk that carries both the value in force and the one it replaced is
    a trap: a question phrased around the earlier period draws the model to
    the older figure, which it then cites to a passage that genuinely is in
    force. The date filter, the citation check and the grounding check all
    pass, and the answer is wrong anyway. The old value is not lost -- the
    prior period is its own row with its own window.

    Re-embedding is the point: the text is what was embedded, so editing it
    without re-embedding would leave retrieval matching the sentence that is
    no longer there. tsv regenerates itself. The answer cache is emptied
    because its entries were drafted from the old text -- a corpus change
    that leaves the cache in place just serves the old answer instead."""
    from .db import connect
    from .bedrock import embed
    from .config import REGIONS
    from contextlib import ExitStack

    done = {}
    with ExitStack() as stack:
        for region in REGIONS:
            conn = stack.enter_context(connect(region))
            rows = conn.execute(
                "SELECT chunk_id, text FROM doc_chunks "
                " WHERE source_type = 'statutory' AND text LIKE %s",
                ("%This replaced:%",)).fetchall()
            n = 0
            for row in rows:
                cid = row["chunk_id"] if isinstance(row, dict) else row[0]
                old = row["text"] if isinstance(row, dict) else row[1]
                new = SUPERSEDED.sub("", old)
                if new == old:
                    continue
                vector, _ = embed(new)
                conn.execute("UPDATE doc_chunks SET text = %s, embedding = %s "
                             " WHERE chunk_id = %s", (new, vector, cid))
                n += 1
            cleared = conn.execute("DELETE FROM answer_cache").rowcount
            done[region] = {"chunks_rewritten": n, "cache_entries_cleared": cleared}
    return {"admin": "strip_superseded", "regions": done}


def _ingest_known_issues(issues: list[dict]) -> dict:
    """Index resolved escalations under every chunking strategy, in both
    regions, replacing whatever was there before.

    Replacing rather than appending keeps this re-runnable: the tracker is a
    live system, so this will be run again, and an append would silently
    double every issue. The delete is scoped to source_type so the statutory
    and process corpus is untouched -- those took a full re-embed to build
    and nothing here should put them at risk.
    """
    from .db import connect
    from .chunking import known_issue_chunks, STRATEGIES
    from .bedrock import embed_many
    from .config import REGIONS

    done = {}
    for region in REGIONS:
        with connect(region) as conn:
            conn.execute("DELETE FROM doc_chunks WHERE source_type = %s",
                         ("known_issue",))
            n = 0
            for strategy in STRATEGIES:
                chunks = known_issue_chunks(strategy, issues)
                if not chunks:
                    continue
                vectors, _ = embed_many([c.text for c in chunks])
                conn.cursor().executemany(
                    "INSERT INTO doc_chunks (strategy,source_type,source_id,"
                    "heading,text,region,effective_from,effective_to,"
                    "token_estimate,embedding) VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(c.strategy, c.source_type, c.source_id, c.heading,
                      c.text, c.region, c.effective_from, c.effective_to,
                      c.tokens, v) for c, v in zip(chunks, vectors)])
                n += len(chunks)
            # A new source can answer questions the cache already has a
            # withheld or escalated answer for, so the cache has to go.
            conn.execute("DELETE FROM answer_cache")
            total = conn.execute(
                "SELECT count(*) AS n FROM doc_chunks WHERE source_type=%s",
                ("known_issue",)).fetchone()["n"]
            done[region] = {"inserted": n, "known_issue_chunks": total}
    return {"admin": "ingest_known_issues", "issues": len(issues),
            "regions": done}


def _answer_set(questions: list[dict], draft_model: str | None) -> dict:
    """Retrieve and draft each question, returning what Ragas needs. The
    model is overridable per request so two drafters can be compared without
    a redeploy -- the comparison is the point, and redeploying between arms
    would leave the corpus and the quota in a different state for each."""
    from .db import connect
    from .answer import answer
    from . import answer as answer_mod
    from .outputcheck import check as output_check
    from .config import TOP_K, REGIONS
    from .pipeline import DEFAULT_CHUNKING, DEFAULT_RETRIEVAL
    from contextlib import ExitStack
    from datetime import date as _date

    was = answer_mod.DRAFT_MODEL
    if draft_model:
        answer_mod.DRAFT_MODEL = draft_model
    out = []
    try:
        with ExitStack() as stack:
            conns = {r: stack.enter_context(connect(r)) for r in REGIONS}
            for q in questions:
                region = q.get("region") or "IN"
                as_of = (_date.fromisoformat(q["as_of"])
                         if q.get("as_of") else None)
                d = answer(conns[region], q["question"], region=region,
                           as_of=as_of, chunk_strategy=DEFAULT_CHUNKING,
                           retrieval_strategy=DEFAULT_RETRIEVAL, k=TOP_K)
                chk = output_check(d, tier="Enterprise", priority="P3",
                                   payroll_week=False)
                out.append({
                    "id": q.get("id"),
                    "question": q["question"],
                    "answer": d.text,
                    "contexts": [h.text for h in d.passages],
                    "citations": d.citations,
                    "escalated": d.escalate or not d.grounded,
                    "check_passed": chk.passed,
                    "check_failures": list(chk.failures),
                    "usd": d.usage.usd if d.usage else 0.0,
                    "seconds": d.usage.seconds if d.usage else 0.0,
                })
    finally:
        answer_mod.DRAFT_MODEL = was
    return {"rows": out, "draft_model": draft_model or was}


def _retrieval_sweep(questions: list[dict], strategies: list[str]) -> dict:
    """Retrieve only — no drafting, no model judgement. Returns what came
    back so the caller can score it with set arithmetic."""
    from .db import connect
    from .bedrock import embed
    from .retrieval import retrieve
    from .config import TOP_K, REGIONS
    from contextlib import ExitStack

    out = []
    with ExitStack() as stack:
        conns = {r: stack.enter_context(connect(r)) for r in REGIONS}
        for q in questions:
            region = q.get("region") or "IN"
            as_of = None
            if q.get("as_of"):
                y, m, d = q["as_of"].split("-")
                as_of = date(int(y), int(m), int(d))
            vector, _ = embed(q["question"])
            for groundable in (False, True):
                for rs in strategies:
                    hits, _ = retrieve(conns[region], DEFAULT_CHUNKING, rs,
                                       q["question"], k=TOP_K, region=region,
                                       as_of=as_of, groundable=groundable,
                                       vector=vector)
                    out.append({
                        "id": q.get("id"), "kind": q.get("kind"),
                        "groundable": groundable, "retrieval": rs,
                        "hits": [{"source_id": h.source_id, "sim": round(h.sim, 4),
                                  "effective_from": str(h.effective_from) if h.effective_from else None,
                                  "effective_to": str(h.effective_to) if h.effective_to else None}
                                 for h in hits],
                    })
    return {"sweep": out, "n": len(out)}


@app.post("/invocations")
def invocations(payload: Invocation) -> dict:
    if payload.admin == "strip_superseded":
        return _strip_superseded()

    if payload.ingest_known_issues is not None:
        return _ingest_known_issues(payload.ingest_known_issues)

    if payload.eval_answer is not None:
        return _answer_set(payload.eval_answer, payload.eval_draft_model)

    if payload.eval_retrieval is not None:
        return _retrieval_sweep(
            payload.eval_retrieval,
            payload.eval_strategies or ["R1_dense", "R2_lexical",
                                        "R3_hybrid", "R4_hybrid_dated"])

    raw = payload.ticket or {
        "ticket_id": f"UI-{datetime.utcnow():%Y%m%d%H%M%S}",
        "channel": "email",
        "customer_id": payload.customer_id,
        "subject": payload.subject or "",
        "body": payload.body or "",
        "category": "",
        "priority": "P3",
        "status": "open",
        "received_at": datetime.utcnow().isoformat(),
        "from_email": "",
        "assigned_group": "",
        "first_response_at": None,
        "resolved_at": None,
        "events": [],
    }
    as_of = None
    if payload.as_of:
        y, m, d = payload.as_of.split("-")
        as_of = date(int(y), int(m), int(d))

    meter = Meter()
    result = run_ticket(raw, as_of=as_of, use_cache=payload.use_cache,
                        chunk_strategy=os.environ.get("CHUNKING", DEFAULT_CHUNKING),
                        retrieval_strategy=os.environ.get("RETRIEVAL", DEFAULT_RETRIEVAL),
                        meter=meter)
    out = result.to_dict()
    out["cost"] = {"usd": round(meter.usd, 6), "by_model": meter.by_model()}
    return out
