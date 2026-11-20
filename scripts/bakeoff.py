"""Hit and try: all four chunking strategies against all four retrieval
strategies, 16 combinations, scored on the same 35 golden questions.

Scored WITHOUT a judge model. Whether the right document came back is a fact
about ids, not a matter of opinion, so it is checked with set arithmetic:
free, deterministic, and repeatable. The judge model is saved for G5, where
the question is whether the ANSWER stayed inside the document — which really
does need judgement.

Metrics, per combination:
  hit@k           did any expected source appear in the top k
  recall@k        fraction of expected sources that appeared
  precision@k     fraction of returned chunks that were expected
  MRR             1/rank of the first expected source
  dated_correct   on the five date-dependent questions, did the retrieved set
                  contain the rule version that was in force on `as_of`, and
                  NOT a version that was not yet in force
  abstain_clean   on the five unanswerable questions, did retrieval correctly
                  return nothing strongly relevant

`dated_correct` is the one that matters. A combination can top every other
column and still answer a June payslip question with the August rule, which
is the error the last vendor pilot shipped to customers.
"""
from __future__ import annotations
import os, json, sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
# A bulk sweep against a free-plan Bedrock quota needs the rate limiter;
# the deployed runtime does not. See pilot.bedrock.
os.environ.setdefault("PILOT_RATE_LIMIT", "1")

from pilot.config import TOP_K                       # noqa: E402
from pilot.db import connect                          # noqa: E402
from pilot.chunking import STRATEGIES as CHUNKERS     # noqa: E402
from pilot.retrieval import STRATEGIES as RETRIEVERS, retrieve   # noqa: E402

GOLDEN = ROOT / "evals" / "golden" / "g5_rag.jsonl"
REPORT = ROOT / "evals" / "reports" / "retrieval_bakeoff.md"


def _d(s: str) -> date:
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


def expected_ids(q: dict) -> set[str]:
    """Source ids are file names or rule ids; chunk source_id may carry a
    '#section' suffix, so compare on the part before it."""
    return {s.split("#")[0] for s in q.get("source_ids", [])}


def score(q: dict, hits: list) -> dict:
    want = expected_ids(q)
    got = [h.source_id.split("#")[0] for h in hits]
    got_set = set(got)

    if q["kind"] == "unanswerable" and not want:
        # Nothing should come back with real confidence. Dense retrieval
        # always returns k rows, so "clean" means the best score is weak.
        best = max((h.sim for h in hits), default=0.0)
        return {"hit": None, "recall": None, "precision": None, "mrr": None,
                "dated": None, "abstain_clean": best < 0.55}

    hit = bool(want & got_set)
    recall = len(want & got_set) / len(want) if want else None
    precision = sum(1 for g in got if g in want) / len(got) if got else 0.0
    mrr = 0.0
    for i, g in enumerate(got):
        if g in want:
            mrr = 1.0 / (i + 1)
            break

    dated = None
    if q["kind"] == "dated":
        as_of = _d(q["as_of"])
        # Every statutory chunk that came back must have been in force on the
        # day the question is about.
        rules = [h for h in hits if h.effective_from]
        in_force = [h for h in rules
                    if h.effective_from <= as_of
                    and (h.effective_to is None or h.effective_to > as_of)]
        premature = [h for h in rules if h.effective_from > as_of]
        dated = bool(in_force) and not premature

    return {"hit": hit, "recall": recall, "precision": precision, "mrr": mrr,
            "dated": dated, "abstain_clean": None}


def mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def main() -> None:
    questions = [json.loads(l) for l in GOLDEN.open() if l.strip()]
    results = []

    from contextlib import ExitStack
    with ExitStack() as stack:
        conns = {r: stack.enter_context(connect(r)) for r in ("IN", "UAE")}

        for ch in CHUNKERS:
            for rs in RETRIEVERS:
                rows = []
                for q in questions:
                    conn = conns[q["region"]]
                    hits, _ = retrieve(conn, ch, rs, q["question"], k=TOP_K,
                                       region=q["region"], as_of=_d(q["as_of"]))
                    rows.append(score(q, hits))

                answerable = [r for r in rows if r["hit"] is not None]
                results.append({
                    "chunking": ch, "retrieval": rs,
                    "hit_at_k": mean(r["hit"] for r in answerable),
                    "recall": mean(r["recall"] for r in answerable),
                    "precision": mean(r["precision"] for r in answerable),
                    "mrr": mean(r["mrr"] for r in answerable),
                    "dated": mean(r["dated"] for r in rows),
                    "abstain": mean(r["abstain_clean"] for r in rows),
                })
                r = results[-1]
                print(f"  {ch:20} {rs:16} hit {r['hit_at_k']:.2f}  "
                      f"recall {r['recall']:.2f}  prec {r['precision']:.2f}  "
                      f"mrr {r['mrr']:.2f}  dated {r['dated']:.2f}")
    # Rank: date correctness first, then hit rate, then MRR. Precision is
    # reported but deliberately not ranked on — at k=5 over a 40-chunk corpus
    # it mostly measures how many chunks a strategy made, not their quality.
    results.sort(key=lambda r: (-(r["dated"] or 0), -(r["hit_at_k"] or 0),
                                -(r["mrr"] or 0)))
    best = results[0]

    lines = [
        "# Retrieval bake-off",
        "",
        f"35 golden questions · k={TOP_K} · scored with set arithmetic, no judge model.",
        "",
        "Ranked on date correctness first, then hit rate, then MRR. "
        "`dated` is the share of the five date-dependent questions where every "
        "statutory rule retrieved was actually in force on the day asked about. "
        "A combination that scores well everywhere else and badly here will "
        "answer a June payslip question with the August rule — which is the "
        "error the previous vendor pilot shipped.",
        "",
        "| # | Chunking | Retrieval | hit@k | recall | precision | MRR | dated | abstain |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        lines.append(
            f"| {i} | {r['chunking']} | {r['retrieval']} | "
            f"{r['hit_at_k']:.2f} | {r['recall']:.2f} | {r['precision']:.2f} | "
            f"{r['mrr']:.2f} | {r['dated']:.2f} | "
            f"{r['abstain']:.2f} |")
    lines += ["",
              f"**Winner: {best['chunking']} + {best['retrieval']}** — "
              f"hit@{TOP_K} {best['hit_at_k']:.2f}, MRR {best['mrr']:.2f}, "
              f"date correctness {best['dated']:.2f}.", ""]
    REPORT.write_text("\n".join(lines))
    (ROOT / "evals" / "reports" / "bakeoff.json").write_text(
        json.dumps(results, indent=2))
    print(f"\nwinner: {best['chunking']} + {best['retrieval']}")
    print(f"report -> {REPORT}")


if __name__ == "__main__":
    main()
