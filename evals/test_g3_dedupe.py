"""Gate G3 — dedupe. Pass mark: one client, one case.

Both directions. Duplicates must merge; two genuinely different issues from
the same customer on the same day must NOT. A deduper that merges everything
scores perfectly on the first test and loses a customer's second problem.
"""
from __future__ import annotations
import json, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import load, record                     # noqa: E402

from pilot.config import DATA_ROOM                     # noqa: E402
from pilot.normalise import normalise                  # noqa: E402
from pilot.dedupe import assign_cases, summarise, midnight_splits  # noqa: E402


def run() -> dict:
    tickets = [normalise(r) for r in json.loads(
        (DATA_ROOM / "data" / "tickets_export.json").read_text())["tickets"]]
    cases = assign_cases(tickets)
    rows = load("g3_dedupe.jsonl")

    merged_ok = merged_bad = 0
    for r in rows:
        if r["kind"] != "duplicate_cluster":
            continue
        ids = [t for t in r["ticket_ids"] if t in cases]
        if not ids:
            continue
        if len({cases[t] for t in ids}) == 1:
            merged_ok += 1
        else:
            merged_bad += 1

    # Negatives: different categories, same customer, same day -> distinct cases
    by_ticket = {t.ticket_id: t for t in tickets}
    split_ok = split_bad = 0
    for r in rows:
        if r["kind"] != "distinct_issues":
            continue
        same_day = [t for t in tickets
                    if t.customer_id == r["customer_id"] and t.received_at
                    and str(t.received_at.date()) == r["day"]]
        from pilot.taxonomy import canonical_category
        by_cat = defaultdict(set)
        for t in same_day:
            c = canonical_category(t.raw_category)
            if c and t.ticket_id in cases:
                by_cat[c].add(cases[t.ticket_id])
        case_ids = {cid for s in by_cat.values() for cid in s}
        if len(case_ids) >= len(by_cat) and len(by_cat) > 1:
            split_ok += 1
        elif len(by_cat) > 1:
            split_bad += 1

    return {"summary": summarise(tickets, cases),
            "merged_ok": merged_ok, "merged_bad": merged_bad,
            "split_ok": split_ok, "split_bad": split_bad,
            "midnight": midnight_splits(tickets, cases)}


def test_g3_dedupe():
    m = run(); s = m["summary"]
    print(f"\n  tickets                {s['tickets']}")
    print(f"  cases after dedupe     {s['cases']}")
    print(f"  clusters merged        {s['clusters']} "
          f"covering {s['tickets_in_clusters']} tickets")
    print(f"  ticket->case reduction {s['reduction']*100:.1f}%")
    print(f"  duplicate clusters collapsed to one case  {m['merged_ok']} "
          f"(failed {m['merged_bad']})")
    print(f"  distinct issues kept apart                {m['split_ok']} "
          f"(wrongly merged {m['split_bad']})")
    print(f"  cost of the calendar-day rule: {m['midnight']} continuations "
          f"across midnight became a second case")

    passed = m["merged_bad"] == 0 and m["split_bad"] == 0
    record("G3 dedupe", "one client = one case",
           f"{m['merged_ok']} clusters merged, {m['split_bad']} wrong merges",
           passed,
           reduction=f"{s['reduction']*100:.1f}%",
           cases=s["cases"], tickets=s["tickets"])

    assert m["merged_bad"] == 0, f"{m['merged_bad']} duplicate clusters not merged"
    assert m["split_bad"] == 0, f"{m['split_bad']} distinct issues wrongly merged"


if __name__ == "__main__":
    test_g3_dedupe()
