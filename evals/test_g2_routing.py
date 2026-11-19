"""Gate G2 — classify / route. Pass mark: 95% or better.

Baseline to beat: the intake dropdown gets 84.8% right today (1,137 of 1,341).
The 204 it gets wrong are the misroutes a human has to undo, and they are the
reason this pilot exists.

The classifier sees only masked subject + body. It never sees the category
label, the queue or the event history.
"""
from __future__ import annotations
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import load, parallel, record          # noqa: E402

from pilot.classify import classify                   # noqa: E402

TARGET = 0.95


def run() -> dict:
    rows = load("g2_routing.jsonl")

    def one(r):
        cat, queue, usage = classify(r["text"])
        return {**r, "pred_queue": queue, "usage": usage}

    out = parallel(one, rows)

    hits = sum(r["pred_queue"] == r["gold_queue"] for r in out)
    acc = hits / len(out)

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

    return {
        "n": len(out), "accuracy": acc, "baseline": baseline,
        "per_queue": per_q, "worst": worst,
        "misrouted_total": len(mis), "misrouted_fixed": mis_fixed,
        "usd": usd, "p95_s": secs[int(len(secs) * 0.95)],
    }


def test_g2_routing_accuracy():
    m = run()
    print(f"\n  tickets            {m['n']}")
    print(f"  intake baseline    {m['baseline']*100:.1f}%")
    print(f"  pilot accuracy     {m['accuracy']*100:.1f}%")
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
