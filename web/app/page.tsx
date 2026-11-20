"use client";

import { useEffect, useRef, useState } from "react";
import { TICKETS, BURST, type Demo, FEED } from "./lib/demo";
import { status, withMarkers, period, pretty, pool, readable, heading, neutralise,
         score, stale, type Row, type Result, type Source,
         type Stage,
         bucket, matches, type Bucket,
         sortRows, type Sort } from "./lib/types";
import { Gauge, Saved, Split, Speed } from "./charts";
import { streamTicket } from "./lib/stream";

// What to call each identifier in front of an agent. "AADHAAR" is how the
// gate reports it; "Aadhaar number" is what a person says.
const PII_NAMES: Record<string, [string, string]> = {
  AADHAAR: ["Aadhaar number", "Aadhaar numbers"],
  PAN: ["PAN", "PANs"],
  IBAN: ["bank account number", "bank account numbers"],
  EMAIL: ["email address", "email addresses"],
  PHONE: ["phone number", "phone numbers"],
  EMIRATES_ID: ["Emirates ID", "Emirates IDs"],
};

function piiName(key: string, n: number): string {
  const pair = PII_NAMES[key.toUpperCase()];
  if (pair) return n > 1 ? pair[1] : pair[0];
  const plain = key.toLowerCase().replace(/_/g, " ");
  return n > 1 ? `${plain}s` : plain;
}

const mk = (d: Demo): Row => ({
  key: d.id, subject: d.subject, body: d.body, customer: d.customer,
  asOf: d.asOf, note: d.note, dated: d.dated, priority: d.priority,
  state: "idle",
});

async function triage(row: { subject: string; body: string; customer: string;
                             asOf: string; priority?: string },
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
  const [writing, setWriting] = useState(false);
  const [feeding, setFeeding] = useState(false);
  const feedRef = useRef(false);
  // The feed follows the newest ticket until you pick one yourself, and then
  // it stops and leaves you on it. Without this the selection is yanked away
  // every couple of seconds and an arriving queue is impossible to read --
  // which is the opposite of the point.
  const [filter, setFilter] = useState<Bucket | "all">("all");
  const [query, setQuery] = useState("");
  const [sortBy, setSortBy] = useState<Sort>("arrival");
  // Collapsed by default. The charts are worth a lot on a projector and the
  // footer is sticky, so left open they cover the reply you are reading --
  // which is the one thing that must always be visible.
  const [charts, setCharts] = useState(false);
  const filterRef = useRef<Bucket | "all">("all");
  const queryRef = useRef("");
  useEffect(() => { filterRef.current = filter; }, [filter]);
  useEffect(() => { queryRef.current = query; }, [query]);
  const [follow, setFollow] = useState(true);
  const followRef = useRef(true);
  const choose = (key: string) => {
    setSel(key);
    if (feedRef.current) { followRef.current = false; setFollow(false); }
  };
  const resume = () => { followRef.current = true; setFollow(true); };

  const patch = (key: string, p: Partial<Row>) =>
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...p } : r)));

  // Stage frames arrive between renders, so the callback cannot close over
  // `rows` -- it would append to whatever the list was when the run started
  // and drop every stage but the last.
  const rowsRef = useRef<Row[]>(rows);
  useEffect(() => { rowsRef.current = rows; }, [rows]);

  // Streaming is an optimisation, never a dependency. If the token route,
  // the socket or the Lambda is unhappy for any reason, this falls through
  // to the buffered endpoint that has always worked, and the only visible
  // difference is that the boxes do not light up on the way past.
  async function runOne(row: Row, useCache = true): Promise<Result> {
    try {
      const t = await fetch("/api/stream-token", { cache: "no-store" });
      if (!t.ok) throw new Error("no stream token");
      const { url, token } = await t.json();
      let live = "";
      return await streamTicket(url, token, row, useCache, {
        onStage: (st: Stage) =>
          patch(row.key, { stages: [...(rowsRef.current.find(
            (r) => r.key === row.key)?.stages ?? []), st] }),
        onDraftBegin: () => patch(row.key, { live: "" }),
        onToken: (text) => { live += text; patch(row.key, { live }); },
      }).then((result) => { patch(row.key, { streamed: true }); return result; });
    } catch {
      return await triage(row, useCache);
    }
  }

  async function run(key: string) {
    const row = rows.find((r) => r.key === key);
    if (!row) return;
    patch(key, { state: "running", result: undefined, stages: [], live: undefined,
                 streamed: false });
    try {
      const result = await runOne(row);
      patch(key, { state: result.error ? "failed" : "done", result,
                   live: undefined });
    } catch (e) {
      patch(key, { state: "failed", live: undefined,
                   result: { error: String(e) } as Result });
    }
  }

  async function runAll() {
    setBusy(true);
    await pool(rows.filter((r) => r.state !== "done").map((r) => r.key), 4, run);
    setBusy(false);
  }

  // The feed: tickets keep arriving until you stop them. One at a time and
  // in order, because the point is to watch the pipeline decide differently
  // on each one -- answer, money, injection, not-in-corpus -- and a parallel
  // burst would just be a wall of spinners.
  async function toggleFeed() {
    if (feedRef.current) { feedRef.current = false; setFeeding(false); return; }
    feedRef.current = true; setFeeding(true);
    followRef.current = true; setFollow(true);
    let n = 0;
    while (feedRef.current) {
      const d = FEED[n % FEED.length];
      n += 1;
      const key = `${d.id}-${n}`;
      const row: Row = { key, subject: d.subject, body: d.body,
                         customer: d.customer, asOf: d.asOf, note: d.note,
                         priority: d.priority, state: "running" };
      setRows((rs) => [...rs, row]);
      // Following is for watching the queue, not for fighting a filter. If
      // you have narrowed to "to you" or typed a search, an arriving ticket
      // that does not match must not drag you off what you are reading.
      if (followRef.current && filterRef.current === "all" && !queryRef.current)
        setSel(key);
      try {
        const result = await runOne(row, false);
        patch(key, { state: result.error ? "failed" : "done", result,
                     live: undefined });
      } catch (e) {
        patch(key, { state: "failed", live: undefined,
                     result: { error: String(e) } as Result });
      }
      if (!feedRef.current) break;
      await new Promise((r) => setTimeout(r, 1200));
    }
  }

  // A feed left running after the tab is closed would keep billing the
  // account for drafts nobody is watching.
  useEffect(() => () => { feedRef.current = false; }, []);

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

  // A ticket typed here goes through exactly the same path as the seeded
  // ones: same endpoint, same gate, same checks. Nothing about it is special
  // cased, which is the point of being able to type one during a demo.
  async function addTicket(t: { subject: string; body: string;
                                customer: string; asOf: string }) {
    const key = `W-${Date.now().toString(36)}`;
    const row: Row = { key, subject: t.subject.trim() || "Untitled ticket",
                       body: t.body.trim(), customer: t.customer.trim() || "C-1001",
                       asOf: t.asOf, note: "Typed into the console just now.",
                       state: "running" };
    setRows((rs) => [...rs, row]);
    setSel(key);
    setWriting(false);
    try {
      const result = await triage(row, false);
      patch(key, { state: result.error ? "failed" : "done", result });
    } catch (e) {
      patch(key, { state: "failed", result: { error: String(e) } as Result });
    }
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

  const done = rows.filter((r) => r.state === "done" && r.result);
  const incident = done.find((r) => r.result!.incident);
  const board = score(rows);

  // One pass over the queue gives both the pile counts and the list on
  // screen, so the chips, the tally and the rows can never disagree.
  const counts = rows.reduce((a, r) => { a[bucket(r)] += 1; return a; },
    { ready: 0, person: 0, refused: 0, waiting: 0 } as Record<Bucket, number>);
  const shown = sortRows(
    rows.filter((r) => (filter === "all" || bucket(r) === filter)
                       && matches(r, query)), sortBy);

  // Narrowing the queue must not strand you on a ticket the filter hides:
  // the reply pane would still show it while the list insists it is not
  // there. Fall back to the first row that survives.
  const curKey = shown.some((r) => r.key === sel) ? sel : shown[0]?.key;

  const PILES: { id: Bucket | "all"; label: string; n: number }[] = [
    { id: "all", label: "All", n: rows.length },
    { id: "ready", label: "Drafted", n: counts.ready },
    { id: "person", label: "To you", n: counts.person },
    { id: "refused", label: "Refused", n: counts.refused },
    { id: "waiting", label: "Waiting", n: counts.waiting },
  ];

  const cur = rows.find((r) => r.key === curKey) ?? shown[0] ?? rows[0];

  return (
    <main className="shell">
      <header className="top">
        <div className="brand">
          <h1>Payce Support</h1>
          <p>Drafts replies to payroll tickets, and ties every answer to the date
             the rule was in force. Nothing is sent, and nothing is paid.</p>
        </div>
        <div className="actions">
          <button className="ghost" onClick={() => setWriting((w) => !w)} disabled={busy}>
            {writing ? "Cancel" : "Write a ticket"}
          </button>
          <button className="ghost" onClick={addRepeats} disabled={busy}>Add repeat reports</button>
          <button className={feeding ? "ghost on" : "ghost"} onClick={toggleFeed}
                  disabled={busy}>
            {feeding ? "Stop the feed" : "Start live feed"}
          </button>
          <button className="primary" onClick={runAll} disabled={busy}>
            {busy ? "Working…" : "Draft all replies"}
          </button>
        </div>
      </header>

      {writing && <Composer onSubmit={addTicket} />}

      {incident && (
        <p className="incident">
          <b>Repeat issue spotted.</b> {incident.result!.case_size} reports of the
          same problem have arrived today. Raised automatically, on the fifth — today
          it takes a person about five hours to notice a pattern like this.
        </p>
      )}

      {feeding && !follow && (
        <p className="held">
          Tickets are still arriving &mdash; you are holding this one.
          <button className="link" onClick={resume}>Follow the newest again</button>
        </p>
      )}

      <div className="split">
        <nav className="list">
          <div className="qhead">
            <input className="find" type="search" value={query}
                   placeholder="Search tickets, replies, customer"
                   onChange={(e) => setQuery(e.target.value)} />
            <div className="chips">
              {PILES.map((pl) => (
                <button key={pl.id}
                        className={`chip ${filter === pl.id ? "on" : ""} c-${pl.id}`}
                        onClick={() => setFilter(pl.id)}>
                  {pl.label} <b>{pl.n}</b>
                </button>
              ))}
            </div>
            <label className="sortby">
              Sort
              <select value={sortBy} onChange={(e) => setSortBy(e.target.value as Sort)}>
                <option value="arrival">as they arrived</option>
                <option value="priority">by priority</option>
                <option value="slowest">slowest first</option>
                <option value="costliest">most expensive first</option>
              </select>
            </label>
            <p className="rail">
              {shown.length === rows.length
                ? `Queue · ${rows.length}`
                : `Showing ${shown.length} of ${rows.length}`}
            </p>
          </div>

          {shown.length === 0 && (
            <p className="none">
              Nothing matches.
              <button className="link" onClick={() => { setQuery(""); setFilter("all"); }}>
                Clear
              </button>
            </p>
          )}

          {shown.map((r) => {
            const s = r.result ? status(r.result) : null;
            return (
              <button key={r.key} onClick={() => choose(r.key)}
                      className={`item ${curKey === r.key ? "on" : ""}`}>
                <span className={`dot ${r.state === "running" ? "run" : s ? s.tone : "idle"}`} />
                {r.priority && r.priority !== "P3" && (
                  <span className={`pri ${r.priority}`}>{r.priority}</span>
                )}
                <span className="itext">
                  <span className="isub">{r.subject}</span>
                  <span className="imeta">
                    {r.state === "running" ? "drafting…" : s ? s.label : "not drafted"}
                  </span>
                </span>
              </button>
            );
          })}
          <p className="tally">
            Sample tickets. The PF wage ceiling changed on 1 Aug 2026, so the
            first two ask the same thing on either side of it.
          </p>
        </nav>

        <section className="work">
          <article className="ask">
            <p className="qmark">Customer asked</p>
            <p className="qbody">{cur.body}</p>
            <div className="qfoot">
              <span>{cur.customer}</span>
              <span className="sep">·</span>
              <label className="asof">
                answer for the payroll period of
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

          {cur.state === "running" && (
            (cur.stages?.length || cur.live !== undefined)
              ? <Live row={cur} />
              : <p className="waiting">Reading the rules…</p>
          )}

          {cur.result && (
            <Reply row={cur} acted={acted[cur.key]}
                   onAct={(how) => setActed((a) => ({ ...a, [cur.key]: how }))}
                   onCompare={(d) => comparePeriod(cur.key, d)} />
          )}

          {cur.compare && <Compare row={cur} />}
        </section>
      </div>

      {board && (
        <footer className="scorebar">
          {/* The counts are the filter. Reading "27 to you" and having no way
              to see those 27 is the kind of dead end that makes a dashboard
              feel like a poster. */}
          {charts && <div className="charts">
            <Saved rows={rows} />
            <Gauge pct={parseFloat(board.alone)} label="Handled without you"
                   sub={`${board.drafted} of ${board.seen} needed nobody`} />
            <Split s={board} />
            <Speed rows={rows} />
          </div>}

          <div className="tallies">
            <button className="impact" onClick={() => setCharts((c) => !c)}>
              {charts ? "Hide impact" : "Show impact"}
            </button>
            <button onClick={() => setFilter("all")}><b>{board.seen}</b> handled</button>
            <button className="ok" onClick={() => setFilter("ready")}>
              <b>{board.drafted}</b> drafted</button>
            <button className="warn" onClick={() => setFilter("person")}>
              <b>{board.toPerson}</b> to you</button>
            {board.refused > 0 && (
              <button className="bad" onClick={() => setFilter("refused")}>
                <b>{board.refused}</b> refused</button>
            )}
            <span className="sp">{board.spend} spent</span>
          </div>
          <dl className="board">
            <div><dt>Backed by a source</dt>
                 <dd className={board.cited === "100%" ? "good" : ""}>{board.cited}</dd>
                 <dfn>48% do today</dfn></div>
            <div><dt>Out-of-date rule used</dt>
                 <dd className={board.stale === "0" ? "good" : "bad"}>{board.stale}</dd>
                 <dfn>48 times last quarter</dfn></div>
            <div><dt>Cost per ticket</dt>
                 <dd className="good">{board.cost}</dd>
                 <dfn>the desk spends $6.03</dfn></div>
            <div><dt>Handled without you</dt>
                 <dd>{board.alone}</dd>
                 <dfn>the rest carry a reason</dfn></div>
          </dl>
        </footer>
      )}
    </main>
  );
}

// Type a ticket and send it down the same pipeline as the seeded ones.
// Customer and date are editable because both change the answer: an unknown
// customer is a hand-off, and the date decides which rule is in force.
function Composer({ onSubmit }: {
  onSubmit: (t: { subject: string; body: string;
                  customer: string; asOf: string }) => void;
}) {
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [customer, setCustomer] = useState("C-1001");
  const [asOf, setAsOf] = useState("2026-09-15");

  return (
    <form className="composer"
          onSubmit={(e) => { e.preventDefault();
                             if (body.trim()) onSubmit({ subject, body, customer, asOf }); }}>
      <p className="cmark">Write a ticket</p>
      <input className="cin" placeholder="Subject" value={subject}
             onChange={(e) => setSubject(e.target.value)} />
      <textarea className="cin cbody" rows={3} autoFocus
                placeholder="What is the customer asking? Paste anything — the PII gate and the injection screen run before any model sees it."
                value={body} onChange={(e) => setBody(e.target.value)} />
      <div className="crow">
        <label>Customer
          <input className="cin csm" value={customer}
                 onChange={(e) => setCustomer(e.target.value)} />
        </label>
        <label>Payroll period
          <input className="cin csm" type="date" value={asOf}
                 onChange={(e) => setAsOf(e.target.value)} />
        </label>
        <button className="primary" type="submit" disabled={!body.trim()}>
          Draft a reply
        </button>
      </div>
      <p className="chint">
        Known customers include <code>C-1001</code> (India) and <code>C-1003</code> (UAE).
        An id that is not on file is handed to a person, because no regional
        database can be chosen for it.
      </p>
    </form>
  );
}

/** What the pipeline is doing, while it does it.
 *
 *  The draft streams in here and NOT into the reply area, because at this
 *  point it has not been through C1-C9. If a check fails, this text is
 *  withheld and replaced by the reason -- so the panel says plainly that
 *  what you are reading is unchecked. The agent is staff, not the customer;
 *  the promise this pilot makes is that nothing unchecked reaches the
 *  customer, and approving is still a deliberate click afterwards.
 */
function Live({ row }: { row: Row }) {
  return (
    <section className="live">
      <ol className="boxes">
        {(row.stages ?? []).map((st, i) => (
          <li key={i}>
            <span className="bx">{st.box}</span>
            <span className="bn">{st.note}</span>
            {st.ms !== null && <span className="bm">{st.ms} ms</span>}
          </li>
        ))}
        {row.live === undefined && <li className="pending"><span className="bx">…</span></li>}
      </ol>
      {row.live !== undefined && (
        <div className="livedraft">
          <p className="lhead">Writing — not checked yet</p>
          <p className="ltext">{row.live}<span className="caret" /></p>
        </div>
      )}
    </section>
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
  const bad = stale(r, row.asOf);

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

      {bad.length > 0 && (
        <p className="staleline">
          {bad.length === 1 ? "A cited rule was" : `${bad.length} cited rules were`} not
          in force on this date: {bad.map((b) => b.source_id).join(", ")}.
        </p>
      )}

      {pii.length > 0 && (
        <p className="pii">
          Personal details were hidden before this was drafted: {pii.map(([k, v]) =>
            `${v} ${piiName(k, v)}`).join(", ")}. The customer still sees them
          on the ticket; the draft was written without them.
        </p>
      )}

      <div className="act">
        {acted ? (
          <p className="acted">
            {acted === "send"
              ? "Approved. This is a pilot, so nothing was actually sent."
              : "Sent to a colleague, with the reason attached."}
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
              <button className="ghost" onClick={() => onCompare(otherPeriod(row.asOf))}>
                Compare with {pretty(otherPeriod(row.asOf))}
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
