"use client";

import { Row, Score, totalMs, SAVED_MIN, TODAY_MIN, REVIEW_MIN } from "./lib/types";

// Charts drawn as inline SVG rather than pulled from a charting library.
// Three reasons, in order: the whole console is one page served from the
// edge and a charting bundle is larger than everything else here put
// together; these are four fixed shapes, not a grammar of graphics; and a
// library's default palette would fight the one the rest of the page uses.
//
// Everything below is drawn from the same rows the queue shows, so a number
// on a chart can always be traced to a ticket you can open.

/** A Grafana-style arc. Semicircle, because a full ring invites the reader
 *  to compare the gap to the fill, which is not the comparison here. */
export function Gauge({ pct, label, sub }:
                      { pct: number; label: string; sub: string }) {
  const R = 46, C = Math.PI * R;            // semicircle arc length
  const on = Math.max(0, Math.min(100, pct)) / 100 * C;
  const tone = pct >= 60 ? "var(--ok)" : pct >= 30 ? "var(--warn, #F2B134)" : "var(--accent)";
  return (
    <figure className="chart gauge">
      <svg viewBox="0 0 120 68" role="img" aria-label={`${label}: ${Math.round(pct)}%`}>
        <path d="M 14 58 A 46 46 0 0 1 106 58" fill="none"
              stroke="var(--line)" strokeWidth="9" strokeLinecap="round" />
        <path d="M 14 58 A 46 46 0 0 1 106 58" fill="none"
              stroke={tone} strokeWidth="9" strokeLinecap="round"
              strokeDasharray={`${on} ${C}`} />
        <text x="60" y="52" textAnchor="middle" className="gnum">{Math.round(pct)}%</text>
      </svg>
      <figcaption><b>{label}</b><span>{sub}</span></figcaption>
    </figure>
  );
}

/** Cumulative agent-hours returned, one step per handled ticket. A line
 *  rather than a total alone because the slope is the argument: it is the
 *  rate the desk gets time back, and it only bends when the pilot declines. */
export function Saved({ rows }: { rows: Row[] }) {
  const done = rows.filter((r) => r.state === "done" && r.result);
  const pts: number[] = [];
  let acc = 0;
  for (const r of done) {
    const drafted = r.result!.route !== "human" && !r.result!.needs_approval;
    if (drafted) acc += SAVED_MIN;
    pts.push(acc);
  }
  const hours = acc / 60;
  const W = 260, H = 64, max = Math.max(acc, 1);
  const d = pts.length < 2 ? "" : pts.map((v, i) =>
    `${i === 0 ? "M" : "L"} ${(i / (pts.length - 1)) * W} ${H - (v / max) * (H - 6)}`
  ).join(" ");
  const area = d ? `${d} L ${W} ${H} L 0 ${H} Z` : "";
  return (
    <figure className="chart saved">
      <p className="big">{hours < 1 ? `${Math.round(acc)} min` : `${hours.toFixed(1)} h`}</p>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
           aria-label={`Agent time returned: ${Math.round(acc)} minutes`}>
        {area && <path d={area} fill="var(--ok)" opacity="0.12" />}
        {d && <path d={d} fill="none" stroke="var(--ok)" strokeWidth="2"
                    vectorEffect="non-scaling-stroke" />}
      </svg>
      <figcaption><b>Agent time returned</b>
        <span>{SAVED_MIN} min a drafted reply &mdash; {TODAY_MIN} min today,
              less {REVIEW_MIN} min to read and approve</span></figcaption>
    </figure>
  );
}

/** Where the queue went. A stacked bar, not a pie: three parts, and the
 *  question is "how much of the queue", which is a length. */
export function Split({ s }: { s: Score }) {
  const tot = Math.max(s.drafted + s.toPerson + s.refused, 1);
  const seg = [
    { n: s.drafted, c: "var(--ok)", l: "drafted" },
    { n: s.toPerson, c: "var(--warn, #F2B134)", l: "to you" },
    { n: s.refused, c: "var(--accent)", l: "refused" },
  ].filter((x) => x.n > 0);
  let x = 0;
  return (
    <figure className="chart split">
      <svg viewBox="0 0 260 14" preserveAspectRatio="none" role="img"
           aria-label="How the queue was handled">
        {seg.map((g, i) => {
          const w = (g.n / tot) * 260, at = x; x += w;
          return <rect key={i} x={at} y="0" width={Math.max(w - 1, 0)} height="14"
                       rx="3" fill={g.c} />;
        })}
      </svg>
      <figcaption><b>Where the queue went</b>
        <span>{seg.map((g) => `${g.n} ${g.l}`).join(" · ")}</span></figcaption>
    </figure>
  );
}

/** Time per ticket, newest at the right, with the 3s gate drawn in. The
 *  gate is the point: a bar chart without the threshold is decoration. */
export function Speed({ rows }: { rows: Row[] }) {
  const ms = rows.filter((r) => r.state === "done" && r.result)
                 .map((r) => totalMs(r.result)).filter((v) => v > 0).slice(-40);
  const W = 260, H = 54, GATE = 3000;
  const max = Math.max(GATE * 1.15, ...ms, 1);
  const bw = ms.length ? W / ms.length : 0;
  const gy = H - (GATE / max) * H;
  return (
    <figure className="chart speed">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
           aria-label="Time per ticket against the three second gate">
        {ms.map((v, i) => {
          const h = (v / max) * H;
          return <rect key={i} x={i * bw} y={H - h} width={Math.max(bw - 1, 0.6)}
                       height={h} rx="1"
                       fill={v > GATE ? "var(--accent)" : "var(--ok)"} opacity="0.85" />;
        })}
        <line x1="0" y1={gy} x2={W} y2={gy} stroke="var(--dim)"
              strokeWidth="1" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
      </svg>
      <figcaption><b>Time per ticket</b>
        <span>dashed line is the 3s gate &mdash; {ms.filter((v) => v > GATE).length} over,
              of {ms.length}</span></figcaption>
    </figure>
  );
}
