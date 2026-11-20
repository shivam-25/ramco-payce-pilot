"""Measure what a customer actually waits for, and attribute it.

G7's target is a first reply inside 3 s at p95. The gate's own harness cannot
measure that, because it runs against a free-plan Bedrock quota with a rate
limiter in the way, so its wall-clock is the limiter's sleep rather than the
work. This measures the deployed thing instead: real requests, sequentially,
at production pace, cache off so every request does the full job.

It reports three numbers, not one:

  wall      what the caller waited
  pipeline  the sum of the boxes' own timings, which the runtime returns
  overhead  wall minus pipeline: everything spent reaching the code

The split is the whole point. The first measurement of this said 1.28 s of
pipeline inside 5.50 s of wall, and chasing the pipeline would have been a
waste of a day.

Two modes:

  direct   invoke AgentCore with boto3, so the session id is controllable.
           `--arms` runs the same requests twice, once with a fresh session
           id per request and once reusing one, which is what attributes the
           overhead to session cold starts rather than to AgentCore.

  url      POST to the public Function URL, which is what the console does
           and therefore the number the gate should quote.

Usage:
  python scripts/latency.py direct --arms -n 8
  python scripts/latency.py url -n 12
"""
from __future__ import annotations
import argparse, json, os, statistics, sys, time, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "evals" / "reports"

# The console's own demo tickets. Deliberately not drawn from the data room:
# this script's output is committed, and no case data belongs in it. Three
# that reach the drafter, so the measurement covers the full path rather than
# the cheap early returns.
TICKETS = [
    {"subject": "EPF wage ceiling for June payroll",
     "body": "What EPF wage ceiling applies to our June 2026 payroll run?",
     "customer_id": "C-1001", "as_of": "2026-06-15", "use_cache": False},
    {"subject": "EPF wage ceiling for September payroll",
     "body": "What EPF wage ceiling applies to our September 2026 payroll run?",
     "customer_id": "C-1001", "as_of": "2026-09-15", "use_cache": False},
    {"subject": "How often are KB articles reviewed?",
     "body": "How often must knowledge base articles be reviewed?",
     "customer_id": "C-1001", "as_of": "2026-09-15", "use_cache": False},
]


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))] if xs else 0.0


def summarise(rows: list[dict]) -> dict:
    wall = [r["wall"] for r in rows]
    pipe = [r["pipeline"] for r in rows]
    over = [r["wall"] - r["pipeline"] for r in rows]
    return {
        "n": len(rows),
        "wall_p50": round(statistics.median(wall), 2) if wall else 0,
        "wall_p95": round(pct(wall, 0.95), 2),
        "pipeline_p50": round(statistics.median(pipe), 2) if pipe else 0,
        "pipeline_p95": round(pct(pipe, 0.95), 2),
        "overhead_p50": round(statistics.median(over), 2) if over else 0,
        "overhead_p95": round(pct(over, 0.95), 2),
    }


def _pipeline_s(body: dict) -> float:
    """The boxes' own timings, as the runtime reported them."""
    return sum((body.get("timings_ms") or {}).values()) / 1000.0


def run_direct(n: int, reuse: bool, pace: float) -> list[dict]:
    import boto3
    arn = os.environ["RUNTIME_ARN"]
    client = boto3.client("bedrock-agentcore")
    fixed = str(uuid.uuid4())
    rows = []
    for i in range(n):
        t = TICKETS[i % len(TICKETS)]
        sid = fixed if reuse else str(uuid.uuid4())
        t0 = time.perf_counter()
        r = client.invoke_agent_runtime(
            agentRuntimeArn=arn, qualifier="DEFAULT", runtimeSessionId=sid,
            contentType="application/json", payload=json.dumps(t).encode())
        body = json.loads(r["response"].read())
        wall = time.perf_counter() - t0
        rows.append({"wall": wall, "pipeline": _pipeline_s(body),
                     "route": body.get("route", "?")})
        print(f"    {i+1:>2}/{n}  wall {wall:5.2f}s  "
              f"pipeline {rows[-1]['pipeline']:5.2f}s  {rows[-1]['route']}",
              flush=True)
        if i + 1 < n:
            time.sleep(pace)
    return rows


def run_url(n: int, pace: float) -> list[dict]:
    import urllib.request
    url = os.environ["PILOT_API_URL"]
    key = os.environ["PILOT_API_KEY"]
    rows = []
    for i in range(n):
        t = TICKETS[i % len(TICKETS)]
        req = urllib.request.Request(
            url, data=json.dumps(t).encode(), method="POST",
            headers={"content-type": "application/json", "x-pilot-key": key})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())
        wall = time.perf_counter() - t0
        rows.append({"wall": wall, "pipeline": _pipeline_s(body),
                     "route": body.get("route", "?")})
        print(f"    {i+1:>2}/{n}  wall {wall:5.2f}s  "
              f"pipeline {rows[-1]['pipeline']:5.2f}s  {rows[-1]['route']}",
              flush=True)
        if i + 1 < n:
            time.sleep(pace)
    return rows


def show(label: str, m: dict) -> None:
    print(f"\n  {label}  (n={m['n']})")
    print(f"    wall      p50 {m['wall_p50']:5.2f}s   p95 {m['wall_p95']:5.2f}s")
    print(f"    pipeline  p50 {m['pipeline_p50']:5.2f}s   p95 {m['pipeline_p95']:5.2f}s")
    print(f"    overhead  p50 {m['overhead_p50']:5.2f}s   p95 {m['overhead_p95']:5.2f}s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["direct", "url"])
    ap.add_argument("-n", type=int, default=8)
    ap.add_argument("--pace", type=float, default=2.0,
                    help="seconds between requests; production is 1.4 rpm, so "
                         "anything above ~1s does not queue")
    ap.add_argument("--arms", action="store_true",
                    help="direct mode: compare a fresh session id per request "
                         "against reusing one")
    ap.add_argument("--out", default=None, help="write JSON here")
    a = ap.parse_args()

    out: dict = {"mode": a.mode, "pace_s": a.pace,
                 "method": "sequential, cache disabled, production pace"}

    if a.mode == "url":
        print("\n  public Function URL, what the console calls")
        out["url"] = summarise(run_url(a.n, a.pace))
        show("public endpoint", out["url"])
    elif a.arms:
        print("\n  arm A: a fresh AgentCore session id per request")
        a_rows = run_direct(a.n, reuse=False, pace=a.pace)
        print("\n  arm B: one AgentCore session id, reused")
        b_rows = run_direct(a.n, reuse=True, pace=a.pace)
        out["fresh_session"] = summarise(a_rows)
        out["reused_session"] = summarise(b_rows)
        show("fresh session id per request", out["fresh_session"])
        show("one session id, reused", out["reused_session"])
        d = out["fresh_session"]["wall_p50"] - out["reused_session"]["wall_p50"]
        print(f"\n    session reuse saves {d:.2f}s at p50")
        out["saved_p50_s"] = round(d, 2)
    else:
        print("\n  direct AgentCore invoke")
        out["direct"] = summarise(run_direct(a.n, reuse=False, pace=a.pace))
        show("direct", out["direct"])

    if a.out:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=1))
        print(f"\n  wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
