export type Source = {
  source_id: string; heading: string | null;
  effective_from: string | null; effective_to: string | null;
  snippet: string; sim: number;
};

export type Result = {
  ticket_id: string; route: string; reason: string;
  queue: string | null; region: string | null; tier: string | null;
  priority: string | null; case_id: string | null;
  case_size?: number; incident?: boolean;
  draft: string; citations: string[]; sources?: Source[];
  needs_approval: boolean; output_check: Record<string, string>;
  pii_counts: Record<string, number>; cache_hit: boolean;
  timings_ms: Record<string, number>; usd: number;
  sla_hours: [number, number] | null;
  error?: string;
};

export type Row = {
  key: string; subject: string; body: string; customer: string;
  asOf: string; note: string; dated?: boolean;
  state: "idle" | "running" | "done" | "failed";
  result?: Result;
  compare?: { asOf: string; result?: Result; state: "running" | "done" };
};

export type Status = { label: string; tone: "ok" | "warn" | "bad"; line: string };

/** One word for the state, one sentence for why — in the words a support
 *  lead would use, not the pipeline's. */
export function status(r: Result): Status {
  if (r.error) return { label: "Failed", tone: "bad", line: r.error };
  if (r.reason?.includes("injection"))
    return { label: "Refused", tone: "bad",
             line: "This ticket tried to give the assistant instructions. Nothing was run." };
  if (r.needs_approval)
    return { label: "Needs you", tone: "warn",
             line: "Money is involved, so a person decides." };
  if (r.route === "human") {
    const withheld = Object.keys(r.output_check || {}).length > 0;
    return { label: "Needs you", tone: "warn",
             line: withheld
               ? "A reply was written but it did not answer the question, so it was held back."
               : plainReason(r.reason) };
  }
  return { label: "Ready", tone: "ok",
           line: r.cache_hit
             ? "Reused an earlier reply that is still correct for this period."
             : "Written from the rules in force for this period." };
}

/** The pipeline's reasons are accurate and unreadable. */
function plainReason(reason: string): string {
  const r = (reason || "").toLowerCase();
  if (r.includes("index entry") || r.includes("pointer"))
    return "The only matching article has no content in the export — just a link.";
  if (r.includes("score floor") || r.includes("nothing"))
    return "Nothing in the rules covers this question.";
  if (r.includes("not grounded"))
    return "A reply was drafted but nothing supported it, so it was held back.";
  if (r.includes("customer not found"))
    return "That customer is not in the pilot's records.";
  if (r.includes("escalat"))
    return "The rules do not cover this, and guessing is not an option.";
  return reason || "A person should handle this one.";
}

/** Split a draft so citation markers can be rendered as controls. */
export function withMarkers(draft: string): (string | number)[] {
  const out: (string | number)[] = [];
  let last = 0;
  const re = /\[(\d+)\]/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(draft))) {
    if (m.index > last) out.push(draft.slice(last, m.index));
    out.push(Number(m[1]));
    last = m.index + m[0].length;
  }
  if (last < draft.length) out.push(draft.slice(last));
  return out;
}

export function period(s: Source): string {
  if (!s.effective_from) return "no dates recorded";
  const to = s.effective_to ? ` to ${pretty(s.effective_to)}` : " onwards";
  return `applies ${pretty(s.effective_from)}${to}`;
}

/** Chunks are stored with a scaffolding prefix naming the region and the
 *  period, which the ranker needs and a reader does not. Strip it, and drop
 *  a trailing half-sentence left by the snippet cut. */
export function readable(text: string): string {
  let t = (text || "").replace(/^\[[^\]]*\]\s*/, "").trim();
  const cut = t.lastIndexOf(". ");
  if (t.length > 80 && cut > t.length * 0.5 && !t.endsWith(".")) t = t.slice(0, cut + 1);
  return t;
}

/** Topic keys read like column names. Make them read like English. */
const ACRONYMS = new Set(["pf","epf","esi","tds","gst","pt","lwf","hra","pan",
                          "uan","iban","vat","wps","ecr","kyc","sla","ctc"]);

export function heading(h: string | null): string {
  if (!h) return "";
  return h.replace(/[_-]+/g, " ").trim().split(/\s+/)
    .map((w, i) => ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase()
                 : i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w)
    .join(" ");
}

/** For a period comparison the customer's own month would be echoed back by
 *  the model, so the reply reads "for June" while answering for September.
 *  Neutralising the month asks the same question without that trap — and
 *  the card says that is what happened. */
export function neutralise(body: string): string {
  return body
    .replace(/\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b/gi, "")
    // A bare month with no year beside it ("for June payroll") survives the
    // rule above and still steers the wording, so it goes too.
    .replace(/\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b/gi, "")
    .replace(/\b\d{4}\b/g, "")
    .replace(/\s{2,}/g, " ")
    .replace(/\s+\?/, "?")
    .trim();
}

export function pretty(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const M = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][m - 1];
  return `${d} ${M} ${y}`;
}

/** Run work with a small pool so six tickets do not take six round trips
 *  end to end. The backend's quota is 20 requests a minute; four at a time
 *  stays well inside it. */
export async function pool<T>(items: T[], n: number, fn: (t: T) => Promise<void>) {
  const queue = [...items];
  await Promise.all(Array.from({ length: Math.min(n, queue.length) }, async () => {
    while (queue.length) await fn(queue.shift() as T);
  }));
}


/** Did every passage this reply cites actually apply on the date asked about?
 *  The console checks this itself rather than trusting the answer: the window
 *  comes back with each source, so a stale citation is visible here even if
 *  nothing upstream caught it. This is the brief's headline number -- stale
 *  citations after the effective date, 48 a quarter today, target zero. */
export function stale(r: Result, asOf: string): Source[] {
  return (r.sources || []).filter((s) => {
    if (s.effective_from && asOf < s.effective_from) return true;
    if (s.effective_to && asOf >= s.effective_to) return true;
    return false;
  });
}

export type Score = { cited: string; stale: string; cost: string; alone: string };

/** The four day-one numbers from the brief, computed from what is on screen. */
export function score(rows: Row[]): Score | null {
  const done = rows.filter((r) => r.state === "done" && r.result);
  if (!done.length) return null;
  const answered = done.filter((r) => r.result!.draft);
  const withCite = answered.filter((r) => (r.result!.citations || []).length);
  const staleN = done.filter((r) => stale(r.result!, r.asOf).length).length;
  const spend = done.reduce((a, r) => a + (r.result!.usd || 0), 0) / done.length;
  const alone = done.filter((r) => r.result!.route !== "human"
                                && !r.result!.needs_approval).length;
  return {
    cited: answered.length ? `${Math.round(100 * withCite.length / answered.length)}%` : "—",
    stale: String(staleN),
    cost: `$${spend.toFixed(5)}`,
    alone: `${Math.round(100 * alone / done.length)}%`,
  };
}
