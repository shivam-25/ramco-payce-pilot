"use client";

import { useCallback, useEffect, useState } from "react";

type Ticket = {
  ticket_id: string; subject: string; category: string; priority: string;
  state: "open" | "resolved"; received_at: string; resolved_at: string;
  queue: string; case_id: string;
};
type Org = { customer_id: string; name: string; tier: string; region: string };
type Session = { user: string; customer_id: string; region: string; role: string };
type Counts = { open: number; resolved: number; all: number };

const TABS: { key: "open" | "resolved" | "all"; label: string }[] = [
  { key: "open", label: "Open" },
  { key: "resolved", label: "Resolved" },
  { key: "all", label: "All" },
];

function when(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(+d) ? "" : d.toLocaleDateString(undefined,
    { day: "numeric", month: "short", year: "numeric" });
}

export default function Portal() {
  const [session, setSession] = useState<Session | null>(null);
  const [org, setOrg] = useState<Org | null>(null);
  const [rows, setRows] = useState<Ticket[]>([]);
  const [counts, setCounts] = useState<Counts>({ open: 0, resolved: 0, all: 0 });
  const [tab, setTab] = useState<"open" | "resolved" | "all">("open");
  const [busy, setBusy] = useState(false);
  const [working, setWorking] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [booted, setBooted] = useState(false);

  const load = useCallback(async (state: string) => {
    setBusy(true); setErr("");
    try {
      const r = await fetch("/api/portal", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ action: "tickets", state }),
      });
      const d = await r.json();
      if (!r.ok) { if (r.status === 401) setSession(null);
                   else setErr(d.error || "Could not load tickets."); return; }
      setRows(d.tickets ?? []); setCounts(d.counts ?? counts);
      setOrg(d.organisation ?? null); setSession(d.session ?? null);
    } catch { setErr("Could not reach the service."); }
    finally { setBusy(false); }
  }, [counts]);

  useEffect(() => {
    fetch("/api/portal").then((r) => r.json()).then((d) => {
      if (d.session) { setSession(d.session); load(tab); }
    }).catch(() => {}).finally(() => setBooted(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function setResolved(id: string, resolved: boolean) {
    setWorking(id); setErr("");
    try {
      const r = await fetch("/api/portal", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ action: resolved ? "resolve" : "reopen",
                               ticket_id: id }),
      });
      const d = await r.json();
      if (!r.ok || d.changed === false) {
        setErr(d.reason || d.error || "That ticket could not be updated.");
      } else { await load(tab); }
    } catch { setErr("Could not reach the service."); }
    finally { setWorking(null); }
  }

  if (!booted) return <main className="pwrap"><p className="pmuted">Loading…</p></main>;
  if (!session) return <SignIn onIn={(s) => { setSession(s); load("open"); }} />;

  return (
    <main className="pwrap">
      <header className="phead">
        <div>
          <p className="pbrand">Payce Support</p>
          <p className="pmuted">Your organisation&rsquo;s tickets. Nothing here is sent
            to a customer, and nothing is paid.</p>
        </div>
        <div className="pwho">
          {org && (
            <span className="porg">
              <b>{org.name}</b>
              <span className="pchip">{org.region}</span>
              <span className="pchip">{org.tier}</span>
            </span>
          )}
          <span className="pavatar">{(session.role || "U").charAt(0)}</span>
          <span className="pmuted">{session.role}</span>
          <button className="plink" onClick={async () => {
            await fetch("/api/portal", { method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({ action: "logout" }) });
            setSession(null); setRows([]);
          }}>Sign out</button>
        </div>
      </header>

      <h1 className="ph1">Your support tickets</h1>
      <p className="pmuted">
        Select a ticket to see what happened at each stage. Resolving one
        records it here; it does not move money or message anybody.
      </p>

      <div className="ptabs">
        {TABS.map((t) => (
          <button key={t.key}
            className={`ptab ${tab === t.key ? "on" : ""}`}
            onClick={() => { setTab(t.key); load(t.key); }}>
            {t.label} <span className="pn">{counts[t.key]}</span>
          </button>
        ))}
      </div>

      {err && <p className="perr">{err}</p>}

      {busy && rows.length === 0 ? <p className="pmuted">Loading tickets…</p>
       : rows.length === 0 ? (
        <p className="pempty">
          {tab === "open" ? "No open tickets. Everything here has been resolved."
           : tab === "resolved" ? "Nothing resolved yet."
           : "No tickets on this account."}
        </p>
      ) : (
        <ul className="plist">
          {rows.map((t) => (
            <li key={t.ticket_id} className="prow">
              <div className="pmain">
                <p className="psub">{t.subject}</p>
                <p className="pmeta">
                  <span className="pid">{t.ticket_id}</span>
                  <span className={`ppri ${t.priority}`}>{t.priority}</span>
                  <span>{t.category}</span>
                  {t.queue && <span>{t.queue}</span>}
                  {t.received_at && <span>{when(t.received_at)}</span>}
                  {t.case_id && <span className="pcase">grouped · {t.case_id}</span>}
                </p>
              </div>
              <div className="pact">
                <span className={`pstate ${t.state}`}>
                  {t.state === "resolved"
                    ? `Resolved${t.resolved_at ? " · " + when(t.resolved_at) : ""}`
                    : "Open"}
                </span>
                <button className="pbtn"
                  disabled={working === t.ticket_id}
                  onClick={() => setResolved(t.ticket_id, t.state !== "resolved")}>
                  {working === t.ticket_id ? "Saving…"
                   : t.state === "resolved" ? "Reopen" : "Mark resolved"}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}

function SignIn({ onIn }: { onIn: (s: Session) => void }) {
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function go(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const r = await fetch("/api/portal", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ action: "login", user, password }),
      });
      const d = await r.json();
      if (!r.ok) setErr(d.error || "Could not sign in.");
      else onIn(d.session);
    } catch { setErr("Could not reach the service."); }
    finally { setBusy(false); }
  }

  return (
    <main className="pwrap plogin">
      <p className="pbrand">Payce Support</p>
      <h1 className="ph1">Sign in</h1>
      <p className="pmuted">
        Each account sees one organisation&rsquo;s tickets. Which organisation
        comes from the session on the server, not from this page.
      </p>
      <form className="pform" onSubmit={go}>
        <label htmlFor="pu">Username</label>
        <input id="pu" value={user} autoComplete="username"
               onChange={(e) => setUser(e.target.value)} />
        <label htmlFor="pp">Password</label>
        <input id="pp" type="password" value={password}
               autoComplete="current-password"
               onChange={(e) => setPassword(e.target.value)} />
        <button className="pbtn primary" disabled={busy || !user || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
      {err && <p className="perr">{err}</p>}
      <p className="pdemo">
        <b>Demo accounts.</b> <code>hradmin</code> (India, Enterprise),
        <code>uaeadmin</code> (UAE, Enterprise), <code>bizadmin</code> (India,
        Business) &mdash; password <code>payce-demo</code>. This is a
        demonstration gate over synthetic data, not an identity system.
      </p>
    </main>
  );
}
