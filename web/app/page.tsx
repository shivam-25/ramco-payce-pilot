"use client";

import { useEffect, useRef, useState } from "react";
import { TICKETS, BURST, type Demo } from "./lib/demo";
import { status, withMarkers, period, pretty, pool, readable, heading, neutralise,
         type Row, type Result, type Source } from "./lib/types";

const mk = (d: Demo): Row => ({
  key: d.id, subject: d.subject, body: d.body, customer: d.customer,
  asOf: d.asOf, note: d.note, dated: d.dated, state: "idle",
});

async function triage(row: { subject: string; body: string; customer: string; asOf: string },
                      useCache = true) {
  const res = await fetch("/api/triage", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ subject: row.subject, body: row.body,
                           customer_id: row.customer, as_of: row.asOf,
                           use_cache: useCache }),
  });
  return (await res.json()) as Result;
}

export default function Console() {
  const [rows, setRows] = useState<Row[]>(TICKETS.map(mk));
  const [sel, setSel] = useState(TICKETS[0].id);
  const [busy, setBusy] = useState(false);
  const [acted, setActed] = useState<Record<string, string>>({});

  const patch = (key: string, p: Partial<Row>) =>
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...p } : r)));

  async function run(key: string) {
    const row = rows.find((r) => r.key === key);
    if (!row) return;
    patch(key, { state: "running", result: undefined });
    try {
      const result = await triage(row);
      patch(key, { state: result.error ? "failed" : "done", result });
    } catch (e) {
      patch(key, { state: "failed", result: { error: String(e) } as Result });
    }
  }

  async function runAll() {
    setBusy(true);
    await pool(rows.filter((r) => r.state !== "done").map((r) => r.key), 4, run);
    setBusy(false);
  }

  async function addRepeats() {
    const extra = BURST.map(mk).filter((b) => !rows.some((r) => r.key === b.key));
    setRows((rs) => [...rs, ...extra]);
    setBusy(true);
    // Deliberately one at a time: the point is watching the count climb to
    // the threshold, which a parallel burst would hide.
    for (const b of extra) await run(b.key);
    setBusy(false);
    setSel(extra[extra.length - 1]?.key ?? sel);
  }

  async function comparePeriod(key: string, asOf: string) {
    const row = rows.find((r) => r.key === key);
    if (!row) return;
    patch(key, { compare: { asOf, state: "running" } });
    // Subject as well as body: the subject carries the month too, and while
    // the answer is now correct either way, a reply that says "for June" when
    // asked about September reads like the bug it used to be.
    const result = await triage(
      { ...row, subject: neutralise(row.subject), body: neutralise(row.body), asOf },
      false);
    patch(key, { compare: { asOf, state: "done", result } });
  }

  const cur = rows.find((r) => r.key === sel) ?? rows[0];
  const done = rows.filter((r) => r.state === "done" && r.result);
  const incident = done.find((r) => r.result!.incident);
  const spend = done.reduce((a, r) => a + (r.result!.usd || 0), 0);

  return (
    <main className="shell">
      <header className="top">
        <div className="brand">
          <h1>Payce Support</h1>
          <p>Drafts replies for payroll and statutory tickets. Nothing is sent, and nothing is paid.</p>
        </div>
        <div className="actions">
          <button className="ghost" onClick={addRepeats} disabled={busy}>Add repeat reports</button>
          <button className="primary" onClick={runAll} disabled={busy}>
            {busy ? "Working…" : "Draft all replies"}
          </button>
        </div>
      </header>

      {incident && (
        <p className="incident">
          <b>Repeat issue spotted.</b> {incident.result!.case_size} reports of the
          same problem have arrived today. Raised automatically, on the fifth — today
          it takes a person about five hours to notice a pattern like this.
        </p>
      )}

      <div className="split">
        <nav className="list">
          {rows.map((r) => {
            const s = r.result ? status(r.result) : null;
            return (
              <button key={r.key} onClick={() => setSel(r.key)}
                      className={`item ${sel === r.key ? "on" : ""}`}>
                <span className={`dot ${r.state === "running" ? "run" : s ? s.tone : "idle"}`} />
                <span className="itext">
                  <span className="isub">{r.subject}</span>
                  <span className="imeta">
                    {r.state === "running" ? "drafting…" : s ? s.label : "not drafted"}
                  </span>
                </span>
              </button>
            );
          })}
          {done.length > 0 && (
            <p className="tally">
              {done.length} drafted · {(spend / done.length).toFixed(6)} USD each
            </p>
          )}
        </nav>

        <section className="work">
          <article className="ask">
            <p className="qmark">Customer asked</p>
            <p className="qbody">{cur.body}</p>
            <div className="qfoot">
              <span>{cur.customer}</span>
              <span className="sep">·</span>
              <label>
                for the period
                <input type="date" value={cur.asOf}
                  onChange={(e) => patch(cur.key, { asOf: e.target.value,
                                                    state: "idle", result: undefined,
                                                    compare: undefined })} />
              </label>
              {cur.state !== "running" && (
                <button className="link" onClick={() => run(cur.key)}>
                  {cur.result ? "Draft again" : "Draft a reply"}
                </button>
              )}
            </div>
          </article>

          {cur.state === "running" && <p className="waiting">Reading the rules…</p>}

          {cur.result && (
            <Reply row={cur} acted={acted[cur.key]}
                   onAct={(how) => setActed((a) => ({ ...a, [cur.key]: how }))}
                   onCompare={(d) => comparePeriod(cur.key, d)} />
          )}

          {cur.compare && <Compare row={cur} />}
        </section>
      </div>
    </main>
  );
}

function Reply({ row, acted, onAct, onCompare }: {
  row: Row; acted?: string;
  onAct: (how: string) => void; onCompare: (asOf: string) => void;
}) {
  const r = row.result!;
  const s = status(r);
  const [open, setOpen] = useState<number | null>(null);
  const [details, setDetails] = useState(false);
  const sources = r.sources || [];
  const parts = withMarkers(r.draft || "");
  const pii = Object.entries(r.pii_counts || {});

  return (
    <article className="reply">
      <p className={`state ${s.tone}`}>{s.label}<span>{s.line}</span></p>

      {r.draft ? (
        <div className="draftwrap">
          <p className="draft">
            {parts.map((p, i) => typeof p === "number" ? (
              <button key={i} className={`cite ${open === p ? "on" : ""}`}
                      onClick={() => setOpen(open === p ? null : p)}>{p}</button>
            ) : <span key={i}>{p}</span>)}
          </p>
          {open !== null && sources[open - 1] && (
            <Cited s={sources[open - 1]} n={open} onClose={() => setOpen(null)} />
          )}
        </div>
      ) : (
        <p className="nodraft">Nothing was sent to the customer.</p>
      )}

      {pii.length > 0 && (
        <p className="pii">
          Removed before the assistant read it: {pii.map(([k, v]) =>
            `${v} ${k.toLowerCase()}${v > 1 ? "s" : ""}`).join(", ")}.
        </p>
      )}

      <div className="act">
        {acted ? (
          <p className="acted">
            {acted === "send"
              ? "Approved. In the pilot nothing actually leaves the building."
              : "Passed to a person, with the reason attached."}
          </p>
        ) : (
          <>
            {s.tone === "ok" && (
              <button className="primary" onClick={() => onAct("send")}>Approve</button>
            )}
            <button className="ghost" onClick={() => onAct("human")}>
              {s.tone === "ok" ? "Send to a person instead" : "Take it"}
            </button>
            {row.dated && !row.compare && (
              <button className="link" onClick={() => onCompare(otherPeriod(row.asOf))}>
                Check another period
              </button>
            )}
          </>
        )}
      </div>

      <button className="more" onClick={() => setDetails(!details)}>
        {details ? "Hide how this was decided" : "How this was decided"}
      </button>
      {details && <Details r={r} />}
    </article>
  );
}

function Cited({ s, n, onClose }: { s: Source; n: number; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, []);
  return (
    <div className="cited" ref={ref}>
      <p className="ctop">
        <span className="cnum">{n}</span>
        <span className="cid">{s.source_id}</span>
        <span className="cwin">{period(s)}</span>
        <button className="x" onClick={onClose} aria-label="Close">×</button>
      </p>
      {s.heading && <p className="chead">{heading(s.heading)}</p>}
      <p className="ctext">{readable(s.snippet)}</p>
    </div>
  );
}

function Compare({ row }: { row: Row }) {
  const c = row.compare!;
  return (
    <article className="compare">
      <p className="cmphead">The same question, asked for {pretty(c.asOf)}</p>
      {c.state === "running" ? <p className="waiting">Reading the rules…</p> : (
        <>
          <p className="cmpdraft">{(c.result?.draft || "").replace(/\s*\[\d+\]/g, "").replace(/\s+\./g, ".").trim()
            || "A person would handle this one."}</p>
          <p className="cmpnote">
            Same question, different period, different answer — because the rule
            changed on 1 August. Which version applies is decided by the period, not
            by how the question is worded.
          </p>
        </>
      )}
    </article>
  );
}

function Details({ r }: { r: Result }) {
  const ms = Object.entries(r.timings_ms || {});
  const total = ms.reduce((a, [, v]) => a + v, 0);
  return (
    <dl className="details">
      <dt>Goes to</dt><dd>{r.queue || "a person"}</dd>
      <dt>Why</dt><dd>{r.reason}</dd>
      {r.case_id && <><dt>Grouped as</dt>
        <dd>{r.case_id}{r.case_size && r.case_size > 1
          ? ` · ${r.case_size} reports today` : " · first report today"}</dd></>}
      {Object.keys(r.output_check || {}).length > 0 && (
        <><dt>Held back by</dt><dd>{Object.values(r.output_check).join("; ")}</dd></>
      )}
      <dt>Cost</dt><dd>{(r.usd || 0).toFixed(6)} USD · {(total / 1000).toFixed(1)}s
        {r.cache_hit ? " · reused" : ""}</dd>
    </dl>
  );
}

/** June and September are the two sides of the rule change the pilot exists
 *  to get right, so "another period" means the other one. */
function otherPeriod(asOf: string): string {
  return asOf < "2026-08-01" ? "2026-09-15" : "2026-06-15";
}
