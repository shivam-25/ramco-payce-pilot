"""Gate G9 — a KB index entry is not an answer, and we do not pay to learn it.

The knowledge-base export contains rows that name an article and point at
Confluence without carrying its body. There is nothing in one to ground an
answer on. Output check C7 already refused such drafts, but only after the
model had written one, so the system bought a draft in order to throw it
away.

Stubs are now dropped before the draft call. This gate pins the four things
that have to stay true once they are:

  1. all stubs      -> abstain, and no model call at all
  2. one real one   -> still answered, from the real passage
  3. citations      -> [1] means the first SURVIVING passage, not the first
                       retrieved one, or every citation silently shifts
  4. stub text      -> never reaches the model

Point 3 is the one that would fail silently. Filtering a list that other
code indexes into is exactly how a citation starts pointing at the wrong
document while every test still passes.

No model and no database: it runs in CI in milliseconds.
"""
from __future__ import annotations
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "evals"))

from pilot.chunking import STUB_MARKER          # noqa: E402
from pilot.retrieval import Hit                 # noqa: E402
import pilot.answer as A                        # noqa: E402

AS_OF = date(2026, 9, 15)


def _hit(text: str, sim: float, source_id: str) -> Hit:
    return Hit(chunk_id=1, source_type="kb", source_id=source_id,
               heading=None, text=text, score=sim, sim=sim)


def _run(hits, converse):
    """Call answer() with retrieval and the model both stubbed out."""
    real_retrieve, real_converse = A.retrieve, A.converse
    try:
        A.retrieve = lambda *a, **k: (hits, None)
        A.converse = converse
        return A.answer(None, "What is the ESI contribution rate?",
                        region="IN", as_of=AS_OF,
                        chunk_strategy="D_heading_context",
                        retrieval_strategy="R4_hybrid_dated")
    finally:
        A.retrieve, A.converse = real_retrieve, real_converse


def _never_called(*a, **k):
    raise AssertionError("a model was called on a question that cannot be answered")


def test_g9_stub_gate():
    results = {}

    # 1. Every passage is a stub: abstain without spending anything.
    d = _run([_hit(f"{STUB_MARKER}\nESI rates article", 0.62, "KB-0140"),
              _hit(f"{STUB_MARKER}\nStatutory index", 0.55, "KB-0141")],
             _never_called)
    assert d.escalate, "all-stub retrieval must abstain"
    assert "index entry" in d.reason, d.reason
    assert not d.citations, "an abstention must not cite anything"
    results["all_stubs_abstain_no_model_call"] = True

    # 2+3+4. A real passage ranked BELOW a stub must still be answered from,
    # cited correctly, and the stub must never reach the model.
    seen = {}

    def capture(model, prompt, **k):
        seen["prompt"] = prompt
        return ("The ESI employee contribution is 0.75 percent [1].", None)

    d = _run([_hit(f"{STUB_MARKER}\npointer only", 0.91, "KB-STUB"),
              _hit("The ESI employee contribution is 0.75 percent of wages.",
                   0.44, "KB-REAL")],
             capture)
    assert not d.escalate, "a real passage among stubs is still answerable"
    assert d.citations == ["KB-REAL"], (
        f"citation [1] must mean the first surviving passage, got {d.citations}")
    assert STUB_MARKER not in seen["prompt"], "stub text reached the model"
    assert "KB-STUB" not in seen["prompt"], "stub source reached the model"
    results["real_passage_survives_and_cites_correctly"] = True

    # The floor must be judged on what can be drafted from, not on a stub
    # that happened to embed well.
    d = _run([_hit(f"{STUB_MARKER}\npointer only", 0.95, "KB-STUB"),
              _hit("barely related text", 0.02, "KB-REAL")],
             _never_called)
    assert d.escalate and "score floor" in d.reason, d.reason
    results["floor_judged_on_substantive_passages"] = True

    # An abstention still shows the human everything that was retrieved.
    d = _run([_hit(f"{STUB_MARKER}\npointer only", 0.62, "KB-0140")],
             _never_called)
    assert [p.source_id for p in d.passages] == ["KB-0140"], (
        "the human must still see what was retrieved")
    results["abstention_shows_what_was_retrieved"] = True

    try:
        from _harness import record
        record("G9 stub gate", "no model call on an unanswerable question",
               f"{len(results)}/4 properties hold", True, **results)
    except Exception:
        pass

    for k, v in results.items():
        print(f"  [ok] {k}")
    print(f"\n[PASS] G9 stub gate: {len(results)}/4 properties hold")


if __name__ == "__main__":
    test_g9_stub_gate()
