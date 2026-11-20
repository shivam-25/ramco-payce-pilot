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
from dataclasses import dataclass, field
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
    # The cited passages, carried through the cache. Without these a cached
    # reply shows citation markers that open onto nothing, and the brief
    # asks for 100% of replies to carry a *verifiable* citation. A marker
    # the reader cannot check is not one.
    sources: list = field(default_factory=list)


def window_for(passages) -> tuple[date | None, date | None]:
    """The window in which an answer drafted from these passages is true.

    The latest start and the earliest end across the passages: the answer is
    only valid while every source it rests on is in force. A passage with no
    dates (a process document) constrains nothing.
    """
    starts = [p.effective_from for p in passages if getattr(p, "effective_from", None)]
    ends = [p.effective_to for p in passages if getattr(p, "effective_to", None)]
    return (max(starts) if starts else None, min(ends) if ends else None)


def passage_sources(passages, citations) -> list:
    """The cited passages, in the shape the console renders. Built in one
    place so a fresh answer and a cached one are inspected identically."""
    cited = set(citations or [])
    return [{"source_id": h.source_id,
             "heading": getattr(h, "heading", None),
             "effective_from": str(h.effective_from) if getattr(h, "effective_from", None) else None,
             "effective_to": str(h.effective_to) if getattr(h, "effective_to", None) else None,
             "snippet": (getattr(h, "text", "") or "")[:400],
             "sim": round(getattr(h, "sim", 0.0), 3)}
            for h in passages if h.source_id in cited]


# Bumped whenever an output check is added or tightened.
#
# A cached answer passed the checks that existed when it was stored, and
# then it is served without being checked again -- which is correct, until
# the checks change. C10 was added because a draft claimed the product
# could not export payslips in bulk; the draft that prompted the check was
# already in the cache, so the new check could not see it and the same
# wrong answer kept being served instantly and for free.
#
# Rather than purge the cache, entries below the current version are simply
# not eligible. Each such question is drafted once more, re-checked against
# the current rules, and stored at the new version: the same self-healing
# the sources column does. An answer that no longer passes is never stored,
# so it leaves the cache by not coming back.
CHECKS_VERSION = 2


def _ensure_checks_version(conn) -> None:
    """Rows written before this column predate C10. They default to 1, which
    is below CHECKS_VERSION, so they stop being served and get redrafted."""
    try:
        conn.execute("ALTER TABLE answer_cache ADD COLUMN IF NOT EXISTS "
                     "checks_version integer NOT NULL DEFAULT 1")
    except Exception:
        pass


def _ensure_sources_column(conn) -> None:
    """Older rows predate this column. Adding it is cheap and idempotent,
    and failing to add it must cost the citation detail, not the answer."""
    try:
        conn.execute("ALTER TABLE answer_cache ADD COLUMN IF NOT EXISTS sources jsonb")
    except Exception:
        pass


def lookup(conn, question: str, region: str, as_of: date | None = None,
           threshold: float = CACHE_THRESHOLD) -> tuple[CacheHit | None, Usage]:
    vector, usage = embed(question)
    _ensure_sources_column(conn)
    _ensure_checks_version(conn)
    row = conn.execute(
        """
        SELECT cache_id, answer, citations, sources, as_of, valid_from, valid_to,
               1 - (embedding <=> %s::vector) AS similarity
          FROM answer_cache
         WHERE region = %s
           -- The window is checked in SQL, not after the fact, so a stale
           -- entry is never even the nearest neighbour.
           -- An entry written before passages were recorded cannot show
           -- what it cited, and a citation the reader cannot open is not a
           -- verifiable one. Skipping those entries costs one re-draft
           -- each, after which the entry is stored with its sources and the
           -- cache has healed itself. The alternative is serving markers
           -- that open onto nothing, forever.
           AND sources IS NOT NULL
           -- Checked in SQL for the same reason the date window is: an
           -- entry that predates the current checks must not even be the
           -- nearest neighbour.
           AND checks_version >= %s
           AND (%s::date IS NULL
                OR ((valid_from IS NULL OR valid_from <= %s::date)
                AND (valid_to   IS NULL OR valid_to   >  %s::date)))
         ORDER BY embedding <=> %s::vector
         LIMIT 1
        """, (vector, region, CHECKS_VERSION, as_of, as_of, as_of,
              vector)).fetchone()
    if not row or float(row["similarity"]) < threshold:
        return None, usage
    conn.execute("UPDATE answer_cache SET hits = hits + 1 WHERE cache_id = %s",
                 (row["cache_id"],))
    return CacheHit(row["answer"], row["citations"],
                    float(row["similarity"]), row["as_of"],
                    row["valid_from"], row["valid_to"],
                    row.get("sources") or []), usage


def store(conn, question: str, region: str, answer: str,
          citations: list, as_of: date | None, passages=()) -> None:
    """Only grounded, checked answers are stored. A cached wrong answer is
    served instantly and forever, which is worse than a slow right one."""
    vector, _ = embed(question)
    valid_from, valid_to = window_for(passages)
    _ensure_sources_column(conn)
    _ensure_checks_version(conn)
    conn.execute(
        "INSERT INTO answer_cache (region, question, embedding, answer, "
        "citations, sources, as_of, valid_from, valid_to, checks_version) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (region, question, vector, answer, json.dumps(citations),
         json.dumps(passage_sources(passages, citations)),
         as_of, valid_from, valid_to, CHECKS_VERSION))
