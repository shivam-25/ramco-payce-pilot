import { NextResponse } from "next/server";
import { createHmac } from "node:crypto";

// The console streams over a WebSocket, and the Lambda on the other end has
// to know the caller is allowed. It cannot be the shared secret: this page is
// public, so anything the browser holds is public too, and that key can run
// the account's models.
//
// So the browser gets a token that says only "someone holding the secret
// vouched for this, until T". It is useless after sixty seconds and it
// cannot be turned back into the secret.
export const runtime = "nodejs";

export async function GET() {
  const url = process.env.PILOT_WS_URL;
  const key = process.env.PILOT_API_KEY;
  if (!url || !key) {
    return NextResponse.json({ error: "streaming is not configured" },
                             { status: 503 });
  }
  const exp = String(Math.floor(Date.now() / 1000) + 60);
  const sig = createHmac("sha256", key).update(exp).digest("hex");
  return NextResponse.json({ url, token: `${exp}.${sig}` },
                           { headers: { "cache-control": "no-store" } });
}
