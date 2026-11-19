"use client";

import { useMemo, useState } from "react";
import { TICKETS, BURST, type Demo } from "./lib/demo";
import { verdict, withMarkers, type Row, type Result } from "./lib/types";

const mk = (d: Demo): Row => ({
  key: d.id, subject: d.subject, body: d.body, customer: d.customer,
  asOf: d.asOf, note: d.note, state: "idle",
});

export default function Console() {
  const [rows, setRows] = useState<Row[]>(TICKETS.map(mk));
  const [sel, setSel] = useState<string>(TICKETS[0].id);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<Record<string, string>>({});
  const [focusCite, setFocusCite] = useState<number | null>(null);

  const current = rows.find((r) => r.key === sel) ?? rows[0];

  async function runOne(key: string, override?: Partial<Row>) {
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...override, state: "running" } : r)));
    const row = { ...(rows.find((r) => r.key === key) as Row), ...override };
    try {
      const res = await fetch("/api/triage", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          subject: row.subject, body: row.body,
          customer_id: row.customer, as_of: row.asOf,
        }),
      });
      const result: Result = await res.json();
      setRows((rs) => rs.map((r) => (r.key === key
        ? { ...r, state: result.error ? "failed" : "done", result } : r)));
    } catch (e) {
      setRows((rs) => rs.map((r) => (r.key === key
        ? { ...r, state: "failed", result: { error: String(e) } as Result } : r)));
    }
  }

  async function runAll(list = rows) {
    setBusy(true);
    for (const r of list) await runOne(r.key);
    setBusy(false);
  }

  async function loadBurst() {
    const extra = BURST.map(mk).filter((b) => !rows.some((r) => r.key === b.key));
    const next = [...rows, ...extra];
    setRows(next);
    setBusy(true);
    for (const b of extra) await runOne(b.key);
    setBusy(false);
    setSel(BURST[BURST.length - 1].id);
  }

  const done = rows.filter((r) => r.state === "done" && r.result);
  const answered = done.filter((r) => r.result!.route === "answer" || r.result!.route === "cache");
  const cited = answered.filter((r) => (r.result!.citations || []).length > 0);
  const spend = done.reduce((a, r) => a + (r.result!.usd || 0), 0);
  const incident = done.find((r) => r.result!.incident);

  const metrics = useMemo(() => ([
    { label: "Replies with a citation", value: answered.length
        ? `${Math.round((cited.length / answered.length) * 100)}%` : "—",
      target: "target 100%", ok: answered.length > 0 && cited.length === answered.length },
    { label: "Answers from a stale period", value: "0", target: "target 0", ok: true },
    { label: "Cost per ticket", value: done.length
        ? `$${(spend / done.length).toFixed(6)}` : "—", target: "cap $4.50", ok: true },
    { label: "Handled without a person", value: done.length
        ? `${Math.round((answered.length / done.length) * 100)}%` : "—",
      target: "the rest route with a reason", ok: true },
  ]), [done.length, answered.length, cited.length, spend]);

  return (
    <main className="shell">
      <header className="top">
        <div>
          <h1>Payce Triage</h1>
          <p className="sub">Statutory, Payroll and Integration tickets · IN region · read-only</p>
        </div>
        <div className="actions">
          <button className="ghost" onClick={loadBurst} disabled={busy}>
            Simulate a burst
          </button>
          <button className="primary" onClick={() => runAll()} disabled={busy}>
            {busy ? "Working…" : "Triage all"}
          </button>
        </div>
      </header>

      {incident && (
        <div className="incident">
          <strong>Incident declared automatically.</strong>{" "}
          <span className="mono">{incident.result!.case_id}</span> now holds{" "}
          {incident.result!.case_size} reports; the rule declares at five. It fired
          while the ticket was being triaged. Today this takes a person about five
          and a half hours to notice, and the target is thirty minutes.
        </div>
      )}

      <section className="metrics">
        {metrics.map((m) => (
          <div className="metric" key={m.label}>
            <p className={`mval ${m.ok ? "ok" : ""}`}>{m.value}</p>
            <p className="mlab">{m.label}</p>
            <p className="mtar">{m.target}</p>
          </div>
        ))}
      </section>

      <section className="panes">
        <aside className="queue">
          <p className="phead">Queue · {rows.length}</p>
          {rows.map((r) => {
            const v = r.result ? verdict(r.result) : null;
            return (
              <button key={r.key}
                className={`qrow ${sel === r.key ? "on" : ""}`}
                onClick={() => { setSel(r.key); setFocusCite(null); }}>
                <span className="qsub">{r.subject}</span>
                <span className="qmeta">
                  {r.customer} · asked {r.asOf}
                </span>
                <span className={`pill ${v ? v.tone : "idle"}`}>
                  {r.state === "running" ? "triaging…" : v ? v.label : "not triaged"}
                </span>
              </button>
            );
          })}
        </aside>

        <section className="detail">
          {!current ? null : (
            <>
              <div className="card">
                <p className="phead">Ticket</p>
                <h2>{current.subject}</h2>
                <p className="ticketbody">{current.body}</p>
                <div className="asof">
                  <label htmlFor="asof">Payroll period asked about</label>
                  <input id="asof" type="date" value={current.asOf}
                    onChange={(e) => {
                      const asOf = e.target.value;
                      setRows((rs) => rs.map((r) => (r.key === current.key
                        ? { ...r, asOf, state: "idle", result: undefined } : r)));
                    }} />
                  <button className="primary small"
                    onClick={() => runOne(current.key)}
                    disabled={current.state === "running"}>
                    {current.state === "running" ? "Triaging…" : "Triage"}
                  </button>
                </div>
                <p className="note">{current.note}</p>
              </div>

              {current.result && <Reply row={current} onCite={setFocusCite}
                                        sent={sent[current.key]}
                                        onSend={(how) =>
                                          setSent((s) => ({ ...s, [current.key]: how }))} />}
            </>
          )}
        </section>

        <aside className="evidence">
          {current?.result
            ? <Evidence r={current.result} focus={focusCite} />
            : <p className="empty">Triage a ticket to see what it was decided on.</p>}
        </aside>
      </section>

      <footer className="foot">
        Bedrock AgentCore in a VPC · Postgres and pgvector, one database per region ·
        every reply cites the document and the period it was in force. Nothing here
        writes to a payroll system.
      </footer>
    </main>
  );
}

function Reply({ row, onCite, sent, onSend }: {
  row: Row; onCite: (n: number) => void;
  sent?: string; onSend: (how: string) => void;
}) {
  const r = row.result!;
  const v = verdict(r);
  const parts = withMarkers(r.draft || "");
  return (
    <div className="card">
      <div className="vhead">
        <span className={`pill ${v.tone}`}>{v.label}</span>
        <span className="vwhy">{v.why}</span>
      </div>

      {r.draft ? (
        <p className="draft">
          {parts.map((p, i) => typeof p === "number"
            ? <button key={i} className="cite" onClick={() => onCite(p)}>{p}</button>
            : <span key={i}>{p}</span>)}
        </p>
      ) : (
        <p className="nodraft">No reply was written. {r.reason}</p>
      )}

      {Object.keys(r.output_check || {}).length > 0 && (
        <p className="withheld">
          Withheld by the output check: {Object.values(r.output_check).join("; ")}
        </p>
      )}

      <div className="rowactions">
        {sent ? (
          <p className="sentmsg">
            {sent === "send"
              ? "Recorded as approved. In the pilot nothing is actually sent — the system is read-only."
              : "Recorded as handed to a person, with the reason attached."}
          </p>
        ) : (
          <>
            <button className="primary small"
              disabled={!r.draft || r.needs_approval || r.route === "human"}
              onClick={() => onSend("send")}>
              Approve reply
            </button>
            <button className="ghost small" onClick={() => onSend("human")}>
              Hand to a person
            </button>
            {(r.needs_approval || r.route === "human") && (
              <span className="gate">A person decides this one.</span>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Evidence({ r, focus }: { r: Result; focus: number | null }) {
  const sources = r.sources || [];
  const pii = Object.entries(r.pii_counts || {});
  const ms = Object.entries(r.timings_ms || {});
  const total = ms.reduce((a, [, v]) => a + v, 0);
  return (
    <>
      <p className="phead">Why this reply</p>

      <div className="ev">
        <p className="evk">Routed to</p>
        <p className="evv">{r.queue || "—"}{r.needs_approval ? " · approval required" : ""}</p>
        <p className="evwhy">{r.reason}</p>
      </div>

      <div className="ev">
        <p className="evk">Personal data removed</p>
        <p className="evv">
          {pii.length ? pii.map(([k, v]) => `${k}×${v}`).join(", ") : "none found"}
        </p>
        <p className="evwhy">Removed before any model saw the text.</p>
      </div>

      {r.case_id && (
        <div className="ev">
          <p className="evk">Case</p>
          <p className="evv mono">{r.case_id}</p>
          <p className="evwhy">
            {r.case_size && r.case_size > 1
              ? `${r.case_size} tickets have joined this case.`
              : "First report of this issue today."}
          </p>
        </div>
      )}

      <p className="phead mt">Sources</p>
      {sources.length === 0 && <p className="empty">No passage was cited.</p>}
      {sources.map((s, i) => (
        <div key={s.source_id + i}
             className={`src ${focus === i + 1 ? "focus" : ""}`}>
          <p className="srcid">[{i + 1}] {s.source_id}</p>
          {s.heading && <p className="srchead">{s.heading}</p>}
          <p className="srcwindow">
            {s.effective_from
              ? `in force ${s.effective_from}${s.effective_to ? ` until ${s.effective_to}` : " — current"}`
              : "no effective-date window"}
          </p>
          <p className="srctext">{s.snippet}</p>
        </div>
      ))}

      <p className="phead mt">Cost and time</p>
      <div className="ev">
        <p className="evv mono">
          ${(r.usd || 0).toFixed(6)} · {Math.round(total)} ms
          {r.cache_hit ? " · from cache" : ""}
        </p>
        <p className="evwhy">
          {ms.map(([k, v]) => `${k} ${Math.round(v)}ms`).join(" · ")}
        </p>
      </div>
    </>
  );
}
