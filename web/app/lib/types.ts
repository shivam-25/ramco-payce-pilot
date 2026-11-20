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
  needs_approval: boolean;
  // What the pipeline actually sends: {passed, failures, detail}. It was
  // typed Record<string,string>, which made `passed` a string and quietly
  // allowed the code reading it to be wrong.
  output_check: { passed?: boolean; failures?: string[];
                  detail?: Record<string, unknown> };
  pii_counts: Record<string, number>; cache_hit: boolean;
  timings_ms: Record<string, number>; usd: number;
  sla_hours: [number, number] | null;
  error?: string;
};

/** One box closing, as it closes. `ms` is the box's own time, not elapsed. */
export type Stage = { box: string; ms: number | null; note: string };

export type Row = {
  key: string; subject: string; body: string; customer: string;
  asOf: string; note: string; dated?: boolean;
  state: "idle" | "running" | "done" | "failed";
  result?: Result;
  compare?: { asOf: string; result?: Result; state: "running" | "done" };
  // Only set while a run is streaming. `live` is the draft as it is being
  // written, which has NOT been through the output check yet -- the screen
  // has to say so, because a draft that fails C1-C9 is withheld and this
  // text is then replaced by the reason.
  stages?: Stage[];
  live?: string;
  streamed?: boolean;
};

export type Status = { label: string; tone: "ok" | "warn" | "bad"; line: string };

/** One word for the state, one sentence for why — in the words a support
 *  lead would use, not the pipeline's. */
export function status(r: Result): Status {
  if (r.error) return { label: "Failed", tone: "bad", line: r.error };
  if (r.reason?.includes("injection"))
    return { label: "Refused", tone: "bad",
             line: "This ticket tried to give the assistant instructions. Nothing was run." };
  // `needs_approval` means "a person must sign this off", which the pipeline
  // sets on EVERY hand-off: money, yes, but also a model that declined, a
  // draft nothing supported, and a draft the output check withheld. Reading
  // it as a synonym for money captioned all four "Money is involved", and
  // put that sentence under a ticket asking for a contribution rate. It also
  // made the whole branch below unreachable, so the plain-language reasons
  // written for those cases had never once been shown.
  //
  // The money hand-off is the one the router names, so test for that.
  if (r.needs_approval && /money/i.test(r.reason || ""))
    return { label: "Needs you", tone: "warn",
             line: "Money is involved, so a person decides." };
  if (r.route === "human") {
    // The output check ran and failed — a reply exists but was held back.
    // Presence of the object is not enough: it is also present on replies
    // that passed.
    const withheld = r.output_check?.passed === false;
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
  // "model escalated: The passages do not provide information on how to
  // export last year's YTD figures" -- the tail of that is the single most
  // useful sentence on the screen for whoever picks the ticket up, because
  // it says exactly what was missing. Showing the generic line instead threw
  // it away. Strip the pipeline's prefix and the "passages" vocabulary, and
  // hand the rest over.
  const said = /^model escalated:\s*(.+)$/is.exec(reason || "");
  if (said) {
    const why = said[1].trim()
      .replace(/^the (?:retrieved )?passages? (?:do|does) not (?:provide|contain) (?:any )?(?:information|details?) (?:on|about|regarding)\s*/i,
               "Nothing on file covers ")
      .replace(/^the (?:retrieved )?passages? (?:do|does) not (?:cover|mention|address)\s*/i,
               "Nothing on file covers ")
      .replace(/\bis not covered by the (?:retrieved )?passages?\b/i,
               "is not covered by anything on file")
      .replace(/\bthe (?:retrieved )?passages?\b/gi, "the rules on file");
    return why.charAt(0).toUpperCase() + why.slice(1) +
           (/[.!?]$/.test(why) ? "" : ".");
  }
  if (r.includes("escalat"))
    return "The rules retrieved do not cover this question, so nothing was drafted.";
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

export type Score = {
  cited: string; stale: string; cost: string; alone: string;
  // Counts as well as rates. Watching a feed arrive, "14 drafted, 6 to you,
  // 2 refused" says more about what the desk is in for than four
  // percentages, and a percentage over three tickets is noise in a suit.
  seen: number; drafted: number; toPerson: number; refused: number;
  spend: string;
};

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
  const refused = done.filter((r) => /injection/i.test(r.result!.reason || "")).length;
  return {
    cited: answered.length ? `${Math.round(100 * withCite.length / answered.length)}%` : "—",
    stale: String(staleN),
    cost: `$${spend.toFixed(5)}`,
    alone: `${Math.round(100 * alone / done.length)}%`,
    seen: done.length,
    drafted: alone,
    toPerson: done.length - alone,
    refused,
    spend: `$${done.reduce((a, r) => a + (r.result!.usd || 0), 0).toFixed(4)}`,
  };
}


/** Which pile a ticket belongs in. The same test the tally counts with, so
 *  clicking "9 to you" shows exactly the nine it counted -- a filter that
 *  disagrees with the number above it is worse than no filter. */
export type Bucket = "ready" | "person" | "refused" | "waiting";

export function bucket(r: Row): Bucket {
  if (r.state !== "done" || !r.result) return "waiting";
  if (/injection/i.test(r.result.reason || "")) return "refused";
  return r.result.route !== "human" && !r.result.needs_approval
    ? "ready" : "person";
}

/** Plain substring search over everything the agent can see: what the
 *  customer wrote, who they are, and the reply if there is one. Not fuzzy
 *  and not ranked -- at desk scale that is a cost with no benefit, and a
 *  search that quietly reorders results is hard to trust. */
export function matches(r: Row, q: string): boolean {
  const n = q.trim().toLowerCase();
  if (!n) return true;
  return [r.subject, r.body, r.customer, r.result?.draft,
          r.result?.reason].some((f) => (f || "").toLowerCase().includes(n));
}
