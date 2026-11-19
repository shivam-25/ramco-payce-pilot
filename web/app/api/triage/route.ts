import { NextRequest, NextResponse } from "next/server";

// The browser never talks to AWS. This server route is the only thing that
// knows the function URL and the shared secret, and neither is a credential
// that can do anything else in the account.
export const runtime = "nodejs";
export const maxDuration = 60;

export async function POST(req: NextRequest) {
  const url = process.env.PILOT_API_URL;
  const key = process.env.PILOT_API_KEY;
  if (!url || !key) {
    return NextResponse.json(
      { error: "PILOT_API_URL and PILOT_API_KEY are not configured" },
      { status: 500 },
    );
  }

  // Only the fields the console sends are passed on. Forwarding the whole
  // body would let anyone with the page's URL post eval_retrieval and run a
  // strategy sweep on the account's budget -- the page is public, and the
  // key that reaches AWS lives on this side of it.
  const sent = await req.json();
  const body = {
    subject: String(sent.subject ?? "").slice(0, 400),
    body: String(sent.body ?? "").slice(0, 4000),
    customer_id: String(sent.customer_id ?? "").slice(0, 40),
    as_of: typeof sent.as_of === "string" && /^\d{4}-\d{2}-\d{2}$/.test(sent.as_of)
      ? sent.as_of : null,
    use_cache: sent.use_cache !== false,
  };
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json", "x-pilot-key": key },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(55_000),
    });
    const text = await r.text();
    if (!r.ok) {
      return NextResponse.json(
        { error: `pilot returned ${r.status}: ${text.slice(0, 300)}` },
        { status: 502 },
      );
    }
    return new NextResponse(text, {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "upstream failed" },
      { status: 504 },
    );
  }
}
