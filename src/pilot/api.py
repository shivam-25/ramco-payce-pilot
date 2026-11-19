"""The pilot as an HTTP service.

AgentCore Runtime requires /invocations (POST) and /ping (GET) on port 8080.
The same app runs locally with uvicorn, so what is tested locally is what is
deployed — there is no second code path for the cloud.
"""
from __future__ import annotations
import os
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


@app.get("/ping")
def ping() -> dict:
    return {"status": "healthy", "service": "ramco-payce-pilot"}


@app.post("/invocations")
def invocations(payload: Invocation) -> dict:
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
