"""Four retrieval strategies, scored against the same golden questions.

R1 dense        pgvector HNSW cosine
R2 lexical      Postgres BM25-ish via ts_rank_cd on the generated tsvector
R3 hybrid       both, fused with Reciprocal Rank Fusion
R4 hybrid+dates R3 plus a region filter and an effective-date filter

R4 is expected to win, but not on relevance — on correctness. The EPF wage
ceiling moved from INR 15,000 to INR 25,000 on 2026-08-01. A question about a
June payslip retrieved by R1, R2 or R3 can return the August rule, and the
model will then write a perfectly faithful answer to the wrong document. That
failure scores 1.0 on faithfulness and is still wrong, which is exactly why
retrieval quality and faithfulness have to be measured as two things.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date

from .bedrock import embed, Usage
from .config import TOP_K

STRATEGIES = ("R1_dense", "R2_lexical", "R3_hybrid", "R4_hybrid_dated")
RRF_K = 60          # the constant from the original RRF paper


@dataclass
class Hit:
    chunk_id: int
    source_type: str
    source_id: str
    heading: str | None
    text: str
    score: float                      # ranking score, scale depends on strategy
    effective_from: date | None = None
    effective_to: date | None = None
    sim: float = 0.0                  # cosine similarity, always comparable

# `score` and `sim` are separate on purpose. Reciprocal Rank Fusion produces
# values around 1/(60+rank), roughly 0.016 — nothing like a cosine
# similarity. Using `score` as an abstain threshold therefore makes every
# hybrid result look irrelevant and the pipeline escalates everything. The
# ranking scale and the confidence scale are different quantities and are
# kept apart.


def _rows_to_hits(rows, cosine: bool) -> list[Hit]:
    return [Hit(r["chunk_id"], r["source_type"], r["source_id"], r["heading"],
                r["text"], float(r.get("score") or 0.0),
                r.get("effective_from"), r.get("effective_to"),
                sim=float(r.get("score") or 0.0) if cosine else 0.0)
            for r in rows]


# ------------------------------------------------------------------ dense
def dense(conn, strategy: str, vector, k: int, region: str | None = None,
          as_of: date | None = None) -> list[Hit]:
    sql = """
        SELECT chunk_id, source_type, source_id, heading, text,
               effective_from, effective_to,
               1 - (embedding <=> %s::vector) AS score
          FROM doc_chunks
         WHERE strategy = %s
           AND embedding IS NOT NULL
    """
    args: list = [vector, strategy]
    if region:
        sql += " AND region IN ('ALL', %s)"
        args.append(region)
    if as_of:
        sql += (" AND (effective_from IS NULL OR effective_from <= %s)"
                " AND (effective_to   IS NULL OR effective_to   >  %s)")
        args += [as_of, as_of]
    sql += " ORDER BY embedding <=> %s::vector LIMIT %s"
    args += [vector, k]
    return _rows_to_hits(conn.execute(sql, args).fetchall(), cosine=True)


# ---------------------------------------------------------------- lexical
def lexical(conn, strategy: str, query: str, k: int, region: str | None = None,
            as_of: date | None = None) -> list[Hit]:
    sql = """
        SELECT chunk_id, source_type, source_id, heading, text,
               effective_from, effective_to,
               ts_rank_cd(tsv, plainto_tsquery('english', %s)) AS score
          FROM doc_chunks
         WHERE strategy = %s
           AND tsv @@ plainto_tsquery('english', %s)
    """
    args: list = [query, strategy, query]
    if region:
        sql += " AND region IN ('ALL', %s)"
        args.append(region)
    if as_of:
        sql += (" AND (effective_from IS NULL OR effective_from <= %s)"
                " AND (effective_to   IS NULL OR effective_to   >  %s)")
        args += [as_of, as_of]
    sql += " ORDER BY score DESC LIMIT %s"
    args.append(k)
    return _rows_to_hits(conn.execute(sql, args).fetchall(), cosine=False)


# ------------------------------------------------------------------ fuse
def rrf(*rankings: list[Hit], k: int = TOP_K) -> list[Hit]:
    """Reciprocal Rank Fusion. Rank-based, so it does not need the two score
    scales to be comparable — which they are not: cosine similarity and
    ts_rank_cd have nothing to do with each other."""
    scores: dict[int, float] = {}
    sims: dict[int, float] = {}
    best: dict[int, Hit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
            # Carry the cosine similarity through fusion so the abstain
            # decision still has a scale it can reason about.
            sims[hit.chunk_id] = max(sims.get(hit.chunk_id, 0.0), hit.sim)
            best.setdefault(hit.chunk_id, hit)
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])[:k]
    return [Hit(best[cid].chunk_id, best[cid].source_type, best[cid].source_id,
                best[cid].heading, best[cid].text, sc,
                best[cid].effective_from, best[cid].effective_to,
                sim=sims[cid])
            for cid, sc in ordered]


# ------------------------------------------------------------- front door
def retrieve(conn, chunk_strategy: str, retrieval_strategy: str, question: str,
             k: int = TOP_K, region: str | None = None,
             as_of: date | None = None) -> tuple[list[Hit], Usage | None]:
    """One call, whichever strategy. `region` and `as_of` are ignored by
    R1-R3 on purpose: the bake-off has to show what is lost without them."""
    usage = None
    if retrieval_strategy in ("R1_dense", "R3_hybrid", "R4_hybrid_dated"):
        vector, usage = embed(question)

    if retrieval_strategy == "R1_dense":
        return dense(conn, chunk_strategy, vector, k), usage
    if retrieval_strategy == "R2_lexical":
        return lexical(conn, chunk_strategy, question, k), None
    if retrieval_strategy == "R3_hybrid":
        d = dense(conn, chunk_strategy, vector, k * 3)
        l = lexical(conn, chunk_strategy, question, k * 3)
        return rrf(d, l, k=k), usage
    if retrieval_strategy == "R4_hybrid_dated":
        d = dense(conn, chunk_strategy, vector, k * 3, region=region, as_of=as_of)
        l = lexical(conn, chunk_strategy, question, k * 3, region=region, as_of=as_of)
        return rrf(d, l, k=k), usage
    raise ValueError(retrieval_strategy)
