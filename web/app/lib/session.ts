import { createHmac, timingSafeEqual, randomUUID } from "crypto";

/** Demo authentication, and it is worth being blunt about what that means.
 *
 *  This gates a demonstration over synthetic case data. It is a signed cookie
 *  carrying an organisation id, not an identity system: there is no password
 *  reset, no lockout, no rotation, no audit of who signed in. What it DOES do
 *  is the part that matters for the argument -- the organisation id reaching
 *  the database comes from a server-verified cookie, never from the browser,
 *  so a viewer cannot widen their own scope by editing a request. Swapping
 *  this for a real IdP would change the first eight lines and nothing else.
 */
export type Session = { user: string; customer_id: string; region: string;
                        role: string };

type Account = Session & { password: string };

/** Overridable with PORTAL_USERS so the deployed demo need not ship
 *  credentials in the repository. Shape: user:password:customer_id:region:role,
 *  comma separated. */
function accounts(): Account[] {
  const env = process.env.PORTAL_USERS;
  if (env) {
    return env.split(",").map((row) => {
      const [user, password, customer_id, region, role] = row.split(":");
      return { user, password, customer_id, region,
               role: role || "HR Admin" };
    }).filter((a) => a.user && a.password && a.customer_id && a.region);
  }
  const pw = process.env.PORTAL_DEMO_PASSWORD || "payce-demo";
  return [
    { user: "hradmin",  password: pw, customer_id: "C-1001", region: "IN",
      role: "HR Admin" },
    { user: "uaeadmin", password: pw, customer_id: "C-1003", region: "UAE",
      role: "HR Admin" },
    { user: "bizadmin", password: pw, customer_id: "C-1013", region: "IN",
      role: "Payroll Lead" },
  ];
}

function secret(): string {
  return process.env.PORTAL_SECRET || "ramco-pilot-demo-signing-key";
}

function sign(payload: string): string {
  return createHmac("sha256", secret()).update(payload).digest("base64url");
}

/** Constant-time compare, so a wrong password cannot be narrowed by timing.
 *  Lengths are compared first because timingSafeEqual throws when they differ. */
function same(a: string, b: string): boolean {
  const x = Buffer.from(a), y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

export function authenticate(user: string, password: string): Session | null {
  const a = accounts().find((c) => c.user === user.trim().toLowerCase());
  // Compare against a dummy when the user is unknown, so that a valid
  // username and an invalid one take the same path.
  const target = a ? a.password : randomUUID();
  if (!same(password, target) || !a) return null;
  return { user: a.user, customer_id: a.customer_id, region: a.region,
           role: a.role };
}

export function seal(s: Session): string {
  const body = Buffer.from(JSON.stringify(s)).toString("base64url");
  return `${body}.${sign(body)}`;
}

export function open(cookie: string | undefined): Session | null {
  if (!cookie) return null;
  const [body, mac] = cookie.split(".");
  if (!body || !mac || !same(mac, sign(body))) return null;
  try {
    const s = JSON.parse(Buffer.from(body, "base64url").toString()) as Session;
    return s.customer_id && s.region ? s : null;
  } catch { return null; }
}

export const COOKIE = "payce_portal";
