// Lambda behind a Function URL in RESPONSE_STREAM mode: the streaming twin of
// infra/proxy/handler.py.
//
// Why a second function, and why JavaScript. Lambda response streaming is
// only available on the Node.js managed runtimes -- a Python Lambda cannot
// flush a byte before its handler returns, so the existing proxy physically
// cannot stream however it is written. Rather than port the working path to
// Node and risk it, this sits beside it: the console tries this first and
// falls back to the buffered endpoint if anything here fails, so the demo
// cannot be taken down by this file.
//
// It holds the same shared secret and forwards the same five fields. The IAM
// permission to invoke the runtime lives here, never on Vercel.
import { BedrockAgentCoreClient, InvokeAgentRuntimeCommand }
  from "@aws-sdk/client-bedrock-agentcore";
import { randomUUID, timingSafeEqual } from "node:crypto";

const RUNTIME_ARN = process.env.RUNTIME_ARN;
const API_KEY = process.env.API_KEY;
const QUALIFIER = process.env.QUALIFIER || "DEFAULT";
const client = new BedrockAgentCoreClient({});

// Same two clocks as the buffered proxy: stay inside AgentCore's 15-minute
// idle reclaim, and bound how long one microVM serves traffic.
const MAX_IDLE_MS = 10 * 60 * 1000;
const MAX_AGE_MS = 60 * 60 * 1000;
let sessionId = null, born = 0, used = 0;

function session() {
  const now = Date.now();
  if (!sessionId || now - used > MAX_IDLE_MS || now - born > MAX_AGE_MS) {
    sessionId = randomUUID();          // 36 chars; AgentCore wants >= 33
    born = now;
  }
  used = now;
  return sessionId;
}

// Constant-time, and length-safe: timingSafeEqual throws on a length
// mismatch, which would otherwise be a way to probe the key's length.
function keyOk(given) {
  const a = Buffer.from(given || "", "utf8");
  const b = Buffer.from(API_KEY || "", "utf8");
  return a.length === b.length && timingSafeEqual(a, b);
}

export const handler = awslambda.streamifyResponse(
  async (event, responseStream, _context) => {
    const headers = Object.fromEntries(
      Object.entries(event.headers || {}).map(([k, v]) => [k.toLowerCase(), v]));

    const send = (status, obj) => {
      const s = awslambda.HttpResponseStream.from(responseStream, {
        statusCode: status,
        headers: { "content-type": "application/json" },
      });
      s.write(JSON.stringify(obj));
      s.end();
    };

    if (!keyOk(headers["x-pilot-key"])) return send(401, { error: "unauthorized" });

    let payload;
    try {
      payload = JSON.parse(event.body || "{}");
    } catch {
      return send(400, { error: "body must be JSON" });
    }

    // The same allow-list as the buffered proxy, plus the flag that asks the
    // runtime for events. A caller cannot reach through and hand the runtime
    // an arbitrary document, or start an eval sweep on the account's budget.
    const allowed = ["subject", "body", "customer_id", "as_of", "use_cache"];
    const clean = { stream: true };
    for (const k of allowed) if (k in payload) clean[k] = payload[k];

    const out = awslambda.HttpResponseStream.from(responseStream, {
      statusCode: 200,
      headers: {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        "x-accel-buffering": "no",
      },
    });

    try {
      const r = await client.send(new InvokeAgentRuntimeCommand({
        agentRuntimeArn: RUNTIME_ARN,
        qualifier: QUALIFIER,
        runtimeSessionId: session(),
        contentType: "application/json",
        accept: "text/event-stream",
        payload: new TextEncoder().encode(JSON.stringify(clean)),
      }));
      // Pass the runtime's bytes through untouched. Re-framing them here
      // would mean two places that have to agree on the event format.
      for await (const chunk of r.response) out.write(chunk);
      out.end();
    } catch (e) {
      sessionId = null;               // a wedged session must not be reused
      out.write(`data: ${JSON.stringify({
        event: "error", error: `${e.name}: ${e.message}`.slice(0, 300),
      })}\n\n`);
      out.end();
    }
  });
