"""Gate G5 — the answer step. Pass mark: faithfulness >= 0.90, right document.

A RAG answer fails in two different ways and they need two different scores:

  retrieval   did the right document come back?   context precision / recall
  faithfulness did the answer stay inside it?     faithfulness

The June payslip question answered with the August rule fails the first and
passes the second. Reporting one number hides exactly the error that ended
the last vendor pilot, so both are reported, plus two plain-code checks that
a judge model cannot do:

  dated_correct   the answer quotes the value in force on the date asked about
  abstained       on a question the corpus cannot answer, the system said so

Ragas runs on Bedrock. The judge and the embedder are the account's own
models, so no third-party key and no case data leaving AWS — the brief says
keep case data out of public services and that includes the eval harness.
"""
from __future__ import annotations
import json, sys
from contextlib import ExitStack
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import load, record, REPORTS              # noqa: E402

from pilot.config import (JUDGE_MODEL, EMBED_MODEL, AWS_REGION,  # noqa: E402
                          TOP_K)
from pilot.db import connect                             # noqa: E402
from pilot.answer import answer                          # noqa: E402

TARGET_FAITHFULNESS = 0.90
CHUNKING = "D_heading_context"
RETRIEVAL = "R4_hybrid_dated"


def _d(s: str) -> date:
    y, m, dd = s.split("-")
    return date(int(y), int(m), int(dd))


def generate() -> list[dict]:
    """Run the real pipeline over the golden questions and keep what it
    retrieved. Scoring a mock would prove nothing about the pilot."""
    questions = [json.loads(l) for l in
                 (Path(__file__).resolve().parents[1] / "evals" / "golden"
                  / "g5_rag.jsonl").open() if l.strip()]
    out = []
    with ExitStack() as stack:
        conns = {r: stack.enter_context(connect(r)) for r in ("IN", "UAE")}
        for q in questions:
            d = answer(conns[q["region"]], q["question"], region=q["region"],
                       as_of=_d(q["as_of"]), chunk_strategy=CHUNKING,
                       retrieval_strategy=RETRIEVAL, k=TOP_K)
            out.append({
                "id": q["id"], "kind": q["kind"], "question": q["question"],
                "answer": d.text, "contexts": [h.text for h in d.passages],
                "ground_truth": q["ground_truth"],
                "escalated": d.escalate or not d.grounded,
                "citations": d.citations,
                "must_contain": q.get("must_contain", []),
                "must_not_contain": q.get("must_not_contain", []),
                "must_abstain": q.get("must_abstain", False),
                "usd": d.usage.usd if d.usage else 0.0,
                "seconds": d.usage.seconds if d.usage else 0.0,
            })
            print(f"  {q['id']:10} {'ESCALATED' if out[-1]['escalated'] else 'answered':10} "
                  f"{len(d.citations)} citations")
    return out


def plain_checks(rows: list[dict]) -> dict:
    """What a judge model cannot tell you."""
    dated = [r for r in rows if r["kind"] == "dated"]
    dated_ok = 0
    for r in dated:
        text = r["answer"]
        ok = (all(s in text for s in r["must_contain"])
              and not any(s in text for s in r["must_not_contain"]))
        dated_ok += bool(ok and not r["escalated"])

    abstain = [r for r in rows if r["must_abstain"]]
    abstain_ok = sum(1 for r in abstain if r["escalated"])

    answerable = [r for r in rows if r["kind"] == "answerable"]
    answered = sum(1 for r in answerable if not r["escalated"])

    return {
        "dated_total": len(dated), "dated_ok": dated_ok,
        "abstain_total": len(abstain), "abstain_ok": abstain_ok,
        "answerable_total": len(answerable), "answered": answered,
        "usd_total": sum(r["usd"] for r in rows),
        "usd_per_draft": (sum(r["usd"] for r in rows)
                          / max(1, sum(1 for r in rows if not r["escalated"]))),
    }


def ragas_scores(rows: list[dict]) -> dict:
    """Faithfulness and retrieval quality, judged on Bedrock."""
    from langchain_aws import ChatBedrockConverse, BedrockEmbeddings
    from ragas import evaluate, SingleTurnSample, EvaluationDataset
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.metrics import (Faithfulness, ResponseRelevancy,
                               LLMContextPrecisionWithReference,
                               LLMContextRecall)

    judged = [r for r in rows if not r["escalated"] and r["contexts"]]
    if not judged:
        return {"note": "nothing to judge"}

    llm = LangchainLLMWrapper(ChatBedrockConverse(
        model=JUDGE_MODEL, region_name=AWS_REGION, temperature=0,
        max_tokens=1024))
    emb = LangchainEmbeddingsWrapper(BedrockEmbeddings(
        model_id=EMBED_MODEL, region_name=AWS_REGION))

    dataset = EvaluationDataset(samples=[
        SingleTurnSample(user_input=r["question"], response=r["answer"],
                         retrieved_contexts=r["contexts"],
                         reference=r["ground_truth"])
        for r in judged])

    result = evaluate(
        dataset=dataset,
        metrics=[Faithfulness(llm=llm), ResponseRelevancy(llm=llm, embeddings=emb),
                 LLMContextPrecisionWithReference(llm=llm), LLMContextRecall(llm=llm)],
        llm=llm, embeddings=emb, show_progress=True,
    )
    df = result.to_pandas()
    scores = {}
    for col in ("faithfulness", "answer_relevancy", "llm_context_precision_with_reference",
                "context_recall"):
        if col in df.columns:
            scores[col] = float(df[col].dropna().mean())
    scores["judged"] = len(judged)
    df.to_json(REPORTS / "g5_ragas_detail.json", orient="records", indent=2)
    return scores


def test_g5_rag():
    rows = generate()
    plain = plain_checks(rows)
    (REPORTS / "g5_generations.json").write_text(json.dumps(rows, indent=2))

    print(f"\n  answerable answered    {plain['answered']}/{plain['answerable_total']}")
    print(f"  date-dependent correct {plain['dated_ok']}/{plain['dated_total']}")
    print(f"  correctly abstained    {plain['abstain_ok']}/{plain['abstain_total']}")
    print(f"  cost per draft         USD {plain['usd_per_draft']:.5f}")

    scores = ragas_scores(rows)
    for k, v in scores.items():
        if isinstance(v, float):
            print(f"  {k:36} {v:.3f}")

    faith = scores.get("faithfulness", 0.0)
    passed = (faith >= TARGET_FAITHFULNESS
              and plain["dated_ok"] == plain["dated_total"]
              and plain["abstain_ok"] == plain["abstain_total"])

    record("G5 answer (RAG)", "faithfulness 0.90, right document",
           f"faithfulness {faith:.3f}", passed,
           context_precision=round(scores.get(
               "llm_context_precision_with_reference", 0.0), 3),
           context_recall=round(scores.get("context_recall", 0.0), 3),
           answer_relevancy=round(scores.get("answer_relevancy", 0.0), 3),
           dated_correct=f"{plain['dated_ok']}/{plain['dated_total']}",
           abstained_correctly=f"{plain['abstain_ok']}/{plain['abstain_total']}",
           usd_per_draft=round(plain["usd_per_draft"], 5),
           chunking=CHUNKING, retrieval=RETRIEVAL)

    assert faith >= TARGET_FAITHFULNESS, f"faithfulness {faith:.3f} < 0.90"
    assert plain["dated_ok"] == plain["dated_total"], \
        f"date-dependent answers wrong: {plain['dated_ok']}/{plain['dated_total']}"
    assert plain["abstain_ok"] == plain["abstain_total"], \
        f"failed to abstain: {plain['abstain_ok']}/{plain['abstain_total']}"


if __name__ == "__main__":
    test_g5_rag()
