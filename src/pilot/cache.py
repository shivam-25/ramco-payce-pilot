"""The semantic cache, in the same database as everything else.

Three systems means three hops and three bills. One database means one hop,
and the peak-week target is a first reply in 3 seconds.

A cache needs exactly one rule: embed the question, find the nearest stored
one, compare against a threshold. Start at 0.90 and tune on real tickets. Too
low gives more hits and wrong answers; too high is safe and misses near
duplicates.

Region is part of the lookup, not a filter applied afterwards, so an answer
can never cross a regional boundary by way of the cache.

Time is part of the lookup for the same reason. An entry carries the window
during which it is true — derived from the effective dates of the passages
it was drafted from — and a hit must fall inside it. The first version
stored `as_of` and never read it, so "which SIF version this month" asked in
September matched the same question asked in June at 0.958 and was served
the June answer. A cache that ignores time is a very fast way to give a
stale answer, and stale answers are what this pilot was built to stop.
"""
from __future__ import annotations
import json
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
    valid_from: date | None = None
    valid_to: date | None = None


def window_for(passages) -> tuple[date | None, date | None]:
    """The window in which an answer drafted from these passages is true.

    The latest start and the earliest end across the passages: the answer is
    only valid while every source it rests on is in force. A passage with no
    dates (a process document) constrains nothing.
    """
    starts = [p.effective_from for p in passages if getattr(p, "effective_from", None)]
    ends = [p.effective_to for p in passages if getattr(p, "effective_to", None)]
    return (max(starts) if starts else None, min(ends) if ends else None)


def lookup(conn, question: str, region: str, as_of: date | None = None,
           threshold: float = CACHE_THRESHOLD) -> tuple[CacheHit | None, Usage]:
    vector, usage = embed(question)
    row = conn.execute(
        """
        SELECT cache_id, answer, citations, as_of, valid_from, valid_to,
               1 - (embedding <=> %s::vector) AS similarity
          FROM answer_cache
         WHERE region = %s
           -- The window is checked in SQL, not after the fact, so a stale
           -- entry is never even the nearest neighbour.
           AND (%s::date IS NULL
                OR ((valid_from IS NULL OR valid_from <= %s::date)
                AND (valid_to   IS NULL OR valid_to   >  %s::date)))
         ORDER BY embedding <=> %s::vector
         LIMIT 1
        """, (vector, region, as_of, as_of, as_of, vector)).fetchone()
    if not row or float(row["similarity"]) < threshold:
        return None, usage
    conn.execute("UPDATE answer_cache SET hits = hits + 1 WHERE cache_id = %s",
                 (row["cache_id"],))
    return CacheHit(row["answer"], row["citations"],
                    float(row["similarity"]), row["as_of"],
                    row["valid_from"], row["valid_to"]), usage


def store(conn, question: str, region: str, answer: str,
          citations: list, as_of: date | None, passages=()) -> None:
    """Only grounded, checked answers are stored. A cached wrong answer is
    served instantly and forever, which is worse than a slow right one."""
    vector, _ = embed(question)
    valid_from, valid_to = window_for(passages)
    conn.execute(
        "INSERT INTO answer_cache (region, question, embedding, answer, "
        "citations, as_of, valid_from, valid_to) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (region, question, vector, answer, json.dumps(citations),
         as_of, valid_from, valid_to))
