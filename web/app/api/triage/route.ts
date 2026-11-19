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

  const body = await req.json();
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
