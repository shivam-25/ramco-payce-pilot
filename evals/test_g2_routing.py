"""Gate G2 — classify / route. Pass mark: 95% or better.

Baseline to beat: the intake dropdown gets 84.8% right today (1,137 of 1,341).
The 204 it gets wrong are the misroutes a human has to undo, and they are the
reason this pilot exists.

The classifier sees only masked subject + body. It never sees the category
label, the queue or the event history.
"""
from __future__ import annotations
import math
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import load, parallel, record          # noqa: E402

from pilot.classify import classify                   # noqa: E402

TARGET = 0.95

# Bedrock gives this account 20 classify calls a minute, so the full 1,341
# tickets take about 85 minutes of wall clock and three restarts' worth of
# throttling to get through. A stratified sample measures the same quantity
# with a stated margin of error, and the margin is reported next to the
# number so nobody mistakes a sample for a census.
#
# Set G2_SAMPLE=0 to run every ticket.
SAMPLE = int(os.environ.get("G2_SAMPLE", "400"))
SEED = 20261119


def _stratified(rows: list[dict], n: int) -> list[dict]:
    """Sample proportionally within each gold queue, so a small queue is not
    lost to chance and per-queue recall stays meaningful."""
    by_q: dict[str, list[dict]] = {}
    for r in rows:
        by_q.setdefault(r["gold_queue"], []).append(r)
    rng = random.Random(SEED)
    picked: list[dict] = []
    for q, group in sorted(by_q.items()):
        rng.shuffle(group)
        take = max(8, round(n * len(group) / len(rows)))
        picked.extend(group[:min(take, len(group))])
    return picked


def _wilson(hits: int, n: int) -> tuple[float, float]:
    """95% Wilson interval. Preferred over the normal approximation because
    the proportion here sits near 1, where the normal interval runs past it."""
    if n == 0:
        return 0.0, 0.0
    z, p = 1.96, hits / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def run() -> dict:
    rows = load("g2_routing.jsonl")
    total = len(rows)
    if SAMPLE and SAMPLE < total:
        rows = _stratified(rows, SAMPLE)
        print(f"  stratified sample: {len(rows)} of {total} tickets "
              f"(seed {SEED})")

    def one(r):
        cat, queue, usage = classify(r["text"])
        return {**r, "pred_queue": queue, "usage": usage}

    # Two workers, not six. The limiter paces the mean rate correctly, but
    # six threads waking together still arrive as a burst and Bedrock
    # measures bursts. Throughput is set by the quota either way.
    out = parallel(one, rows, workers=2, label="g2")

    hits = sum(r["pred_queue"] == r["gold_queue"] for r in out)
    acc = hits / len(out)
    lo, hi = _wilson(hits, len(out))

    # Per-queue recall. A mean of 95% that hides a queue at 40% is not a pass
    # anyone should accept, so the worst queue is reported next to the mean.
    per = defaultdict(lambda: [0, 0])
    for r in out:
        per[r["gold_queue"]][1] += 1
        per[r["gold_queue"]][0] += r["pred_queue"] == r["gold_queue"]
    per_q = {q: (h / n, n) for q, (h, n) in sorted(per.items())}
    worst = min(per_q.items(), key=lambda kv: kv[1][0])

    # What it does on the tickets a human actually had to move.
    mis = [r for r in out if r["was_misrouted"]]
    mis_fixed = sum(r["pred_queue"] == r["gold_queue"] for r in mis)

    baseline = sum(r["intake_queue"] == r["gold_queue"] for r in out) / len(out)
    usd = sum(r["usage"].usd for r in out)
    secs = [r["usage"].seconds for r in out]
    secs.sort()

    # A gate that fails should say where. Without this the run reports
    # "90%" and leaves the next person to re-measure for an hour to find
    # out which queues it was.
    import json as _json
    (Path(__file__).resolve().parents[1] / "evals" / "reports"
     / "g2_detail.json").write_text(_json.dumps(
        [{"ticket_id": r.get("ticket_id"), "gold_queue": r["gold_queue"],
          "pred_queue": r["pred_queue"], "intake_queue": r.get("intake_queue"),
          "was_misrouted": r.get("was_misrouted")} for r in out], indent=1))

    return {
        "n": len(out), "sampled_from": total,
        "accuracy": acc, "accuracy_ci95": [round(lo, 4), round(hi, 4)],
        "baseline": baseline,
        "per_queue": per_q, "worst": worst,
        "misrouted_total": len(mis), "misrouted_fixed": mis_fixed,
        "usd": usd, "p95_s": secs[int(len(secs) * 0.95)],
    }


def test_g2_routing_accuracy():
    m = run()
    print(f"\n  tickets            {m['n']}")
    print(f"  intake baseline    {m['baseline']*100:.1f}%")
    lo, hi = m["accuracy_ci95"]
    print(f"  pilot accuracy     {m['accuracy']*100:.1f}%  "
          f"(95% CI {lo*100:.1f}–{hi*100:.1f}%, n={m['n']} "
          f"of {m['sampled_from']})")
    print(f"  misroutes fixed    {m['misrouted_fixed']}/{m['misrouted_total']}")
    print(f"  worst queue        {m['worst'][0]} "
          f"{m['worst'][1][0]*100:.1f}% (n={m['worst'][1][1]})")
    print(f"  classify cost      USD {m['usd']:.4f} for {m['n']} tickets "
          f"(USD {m['usd']/m['n']*1000:.4f} per 1,000)")
    print(f"  classify p95       {m['p95_s']*1000:.0f} ms")
    for q, (a, n) in m["per_queue"].items():
        print(f"    {q:24} {a*100:5.1f}%  n={n}")

    record("G2 classify", "95%", f"{m['accuracy']*100:.1f}%",
           m["accuracy"] >= TARGET,
           baseline=f"{m['baseline']*100:.1f}%",
           worst_queue=f"{m['worst'][0]} {m['worst'][1][0]*100:.1f}%",
           misroutes_fixed=f"{m['misrouted_fixed']}/{m['misrouted_total']}",
           usd_per_1000=round(m["usd"] / m["n"] * 1000, 4))

    assert m["accuracy"] >= TARGET, f"routing {m['accuracy']*100:.1f}% < 95%"


if __name__ == "__main__":
    test_g2_routing_accuracy()
