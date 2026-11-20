"use client";

import { useEffect, useState } from "react";

type Full = {
  ticket_id: string; subject: string; body: string; category: string;
  raw_category: string; priority: string; state: string; received_at: string;
  first_response_at: string; resolved_at: string; queue: string;
  queue_intake: string; case_id: string; channel: string; region: string;
  tier: string; pii_counts: Record<string, number>; csat: null;
};
type Run = {
  route?: string; reason?: string; draft?: string; usd?: number;
  needs_approval?: boolean; queue?: string; masked_text?: string;
  citations?: string[];
  // What the pipeline actually sends for a passage: a similarity, the
  // heading, and a snippet. The source id travels in `citations`, in the
  // same order, which is why this is optional here.
  sources?: { sim?: number; heading?: string; snippet?: string;
              source_id?: string; effective_from?: string;
              effective_to?: string }[];
  priority?: string; similarity?: number; cost?: { usd: number; by_model: Record<string,
    { calls: number; in: number; out: number; usd: number }> };
  // Two shapes reach here: a flat {check: message} map, and the fuller
  // {passed, failures, detail} the output check itself returns. Reading
  // only the first listed "passed" and "failures" as if they were checks.
  output_check?: Record<string, unknown> & {
    passed?: boolean; failures?: string[]; detail?: Record<string, unknown>;
  }; timings_ms?: Record<string, number>;
  pii_counts?: Record<string, number>; cache_hit?: boolean;
};

const STAGE_LABEL: Record<string, string> = {
  pii: "PII gate", guardrail_pii: "Managed PII screen", normalise: "Normalise",
  lookup: "Lookup", injection: "Injection screen",
  guardrail_injection: "Managed injection screen", classify: "Classify",
  dedupe: "Dedupe", route: "Router", router: "Router", retrieve: "Retrieve",
  draft: "Draft", answer: "Draft", judge: "Judge",
  output_check: "Output check", cache: "Cache",
};
const MODEL_STAGES = new Set(["classify", "draft", "answer", "judge"]);

/** Anything the server sends, rendered as text.
 *
 *  React throws on an object child, and the fields below are shaped by the
 *  pipeline rather than by this file: a stage that starts returning
 *  {code, detail} where it used to return a string would take the whole
 *  page down. One page-level crash is a worse failure than one ugly cell,
 *  so unknown shapes are printed instead of thrown. */
function txt(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return v.map(txt).filter(Boolean).join(", ");
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    for (const k of ["reason", "detail", "message", "name", "label", "code"]) {
      if (typeof o[k] === "string") return o[k] as string;
    }
    const parts = Object.entries(o).map(([k, x]) => `${k}: ${txt(x)}`);
    return parts.join(" · ");
  }
  return String(v);
}

function stamp(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(+d) ? "—" : d.toLocaleString(undefined,
    { day: "numeric", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit" });
}

function Masked({ text }: { text: string }) {
  return (
    <>{text.split(/(\[REDACTED:[A-Z_]+(?::\d+)?\])/g).map((p, i) =>
      /^\[REDACTED:/.test(p)
        ? <mark key={i} className="red">{p}</mark> : <span key={i}>{p}</span>)}</>
  );
}

export default function Detail({ id, onBack, onChanged }:
  { id: string; onBack: () => void; onChanged: () => void }) {
  const [t, setT] = useState<Full | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch("/api/portal", { method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ action: "ticket", ticket_id: id }) })
      .then((r) => r.json())
      .then((d) => {
        if (d.error) setErr(d.error);
        else if (d.ticket) setT(d.ticket);
        // A reply with neither is a wiring fault, not a slow network. Saying
        // so beats spinning forever on "Loading ticket".
        else setErr("The server replied without a ticket. Check the portal route.");
      })
      .catch(() => setErr("Could not load the ticket."));
  }, [id]);

  async function analyse() {
    if (!t) return;
    setBusy(true); setErr("");
    try {
      // Through the portal route, not /api/triage, so the organisation comes
      // from the signed cookie. Sending customer_id from here would be the one
      // place the browser got to choose whose data a run reads.
      const r = await fetch("/api/portal", { method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action: "run", subject: t.subject,
          body: t.body,
          as_of: (t.received_at || "").slice(0, 10) || null }) });
      const d = await r.json();
      if (d.error) setErr(d.error); else setRun(d);
    } catch { setErr("Could not reach the pipeline."); }
    finally { setBusy(false); }
  }

  async function toggle() {
    if (!t) return;
    setSaving(true);
    try {
      const r = await fetch("/api/portal", { method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          action: t.state === "resolved" ? "reopen" : "resolve",
          ticket_id: t.ticket_id }) });
      const d = await r.json();
      if (d.changed) { setT({ ...t, state: t.state === "resolved"
        ? "open" : "resolved" }); onChanged(); }
      else setErr(d.reason || d.error || "Could not update.");
    } finally { setSaving(false); }
  }

  if (err && !t) return (
    <div className="dwrap"><button className="plink" onClick={onBack}>← Back</button>
      <p className="perr">{err}</p></div>);
  if (!t) return <div className="dwrap"><p className="pmuted">Loading ticket…</p></div>;

  const stages = Object.entries(run?.timings_ms || {});
  const gateCounts = Object.entries(t.pii_counts || {});

  // Which checks held the draft back, whichever shape the server used.
  const oc = run?.output_check;
  const failures: string[] = Array.isArray(oc?.failures)
    ? (oc!.failures as string[])
    : Object.keys(oc || {}).filter(
        (k) => !["passed", "failures", "detail"].includes(k));
  const failureDetail = failures
    .map((f) => txt((oc?.detail as Record<string, unknown>)?.[f] ?? oc?.[f]))
    .filter(Boolean).join(" · ");

  return (
    <div className="dwrap">
      <div className="dtop">
        <button className="plink" onClick={onBack}>← Back</button>
        <div className="dbadges">
          <span className={`dpill ${t.state}`}>
            {t.state === "resolved" ? "Resolved" : "Open"}</span>
          {!!txt(t.priority) &&
            <span className={`dpill pri ${t.priority}`}>{txt(t.priority)}</span>}
          {run?.route && <span className="dpill">{run.route === "answer"
            ? "Auto answer" : run.route === "cache"
            ? "Reused answer" : "Needs a person"}</span>}
          {run?.cache_hit !== undefined && (
            <span className={`dpill ${run.cache_hit ? "cached" : "fresh"}`}>
              {run.cache_hit ? "From cache" : "Freshly drafted"}</span>)}
        </div>
      </div>

      <h2 className="dtid">{t.ticket_id}
        {run?.usd !== undefined &&
          <span className="dcost">${run.usd.toFixed(5)}</span>}</h2>
      <p className="pmuted">{t.tier} · {t.region} · {t.channel || "email"}</p>

      <div className="dcols">
        <aside className="dcol">
          <p className="dh">Requester</p>
          <dl className="dfacts">
            <dt>Region</dt><dd>{txt(t.region)}</dd>
            <dt>Tier</dt><dd>{t.tier || "—"}</dd>
            <dt>Channel</dt><dd>{t.channel || "—"}</dd>
            <dt>Received</dt><dd>{stamp(t.received_at)}</dd>
            <dt>First reply</dt><dd>{stamp(t.first_response_at)}</dd>
          </dl>

          <p className="dh">Properties</p>
          <dl className="dfacts">
            <dt>Status</dt><dd>{txt(t.state)}</dd>
            <dt>Queue</dt><dd>{t.queue || "—"}</dd>
            <dt>Intake queue</dt><dd>{t.queue_intake || "—"}</dd>
            <dt>Category</dt><dd>{txt(t.category)}</dd>
            {t.raw_category && t.raw_category !== t.category &&
              <><dt>As filed</dt><dd>{txt(t.raw_category)}</dd></>}
            <dt>Grouped as</dt><dd>{t.case_id || "first report"}</dd>
            {run && <><dt>Routing rule</dt><dd>{txt(run.reason)}</dd>
              <dt>Decided by</dt>
              <dd>{run.route === "human" ? "rule → a person" : "rule"}</dd>
              <dt>Human intervention</dt>
              <dd>{run.needs_approval ? "required" : "—"}</dd>
              <dt>Reason codes</dt>
              <dd className="mono">{failures.length
                ? failures.join(", ")
                : run.route === "human" ? "ROUTED_TO_PERSON" : "AUTO_ANSWER"}</dd></>}
            <dt>Resolved</dt><dd>{stamp(t.resolved_at)}</dd>
          </dl>

          <p className="dh">The gate on this ticket</p>
          {gateCounts.length === 0
            ? <p className="dnote">No identifiers found in this one.</p>
            : <ul className="dchips">{gateCounts.map(([k, v]) =>
                <li key={k}>{txt(v)} × {k}</li>)}</ul>}
          <p className="dnote">Counted when the ticket was written. The columns
            below are the masked ones; there is no unmasked column to read.</p>

          <p className="dh">Customer satisfaction</p>
          <p className="dnote">Not collected. This export carries no CSAT
            field, so there is nothing to show and nothing is invented.</p>
        </aside>

        <section className="dmain">
          <article className="dmsg customer">
            <p className="dwho">Customer · {t.channel || "email"} · {stamp(t.received_at)}</p>
            <p className="dsubj">{txt(t.subject)}</p>
            <p className="dbody"><Masked text={txt(t.body) || "(no body on this ticket)"} /></p>
          </article>

          {run?.draft ? (
            <article className="dmsg bot">
              <p className="dwho">
                {run.cache_hit
                  ? "Reused from an earlier answer · not sent"
                  : "Drafted reply · not sent"}</p>
              {run.cache_hit && (
                <p className="dreuse">
                  This was not written again. An earlier answer to the same
                  question, in force for the same period, was reused
                  {typeof run.similarity === "number"
                    ? ` at ${run.similarity.toFixed(3)} similarity` : ""}
                  {" "}— which is why it cost ${(run.usd ?? 0).toFixed(6)}
                  {" "}instead of roughly $0.0007, and why it returned in
                  about a second instead of five.
                </p>)}
              <p className="dbody">{txt(run.draft)}</p>
              {!!run.sources?.length && (
                <ul className="dsrc">
                  {run.sources.map((s, i) => (
                    <li key={i}>
                      <span className="mono">
                        {txt(s.source_id) || txt(run.citations?.[i]) || `passage ${i + 1}`}
                      </span>
                      {s.heading ? ` · ${txt(s.heading)}` : ""}
                      {s.effective_from
                        ? ` · in force from ${txt(s.effective_from)}` : ""}
                      {typeof s.sim === "number" ? ` · ${s.sim.toFixed(3)}` : ""}
                    </li>
                  ))}
                </ul>
              )}
            </article>
          ) : run ? (
            <article className="dmsg held">
              <p className="dwho">Nothing drafted</p>
              <p className="dbody">{txt(run.reason)}</p>
            </article>
          ) : (
            <article className="dmsg empty">
              <p className="dbody">No reply drafted yet. Running the pipeline
                shows every stage, what it decided, and what it cost.</p>
            </article>
          )}

          <div className="dacts">
            <button className="pbtn primary" onClick={analyse} disabled={busy}>
              {busy ? "Running…" : run ? "Run again" : "Run the pipeline"}</button>
            <button className="pbtn" onClick={toggle} disabled={saving}>
              {saving ? "Saving…"
                : t.state === "resolved" ? "Reopen ticket" : "Mark resolved"}</button>
          </div>
          {err && <p className="perr">{err}</p>}
          <p className="dnote">Nothing here is sent to a customer and no money
            moves. Resolving records the state on this ticket.</p>
        </section>

        <aside className="dcol">
          <p className="dh">Ticket flow</p>
          {stages.length === 0
            ? <p className="dnote">Run the pipeline to see each stage with the
                time it actually took.</p>
            : <ol className="dflow">
                {stages.map(([k, v], i) => (
                  <li key={k} className={MODEL_STAGES.has(k) ? "mdl" : ""}>
                    <span className="dnum">{i + 1}</span>
                    <span className="dsn">{STAGE_LABEL[k] || k}</span>
                    <span className="dms">{v < 1 ? "<1" : Math.round(v)} ms</span>
                  </li>))}
              </ol>}

          {run && (
            <>
              <p className="dh">What the model saw</p>
              {run.masked_text
                ? <p className="dmasked"><Masked text={txt(run.masked_text)} /></p>
                : <p className="dnote">Nothing reached a model.</p>}

              <p className="dh">LLM analysis</p>
              <dl className="dfacts">
                <dt>Models</dt>
                <dd>{Object.keys(run.cost?.by_model || {}).length
                  ? Object.entries(run.cost!.by_model).map(([m, d]) =>
                      <span key={m} className="dmodel">
                        {m.split(".").pop()} · {d.calls} call{d.calls === 1 ? "" : "s"}
                      </span>)
                  : "none — refused before a model"}</dd>
                <dt>Priority</dt><dd>{txt(run.priority) || txt(t.priority)}</dd>
                <dt>Summary</dt><dd>{txt(run.reason)}</dd>
              </dl>

              <p className="dh">Decision</p>
              <dl className="dfacts">
                <dt>Route</dt><dd>{txt(run.route)}</dd>
                <dt>Approval</dt><dd>{run.needs_approval ? "a person decides" : "not required"}</dd>
                <dt>Cost</dt><dd>${(run.usd ?? 0).toFixed(6)}</dd>
                <dt>Cache</dt><dd>{run.cache_hit ? "reused" : "fresh"}</dd>
              </dl>
              {!!failures.length && (
                <>
                  <p className="dh">Held back by</p>
                  <ul className="dchips">
                    {failures.map((f) => <li key={f}>{f}</li>)}
                  </ul>
                  {!!failureDetail && <p className="dnote">{failureDetail}</p>}
                </>
              )}
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
