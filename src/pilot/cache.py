"""The semantic cache, in the same database as everything else.

Three systems means three hops and three bills. One database means one hop,
and the peak-week target is a first reply in 3 seconds.

A cache needs exactly one rule: embed the question, find the nearest stored
one, compare against a threshold. Start at 0.90 and tune on real tickets. Too
low gives more hits and wrong answers; too high is safe and misses near
duplicates.

Region is part of the lookup, not a filter applied afterwards, so an answer
can never cross a regional boundary by way of the cache.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date

from .bedrock import embed, Usage
from .config import CACHE_THRESHOLD


@dataclass
class CacheHit:
    answer: str
    citations: list
    similarity: float
    as_of: date | None


def lookup(conn, question: str, region: str,
           threshold: float = CACHE_THRESHOLD) -> tuple[CacheHit | None, Usage]:
    vector, usage = embed(question)
    row = conn.execute(
        """
        SELECT cache_id, answer, citations, as_of,
               1 - (embedding <=> %s::vector) AS similarity
          FROM answer_cache
         WHERE region = %s
         ORDER BY embedding <=> %s::vector
         LIMIT 1
        """, (vector, region, vector)).fetchone()
    if not row or float(row["similarity"]) < threshold:
        return None, usage
    conn.execute("UPDATE answer_cache SET hits = hits + 1 WHERE cache_id = %s",
                 (row["cache_id"],))
    return CacheHit(row["answer"], row["citations"],
                    float(row["similarity"]), row["as_of"]), usage


def store(conn, question: str, region: str, answer: str,
          citations: list, as_of: date | None) -> None:
    """Only grounded, checked answers are stored. A cached wrong answer is
    served instantly and forever, which is worse than a slow right one."""
    vector, _ = embed(question)
    conn.execute(
        "INSERT INTO answer_cache (region, question, embedding, answer, "
        "citations, as_of) VALUES (%s,%s,%s,%s,%s,%s)",
        (region, question, vector, answer,
         __import__("json").dumps(citations), as_of))
