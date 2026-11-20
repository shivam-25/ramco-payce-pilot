"use client";

import { Row, Score, totalMs, SAVED_MIN, TODAY_MIN, REVIEW_MIN } from "./lib/types";

// Charts drawn as inline SVG rather than pulled from a charting library:
// the whole console is one page served from the edge and a charting bundle
// would be larger than everything else here put together, these are four
// fixed shapes rather than a grammar of graphics, and a library's palette
// would fight the one the rest of the page uses.
//
// Every tile has the SAME four parts in the same order -- title, value,
// visual, one note -- so the four line up on a row without any per-tile
// nudging. The first version positioned each one by hand with margins and
// the captions ended up overlapping each other on the projector.

function Tile({ title, value, note, children }: {
  title: string; value: string; note: string; children: React.ReactNode;
}) {
  return (
    <figure className="tile">
      <figcaption className="t-top">
        <span className="t-title">{title}</span>
        <span className="t-value">{value}</span>
      </figcaption>
      <div className="t-vis">{children}</div>
      <p className="t-note">{note}</p>
    </figure>
  );
}

/** Cumulative agent-hours returned. A line rather than a total alone
 *  because the slope is the argument: it is the rate the desk gets time
 *  back, and it flattens exactly when the pilot declines to answer. */
export function Saved({ rows }: { rows: Row[] }) {
  const done = rows.filter((r) => r.state === "done" && r.result);
  const pts: number[] = [];
  let acc = 0;
  for (const r of done) {
    if (r.result!.route !== "human" && !r.result!.needs_approval) acc += SAVED_MIN;
    pts.push(acc);
  }
  const W = 200, H = 40, max = Math.max(acc, 1);
  const d = pts.length < 2 ? "" : pts.map((v, i) =>
    `${i === 0 ? "M" : "L"} ${(i / (pts.length - 1)) * W} ${H - (v / max) * (H - 4)}`
  ).join(" ");
  const hrs = acc / 60;
  return (
    <Tile title="Agent time returned"
          value={hrs < 1 ? `${Math.round(acc)} min` : `${hrs.toFixed(1)} h`}
          note={`${SAVED_MIN} min a reply · ${TODAY_MIN} today less ${REVIEW_MIN} to approve`}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
           aria-label={`${Math.round(acc)} minutes returned`}>
        {d && <path d={`${d} L ${W} ${H} L 0 ${H} Z`} fill="var(--ok)" opacity="0.13" />}
        {d && <path d={d} fill="none" stroke="var(--ok)" strokeWidth="2"
                    vectorEffect="non-scaling-stroke" />}
      </svg>
    </Tile>
  );
}

/** A semicircular gauge, because a full ring invites you to compare the
 *  gap to the fill and that is not the comparison being made. */
export function Gauge({ pct, drafted, seen }:
                      { pct: number; drafted: number; seen: number }) {
  const R = 40, C = Math.PI * R;
  const on = Math.max(0, Math.min(100, pct)) / 100 * C;
  const tone = pct >= 60 ? "var(--ok)" : pct >= 30 ? "var(--warn, #F2B134)" : "var(--accent)";
  return (
    <Tile title="Handled without you" value={`${Math.round(pct)}%`}
          note={`${drafted} of ${seen} needed nobody`}>
      <svg viewBox="0 0 96 44" role="img" aria-label={`${Math.round(pct)} percent`}>
        <path d="M 8 40 A 40 40 0 0 1 88 40" fill="none"
              stroke="var(--line)" strokeWidth="8" strokeLinecap="round" />
        <path d="M 8 40 A 40 40 0 0 1 88 40" fill="none"
              stroke={tone} strokeWidth="8" strokeLinecap="round"
              strokeDasharray={`${on} ${C}`} />
      </svg>
    </Tile>
  );
}

/** A stacked bar, not a pie: three parts, and the question is "how much of
 *  the queue", which the eye reads as a length. */
export function Split({ s }: { s: Score }) {
  const tot = Math.max(s.drafted + s.toPerson + s.refused, 1);
  const seg = [
    { n: s.drafted, c: "var(--ok)" },
    { n: s.toPerson, c: "var(--warn, #F2B134)" },
    { n: s.refused, c: "var(--accent)" },
  ].filter((x) => x.n > 0);
  let x = 0;
  return (
    <Tile title="Where the queue went" value={`${tot}`}
          note={`${s.drafted} drafted · ${s.toPerson} to you · ${s.refused} refused`}>
      <svg viewBox="0 0 200 12" preserveAspectRatio="none" role="img"
           aria-label="How the queue was handled">
        {seg.map((g, i) => {
          const w = (g.n / tot) * 200, at = x; x += w;
          return <rect key={i} x={at} y="0" width={Math.max(w - 1.5, 0)} height="12"
                       rx="2.5" fill={g.c} />;
        })}
      </svg>
    </Tile>
  );
}

/** Time per ticket with the 3s gate drawn in. The threshold is the point:
 *  a bar chart without it is decoration. */
export function Speed({ rows }: { rows: Row[] }) {
  const ms = rows.filter((r) => r.state === "done" && r.result)
                 .map((r) => totalMs(r.result)).filter((v) => v > 0).slice(-36);
  const W = 200, H = 40, GATE = 3000;
  const max = Math.max(GATE * 1.2, ...ms, 1);
  const bw = ms.length ? W / ms.length : 0;
  const gy = H - (GATE / max) * H;
  const over = ms.filter((v) => v > GATE).length;
  const p50 = ms.length
    ? [...ms].sort((a, b) => a - b)[Math.floor(ms.length / 2)] : 0;
  return (
    <Tile title="Time per ticket"
          value={p50 ? `${(p50 / 1000).toFixed(1)}s` : "—"}
          note={`median · ${over} of ${ms.length} over the 3s gate`}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
           aria-label={`Median ${p50} ms, ${over} over the gate`}>
        {ms.map((v, i) => {
          const h = (v / max) * H;
          return <rect key={i} x={i * bw} y={H - h} width={Math.max(bw - 1, 0.8)}
                       height={h} rx="1"
                       fill={v > GATE ? "var(--accent)" : "var(--ok)"} opacity="0.9" />;
        })}
        <line x1="0" y1={gy} x2={W} y2={gy} stroke="var(--dim)" strokeWidth="1"
              strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
      </svg>
    </Tile>
  );
}
