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
  asOf: string; note: string;
  state: "idle" | "running" | "done" | "failed";
  result?: Result;
};

/** What a reader needs to decide whether to trust the reply. */
export function verdict(r: Result): { label: string; tone: string; why: string } {
  if (r.error) return { label: "Failed", tone: "bad", why: r.error };
  if (r.route === "human" && r.reason.includes("injection"))
    return { label: "Refused", tone: "bad", why: "Prompt injection, caught before any model ran" };
  if (r.needs_approval)
    return { label: "Needs approval", tone: "warn", why: r.reason };
  if (r.route === "human")
    return { label: "Sent to a person", tone: "warn", why: r.reason };
  if (r.route === "cache" || r.route === "answer")
    return { label: "Ready to send", tone: "ok", why: r.cache_hit
      ? "Answered from a previous reply that is still in force"
      : "Answered from the rules in force on the date asked about" };
  return { label: r.route, tone: "warn", why: r.reason };
}

/** Citations become clickable markers so a reader can check the source. */
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
