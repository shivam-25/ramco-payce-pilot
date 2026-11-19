"""Gate G8 — the cache may not cross a policy change.

This gate exists because the cache broke the pilot's central promise and
nothing caught it. "Which SIF version this month", asked in September,
matched the same sentence asked in June at 0.958 similarity and was served
June's answer. Region was part of the cache key; time was not.

The property being measured is not "the cache works". It is that a cache hit
can only serve a date for which the cached answer was true. The two are
different, and only the second one is worth a gate.

The second half matters as much as the first: an entry must still be reused
within its own window, or the fix would have been to disable caching, which
costs the pilot its 3-second peak-week target.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import record                             # noqa: E402

from pilot.api import Invocation, invocations            # noqa: E402

# Each case is the SAME question at two dates that straddle a real policy
# change in the data room, with the answer each date must and must not give.
CASES = [
    {
        "question": "Which WPS SIF file version should we send to the bank this month?",
        "customer_id": "C-1003",
        "change": "2026-07-15",
        "before": {"as_of": "2026-06-20", "must": "v2", "must_not": "v3"},
        "after": {"as_of": "2026-09-20", "must": "3", "must_not": "v2"},
    },
    {
        "question": "What EPF wage ceiling applies to our payroll this month?",
        "customer_id": "C-1001",
        "change": "2026-08-01",
        "before": {"as_of": "2026-06-15", "must": "15,000", "must_not": "25,000"},
        "after": {"as_of": "2026-09-15", "must": "25,000", "must_not": "15,000"},
    },
]


def _ask(case, side):
    r = invocations(Invocation(subject=case["question"][:40],
                               body=case["question"],
                               customer_id=case["customer_id"],
                               as_of=side["as_of"]))
    return r["route"], r["draft"] or ""


def run() -> dict:
    wrong_period = []
    reused = 0
    expected_reuse = 0

    for case in CASES:
        for name in ("before", "after"):
            side = case[name]
            # First ask: populates the cache. Second ask: must hit it, and
            # must still be the right period's answer.
            for attempt in (1, 2):
                route, draft = _ask(case, side)
                ok = side["must"] in draft and side["must_not"] not in draft
                if not ok:
                    wrong_period.append({
                        "question": case["question"][:48], "side": name,
                        "as_of": side["as_of"], "attempt": attempt,
                        "route": route, "draft": draft[:120],
                    })
                if attempt == 2:
                    expected_reuse += 1
                    if route == "cache":
                        reused += 1
                print(f"    {side['as_of']}  ask {attempt}  route={route:6} "
                      f"{'ok' if ok else 'WRONG PERIOD'}")

    return {
        "cases": len(CASES) * 2 * 2,
        "wrong_period": wrong_period,
        "cache_reused": reused,
        "cache_expected": expected_reuse,
    }


def test_g8_cache_respects_effective_dates():
    m = run()
    print(f"\n  asks                   {m['cases']}")
    print(f"  wrong-period answers   {len(m['wrong_period'])}")
    print(f"  second asks served     {m['cache_reused']}/{m['cache_expected']} "
          f"from cache")
    for w in m["wrong_period"]:
        print(f"    {w['as_of']} {w['side']}: {w['draft']}")

    passed = not m["wrong_period"] and m["cache_reused"] == m["cache_expected"]
    record("G8 cache", "no cache hit may serve a date it was not true for",
           f"{len(m['wrong_period'])} wrong-period answers, "
           f"{m['cache_reused']}/{m['cache_expected']} reused within window",
           passed)

    assert not m["wrong_period"], f"cache served a stale period: {m['wrong_period']}"
    assert m["cache_reused"] == m["cache_expected"], (
        "entries are not being reused inside their own window; the cache is "
        "now correct but useless")


if __name__ == "__main__":
    test_g8_cache_respects_effective_dates()
