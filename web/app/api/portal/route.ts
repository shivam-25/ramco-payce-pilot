import { NextRequest, NextResponse } from "next/server";
import { COOKIE, open, authenticate, seal } from "../../lib/session";

export const runtime = "nodejs";
export const maxDuration = 60;

/** The organisation id is taken from the signed cookie and never from the
 *  request body. That is the whole access-control story: a caller can ask for
 *  any ticket id it likes, and the SQL still has its own organisation in the
 *  WHERE, so somebody else's ticket matches no row. */
async function callRuntime(portal: Record<string, unknown>) {
  const url = process.env.PILOT_API_URL;
  const key = process.env.PILOT_API_KEY;
  if (!url || !key) throw new Error("pilot endpoint is not configured");
  const r = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", "x-pilot-key": key },
    body: JSON.stringify({ portal }),
    signal: AbortSignal.timeout(55_000),
  });
  const text = await r.text();
  if (!r.ok) throw new Error(`pilot returned ${r.status}: ${text.slice(0, 200)}`);
  return text;
}

export async function POST(req: NextRequest) {
  const sent = await req.json().catch(() => ({}));
  const action = String(sent.action ?? "");

  if (action === "login") {
    const s = authenticate(String(sent.user ?? ""), String(sent.password ?? ""));
    if (!s) return NextResponse.json({ error: "Wrong username or password." },
                                     { status: 401 });
    const res = NextResponse.json({ session: s });
    res.cookies.set(COOKIE, seal(s), {
      httpOnly: true, sameSite: "lax", secure: true, path: "/",
      maxAge: 60 * 60 * 8,
    });
    return res;
  }

  if (action === "logout") {
    const res = NextResponse.json({ ok: true });
    res.cookies.set(COOKIE, "", { httpOnly: true, path: "/", maxAge: 0 });
    return res;
  }

  const s = open(req.cookies.get(COOKIE)?.value);
  if (!s) return NextResponse.json({ error: "Sign in first." }, { status: 401 });

  try {
    if (action === "resolve" || action === "reopen") {
      const text = await callRuntime({
        op: "resolve", customer_id: s.customer_id, region: s.region,
        ticket_id: String(sent.ticket_id ?? "").slice(0, 64),
        resolved: action === "resolve",
      });
      return new NextResponse(text, { status: 200,
        headers: { "content-type": "application/json" } });
    }

    const state = ["open", "resolved", "all"].includes(String(sent.state))
      ? String(sent.state) : "all";
    const text = await callRuntime({
      op: "tickets", customer_id: s.customer_id, region: s.region,
      state, limit: 50, offset: Math.max(0, Number(sent.offset) || 0),
    });
    const body = JSON.parse(text);
    return NextResponse.json({ ...body, session: s });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "upstream failed" },
      { status: 502 });
  }
}

export async function GET(req: NextRequest) {
  const s = open(req.cookies.get(COOKIE)?.value);
  return NextResponse.json({ session: s });
}
