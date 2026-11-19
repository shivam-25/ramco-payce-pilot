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
import uuid

import boto3

RUNTIME_ARN = os.environ["RUNTIME_ARN"]
API_KEY = os.environ["API_KEY"]
QUALIFIER = os.environ.get("QUALIFIER", "DEFAULT")

_client = boto3.client("bedrock-agentcore")


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
    allowed = {"subject", "body", "customer_id", "as_of", "use_cache"}
    clean = {k: v for k, v in payload.items() if k in allowed}

    try:
        response = _client.invoke_agent_runtime(
            agentRuntimeArn=RUNTIME_ARN,
            qualifier=QUALIFIER,
            runtimeSessionId=str(uuid.uuid4()),
            contentType="application/json",
            payload=json.dumps(clean).encode(),
        )
        return _reply(200, json.loads(response["response"].read()))
    except Exception as exc:                      # noqa: BLE001
        return _reply(502, {"error": f"{type(exc).__name__}: {exc}"[:400]})
