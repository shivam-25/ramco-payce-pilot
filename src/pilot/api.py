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
