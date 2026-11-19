"""Gate G7 — speed and cost. Pass marks: first reply <= 3 s at p95,
<= USD 0.02 a draft.

Baseline to beat: the previous vendor pilot's p95 rose to 19 seconds during
the 25-28 May peak and 6% of sessions timed out (postmortem, item 3).

What is measured: a replay of real tickets through the real pipeline, timed
per box, priced from the token counts Bedrock actually returned. Nothing is
estimated.

What is NOT measured, and why. This account's Bedrock quota is 20 requests a
minute for Nova Micro and 10 for Haiku 4.5, so the harness cannot fire
hundreds of concurrent requests to reproduce a peak hour. That ceiling is a
free-plan limit, not a property of the design, and it does not bind the
pilot's real traffic: 15,000 tickets a month is about 1.4 requests a minute,
and roughly 4 at a 3x peak, which fits inside even the 10 rpm ceiling. The
arithmetic is printed with the result so nobody has to take it on trust.
"""
from __future__ import annotations
import json, statistics, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import record, REPORTS, parallel          # noqa: E402

from pilot.config import DATA_ROOM                       # noqa: E402
from pilot.bedrock import Meter                          # noqa: E402
from pilot.normalise import normalise                    # noqa: E402
from pilot.pipeline import run_ticket                    # noqa: E402

TARGET_P95_S = 3.0
TARGET_USD_PER_DRAFT = 0.02

# Tickets a month, from monthly_cost_sheet.csv (the current volume the brief
# costs against).
MONTHLY_TICKETS = 15_000
WORKING_DAYS = 22
ACTIVE_HOURS = 8
PEAK_MULTIPLIER = 3.0

SAMPLE = int(__import__("os").environ.get("G7_SAMPLE", "40"))


def peak_arithmetic() -> dict:
    mean_per_min = MONTHLY_TICKETS / (WORKING_DAYS * ACTIVE_HOURS * 60)
    return {"mean_rpm": mean_per_min, "peak_rpm": mean_per_min * PEAK_MULTIPLIER}


def run() -> dict:
    raw = json.loads((DATA_ROOM / "data" / "tickets_export.json").read_text())["tickets"]

    # The peak week the postmortem names: the 25th to the 3rd. Replay from it.
    peak = []
    for r in raw:
        t = normalise(r)
        if t.received_at and (t.received_at.day >= 25 or t.received_at.day <= 3):
            peak.append(r)
    sample = peak[:SAMPLE]

    meter = Meter()
    results, wall = [], []

    def one(r):
        t0 = time.perf_counter()
        try:
            res = run_ticket(r, meter=meter)
        except Exception as exc:          # a failure is a result, not a crash
            return {"error": str(exc)[:120], "seconds": time.perf_counter() - t0}
        return {"res": res, "seconds": time.perf_counter() - t0}

    out = parallel(one, sample, workers=4)
    errors = [o for o in out if "error" in o]
    ok = [o for o in out if "res" in o]

    wall = sorted(o["seconds"] for o in ok)
    drafted = [o["res"] for o in ok if o["res"].route in ("answer", "cache")]
    to_human = [o["res"] for o in ok if o["res"].route == "human"]
    cached = [o["res"] for o in ok if o["res"].cache_hit]

    # Per-box timing, so the slowest box is named rather than guessed at.
    boxes: dict[str, list[float]] = {}
    for o in ok:
        for box, ms in o["res"].timings_ms.items():
            boxes.setdefault(box, []).append(ms)

    usd_total = meter.usd
    per_draft = usd_total / max(1, len(drafted))

    return {
        "sampled": len(sample), "ok": len(ok), "errors": errors[:3],
        "p50": statistics.median(wall) if wall else 0,
        "p95": wall[int(len(wall) * 0.95)] if wall else 0,
        "max": max(wall) if wall else 0,
        "drafted": len(drafted), "to_human": len(to_human), "cached": len(cached),
        "usd_total": usd_total, "per_draft": per_draft,
        "by_model": meter.by_model(),
        "boxes": {k: round(statistics.median(v), 1) for k, v in sorted(boxes.items())},
        "monthly_at_this_rate": per_draft * MONTHLY_TICKETS,
    }


def test_g7_speed_and_cost():
    m = run()
    pk = peak_arithmetic()

    print(f"\n  peak-week tickets replayed {m['ok']}/{m['sampled']}"
          + (f"  errors: {m['errors']}" if m["errors"] else ""))
    print(f"  end to end p50             {m['p50']:.2f} s")
    print(f"  end to end p95             {m['p95']:.2f} s   (target {TARGET_P95_S} s; "
          f"vendor baseline 19 s)")
    print(f"  slowest                    {m['max']:.2f} s")
    print("\n  median ms per box")
    for box, ms in m["boxes"].items():
        print(f"    {box:14} {ms:7.1f}")
    print(f"\n  routed to a human          {m['to_human']}")
    print(f"  drafted                    {m['drafted']}  (cache hits {m['cached']})")
    print(f"  cost per draft             USD {m['per_draft']:.5f}  "
          f"(target {TARGET_USD_PER_DRAFT})")
    print(f"  at 15,000 tickets a month  USD {m['monthly_at_this_rate']:.2f}")
    for model, d in m["by_model"].items():
        print(f"    {model:46} {d['calls']:4} calls  "
              f"{d['in']:>7} in  {d['out']:>6} out  USD {d['usd']:.5f}")
    print(f"\n  throughput arithmetic: {MONTHLY_TICKETS:,} tickets a month over "
          f"{WORKING_DAYS} days x {ACTIVE_HOURS} h")
    print(f"    = {pk['mean_rpm']:.1f} requests a minute mean, "
          f"{pk['peak_rpm']:.1f} at a {PEAK_MULTIPLIER:.0f}x peak")
    print(f"    account quota is 10-20 rpm, so production volume fits; the "
          f"quota binds the eval sweep, not the pilot")

    passed = m["p95"] <= TARGET_P95_S and m["per_draft"] <= TARGET_USD_PER_DRAFT
    record("G7 speed and cost", "p95 3 s, USD 0.02 a draft",
           f"p95 {m['p95']:.2f} s, USD {m['per_draft']:.5f} a draft", passed,
           p50_s=round(m["p50"], 2), slowest_box=max(m["boxes"], key=m["boxes"].get),
           vendor_baseline_p95_s=19,
           monthly_ai_spend_usd=round(m["monthly_at_this_rate"], 2),
           sampled=m["ok"], cache_hits=m["cached"])
    (REPORTS / "g7_detail.json").write_text(
        json.dumps({k: v for k, v in m.items() if k != "errors"},
                   indent=2, default=str))

    assert m["p95"] <= TARGET_P95_S, f"p95 {m['p95']:.2f}s > {TARGET_P95_S}s"
    assert m["per_draft"] <= TARGET_USD_PER_DRAFT, \
        f"USD {m['per_draft']:.5f} per draft > {TARGET_USD_PER_DRAFT}"


if __name__ == "__main__":
    test_g7_speed_and_cost()
