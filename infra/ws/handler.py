"""The streaming transport, over an API Gateway WebSocket.

Why a WebSocket and not Server-Sent Events over HTTPS. The runtime streams
(it returns text/event-stream and AgentCore passes the bytes through
progressively -- measured), but nothing in front of it in this account will
carry a stream to the public internet:

  * API Gateway HTTP APIs buffer the whole response before returning it.
  * Lambda response streaming needs a Function URL, and this account denies
    anonymous Function URL invocation -- a correct resource policy still gets
    "Forbidden", which is an organisation guardrail, the same class as the
    SCP that denies every region but ap-southeast-2.
  * CloudFront with Origin Access Control reaches the Function URL with IAM,
    but OAC does not hash a POST body, so every request fails SigV4. The
    documented fix is Lambda@Edge in us-east-1, which the region SCP denies.
  * Signing from Vercel would mean AWS credentials on Vercel, which is the
    one thing this design will not do.

A WebSocket API has none of those problems: it is public without IAM, and
the server pushes frames whenever it likes. So the browser opens a socket,
sends one ticket, and this function forwards each pipeline event as it
arrives off the runtime's stream.
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

_agent = boto3.client("bedrock-agentcore")

# Same two clocks as the buffered proxy: stay inside AgentCore's 15-minute
# idle reclaim, and bound how long one microVM serves traffic.
SESSION_MAX_IDLE_S = 10 * 60
SESSION_MAX_AGE_S = 60 * 60
_session_id: str | None = None
_session_born = 0.0
_session_used = 0.0


def _session() -> str:
    global _session_id, _session_born, _session_used
    now = time.monotonic()
    if (_session_id is None
            or now - _session_used > SESSION_MAX_IDLE_S
            or now - _session_born > SESSION_MAX_AGE_S):
        _session_id = str(uuid.uuid4())     # 36 chars; AgentCore wants >= 33
        _session_born = now
    _session_used = now
    return _session_id


def _authorised(given: str) -> bool:
    """Either the shared secret itself, or a short-lived token signed with it.

    The browser cannot hold the shared secret -- the page is public, and a
    key visible in devtools is a key anyone can run the account's models
    with. So Vercel mints a token that says only "someone who knows the
    secret vouched for this, until T", and this verifies it. A leaked token
    is worth at most the seconds left on its clock.

    The raw secret is still accepted because the deploy scripts and the CLI
    tests talk to this directly, and they hold it legitimately.
    """
    if hmac.compare_digest(given, API_KEY):
        return True
    try:
        exp_s, sig = given.split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if exp < time.time():
        return False
    want = hmac.new(API_KEY.encode(), exp_s.encode(), "sha256").hexdigest()
    return hmac.compare_digest(sig, want)


def handler(event, _context):
    ctx = event.get("requestContext") or {}
    route = ctx.get("routeKey")
    if route in ("$connect", "$disconnect"):
        return {"statusCode": 200}

    conn = ctx.get("connectionId")
    api = boto3.client(
        "apigatewaymanagementapi",
        endpoint_url=f"https://{ctx['domainName']}/{ctx['stage']}")

    # One socket carries every ticket on the page, so a frame that does not
    # say which ticket it belongs to is ambiguous the moment two run at
    # once. "Draft all replies" runs four, and without this the browser
    # delivered whichever reply arrived next to whichever row was waiting:
    # a money ticket showed another ticket's drafted answer, and the
    # not-in-corpus one showed the injection refusal. Both wrong, both
    # plausible-looking, which is the dangerous kind.
    #
    # The id is the caller's and is echoed, never interpreted.
    rid = None

    def push(obj) -> bool:
        """True while the browser is still there. A viewer who closes the tab
        mid-run is normal, not an error, and must not look like one."""
        try:
            if rid is not None:
                obj = {**obj, "rid": rid}
            api.post_to_connection(ConnectionId=conn,
                                   Data=json.dumps(obj).encode())
            return True
        except api.exceptions.GoneException:
            return False

    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        push({"event": "error", "error": "body must be JSON"})
        return {"statusCode": 400}

    if not _authorised(str(payload.get("key", ""))):
        push({"event": "error", "error": "unauthorized"})
        return {"statusCode": 401}

    # The same allow-list the buffered proxy uses. A caller cannot reach
    # through this socket and start an eval sweep on the account's budget.
    allowed = {"subject", "body", "customer_id", "as_of", "use_cache",
               "priority"}
    clean = {k: v for k, v in payload.items() if k in allowed}
    # Read after auth so an unauthenticated caller cannot have its id
    # reflected, and bounded so it cannot be used to pad frames.
    got = payload.get("rid")
    if isinstance(got, str) and got:
        rid = got[:64]
    clean["stream"] = True

    try:
        r = _agent.invoke_agent_runtime(
            agentRuntimeArn=RUNTIME_ARN,
            qualifier=QUALIFIER,
            runtimeSessionId=_session(),
            contentType="application/json",
            accept="text/event-stream",
            payload=json.dumps(clean).encode(),
        )
        # Split lines by hand over small chunks rather than using
        # iter_lines(). iter_lines reads a full chunk_size block (1024 bytes
        # by default) before it yields anything, so the early events -- the
        # PII gate, normalise, lookup -- sat in a buffer waiting for enough
        # later output to fill it, and the whole run arrived at once. That
        # looks exactly like no streaming at all. 64 bytes is small enough
        # that a single event leaves immediately.
        buf = b""
        stop = False
        for chunk in r["response"].iter_chunks(64):
            buf += chunk
            while b"\n" in buf and not stop:
                line, buf = buf.split(b"\n", 1)
                text = line.decode(errors="replace").strip()
                if not text.startswith("data: "):
                    continue
                if not push(json.loads(text[6:])):
                    stop = True
            if stop:
                break
    except Exception as exc:                      # noqa: BLE001
        global _session_id
        _session_id = None            # a wedged session must not be reused
        push({"event": "error", "error": f"{type(exc).__name__}: {exc}"[:300]})
    return {"statusCode": 200}
