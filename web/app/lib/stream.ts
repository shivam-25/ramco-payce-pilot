import type { Result, Stage } from "./types";

/** What the console needs to know while a ticket is in flight. */
export type Handlers = {
  onStage: (s: Stage) => void;
  onToken: (text: string) => void;
  onDraftBegin: () => void;
};

type Sent = { subject: string; body: string; customer: string; asOf: string };

// One socket for the page, not one per ticket. API Gateway bills a connection
// by the minute and a handshake costs the best part of a second, so opening a
// fresh one per ticket would show up as latency in exactly the demo this is
// meant to make look fast. The live feed sends a ticket every few seconds
// down this same socket.
let sock: WebSocket | null = null;
let opening: Promise<WebSocket> | null = null;

function socket(url: string): Promise<WebSocket> {
  if (sock && sock.readyState === WebSocket.OPEN) return Promise.resolve(sock);
  if (opening) return opening;
  opening = new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    const giveUp = setTimeout(() => {
      try { ws.close(); } catch { /* already gone */ }
      reject(new Error("websocket did not open"));
    }, 8000);
    ws.onopen = () => { clearTimeout(giveUp); sock = ws; opening = null; resolve(ws); };
    ws.onerror = () => { clearTimeout(giveUp); opening = null; reject(new Error("websocket failed")); };
    ws.onclose = () => { if (sock === ws) sock = null; };
  });
  return opening;
}

/** Run one ticket over the socket. Rejects if the socket cannot carry it, so
 *  the caller can fall back to the buffered endpoint. */
export function streamTicket(url: string, key: string, row: Sent,
                             useCache: boolean, h: Handlers): Promise<Result> {
  return socket(url).then((ws) => new Promise<Result>((resolve, reject) => {
    // Every frame carries the id we sent, and we ignore the ones that are
    // not ours. This file previously claimed "the console serialises its
    // runs" and it does not -- "Draft all replies" runs four at a time --
    // so replies landed on whichever row happened to be listening. A money
    // ticket showed another ticket's drafted answer and the not-in-corpus
    // one showed an injection refusal: wrong, and plausible enough to be
    // believed.
    const rid = `r${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
    const onMsg = (e: MessageEvent) => {
      let ev: Record<string, unknown>;
      try { ev = JSON.parse(String(e.data)); } catch { return; }
      if (ev.rid !== rid) return;
      switch (ev.event) {
        case "stage":
          h.onStage({ box: String(ev.box ?? ""),
                      ms: typeof ev.ms === "number" ? ev.ms : null,
                      note: String(ev.note ?? "") });
          break;
        case "draft_begin": h.onDraftBegin(); break;
        case "token": h.onToken(String(ev.text ?? "")); break;
        case "result":
          ws.removeEventListener("message", onMsg);
          resolve(ev.result as Result);
          break;
        case "error":
          ws.removeEventListener("message", onMsg);
          reject(new Error(String(ev.error ?? "stream failed")));
          break;
      }
    };
    ws.addEventListener("message", onMsg);
    ws.send(JSON.stringify({
      key, rid, subject: row.subject, body: row.body,
      customer_id: row.customer, as_of: row.asOf, use_cache: useCache,
    }));
  }));
}
