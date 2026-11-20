"""Lambda behind a Function URL. The only door between Vercel and AWS.

Why it exists. Invoking an AgentCore runtime requires SigV4, which means AWS
credentials. Putting credentials on Vercel would mean either a long-lived
access key sitting in a third-party dashboard, or this machine's 12-hour
session credentials, which expire. Neither is acceptable for something the
public internet can reach.

So Vercel holds one shared secret that can do exactly one thing: ask this
function to run one ticket through the pilot. The function holds the IAM
permission, and its execution role can invoke the runtime and nothing else.
"""
from __future__ import annotations
import hmac
import json
import os
import time
import uuid

import boto3

RUNTIME_ARN = os.environ["RUNTIME_ARN"]
API_KEY = os.environ["API_KEY"]
QUALIFIER = os.environ.get("QUALIFIER", "DEFAULT")

_client = boto3.client("bedrock-agentcore")

# ---------------------------------------------------------------------------
# Session reuse, which is where three quarters of the latency went.
#
# AgentCore routes every invoke carrying the same runtimeSessionId to the same
# microVM, and keeps that microVM warm through 15 minutes of idle time. This
# function used to mint a fresh uuid4 per request. That asks for a brand-new
# microVM every single time, so no request ever met a warm one and every
# customer paid a full cold start: Python starting, boto3 and psycopg
# importing, a Secrets Manager fetch for the database password, a fresh
# Postgres connection, and a full read of the customer table -- all before the
# first line of the pipeline ran.
#
# Measured through this endpoint at production pace, that was 4.26 s at p50
# against 1.28 s of actual pipeline work. The slow part was not the pipeline
# and not AgentCore; it was this uuid4.
#
# Reusing one session per Lambda execution environment is safe here because
# the runtime holds no per-ticket state worth isolating. What it does hold is
# shared reference data -- the customer table, which maps a customer to their
# region and therefore must be loaded before a region can be chosen, and a
# content-addressed embedding cache that maps a text to that same text's own
# vector. Both are already process-global inside a single invocation, and the
# eval harness has always run thousands of tickets through one process. Case
# data stays in Postgres, one database per region, which this does not touch.
#
# Rotation has two clocks. The idle one stays under AgentCore's 15-minute
# timeout, so this function stops addressing a session the platform has
# already reclaimed. The age one bounds how long any one microVM serves
# traffic, so a leak or a wedged connection cannot persist indefinitely.
SESSION_MAX_IDLE_S = 10 * 60
SESSION_MAX_AGE_S = 60 * 60

_session_id: str | None = None
_session_born = 0.0
_session_used = 0.0


def _session() -> str:
    """The session id to invoke with: the warm one, or a new one."""
    global _session_id, _session_born, _session_used
    now = time.monotonic()
    if (_session_id is None
            or now - _session_used > SESSION_MAX_IDLE_S
            or now - _session_born > SESSION_MAX_AGE_S):
        # 36 characters. AgentCore requires at least 33, so a bare uuid4 hex
        # (32) is rejected -- keep the dashes.
        _session_id = str(uuid.uuid4())
        _session_born = now
    _session_used = now
    return _session_id


def _drop_session() -> None:
    """Forget the warm session after a failed invoke.

    An invoke can fail because the session itself is gone or wedged, and
    retrying into the same id would fail the same way. Dropping it costs the
    next request one cold start and makes the failure self-healing.
    """
    global _session_id
    _session_id = None


def _reply(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event, _context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    # compare_digest, not ==, so the check does not leak the key by timing
    if not hmac.compare_digest(headers.get("x-pilot-key", ""), API_KEY):
        return _reply(401, {"error": "unauthorized"})

    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _reply(400, {"error": "body must be JSON"})

    # Only the fields the pilot accepts are forwarded. A caller cannot reach
    # through this function and hand the runtime an arbitrary document.
    allowed = {"subject", "body", "customer_id", "as_of", "use_cache",
               "priority"}
    clean = {k: v for k, v in payload.items() if k in allowed}

    # The portal is a nested document, so it gets its own schema rather than
    # being waved through as one opaque key. Note what this does NOT do: it
    # does not decide WHICH organisation the caller may read. That is bound
    # to a signed session by the web server, which is the only holder of the
    # key that reaches this function. Validating here keeps a malformed or
    # oversized document away from the runtime; it is not the authorisation.
    port = payload.get("portal")
    if isinstance(port, dict):
        op = str(port.get("op") or "tickets")
        if op not in {"tickets", "resolve", "ticket", "knowledge"}:
            return _reply(400, {"error": "unknown portal op"})
        cid = str(port.get("customer_id") or "")[:40]
        region = str(port.get("region") or "").upper()[:8]
        if not cid or region not in {"IN", "UAE"}:
            return _reply(400, {"error": "portal needs customer_id and region"})
        sub = {"op": op, "customer_id": cid, "region": region}
        if op == "knowledge":
            pass
        elif op == "tickets":
            state = str(port.get("state") or "all")
            sub["state"] = state if state in {"open", "resolved", "all"} else "all"
            try:
                sub["limit"] = max(1, min(int(port.get("limit") or 50), 200))
                sub["offset"] = max(0, int(port.get("offset") or 0))
            except (TypeError, ValueError):
                return _reply(400, {"error": "limit and offset must be numbers"})
        else:
            tid = str(port.get("ticket_id") or "")[:64]
            if not tid:
                return _reply(400, {"error": f"{op} needs a ticket_id"})
            sub["ticket_id"] = tid
            if op == "resolve":
                sub["resolved"] = bool(port.get("resolved", True))
        clean["portal"] = sub

    try:
        response = _client.invoke_agent_runtime(
            agentRuntimeArn=RUNTIME_ARN,
            qualifier=QUALIFIER,
            runtimeSessionId=_session(),
            contentType="application/json",
            payload=json.dumps(clean).encode(),
        )
        return _reply(200, json.loads(response["response"].read()))
    except Exception as exc:                      # noqa: BLE001
        _drop_session()
        return _reply(502, {"error": f"{type(exc).__name__}: {exc}"[:400]})
