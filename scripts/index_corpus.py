"""Embed the corpus under all four chunking strategies, into both regional
databases. Every strategy lives in the same table under its own label, so the
bake-off switches between them with a WHERE clause rather than a rebuild.

Each region's database holds: all four process documents (they are company
process, not customer data), that region's statutory rules, and the KB index
entries marked for that region or ALL.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pilot.config import DATA_ROOM, REGIONS       # noqa: E402
from pilot.db import connect                       # noqa: E402
from pilot.chunking import build, STRATEGIES       # noqa: E402
from pilot.bedrock import embed_many               # noqa: E402


def load_rules() -> list[dict]:
    rules = json.loads(
        (DATA_ROOM / "integrations" / "statutory_rules.json").read_text())["rules"]
    rules.sort(key=lambda r: (r["region"], r["topic"], r["effective_from"]))
    for i, r in enumerate(rules):
        nxt = next((n for n in rules[i + 1:]
                    if n["region"] == r["region"] and n["topic"] == r["topic"]), None)
        r["effective_to"] = nxt["effective_from"] if nxt else None
    return rules


def main() -> None:
    rules = load_rules()
    kb = json.loads((DATA_ROOM / "data" / "kb_articles.json").read_text())
    total_tokens = 0

    for region in REGIONS:
        region_rules = [r for r in rules if r["region"] == region]
        region_kb = [a for a in kb if a.get("region") in ("ALL", region)]

        with connect(region) as conn:
            conn.execute("TRUNCATE doc_chunks")
            for strategy in STRATEGIES:
                chunks = build(strategy, region_rules, region_kb)
                vectors, usage = embed_many([c.text for c in chunks])
                total_tokens += usage.input_tokens
                conn.cursor().executemany(
                    "INSERT INTO doc_chunks (strategy,source_type,source_id,heading,"
                    "text,region,effective_from,effective_to,token_estimate,embedding)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(c.strategy, c.source_type, c.source_id, c.heading, c.text,
                      c.region, c.effective_from, c.effective_to, c.tokens, v)
                     for c, v in zip(chunks, vectors)])
                print(f"  {region:4} {strategy:20} {len(chunks):3} chunks")
            n = conn.execute("SELECT count(*) AS n FROM doc_chunks").fetchone()["n"]
            print(f"  {region} total chunks: {n}")

    # Titan embed v2 at USD 0.00002 per 1K tokens. The whole corpus, four
    # strategies, two regions, costs less than a cent — worth stating when
    # someone asks whether a vector store is affordable.
    print(f"\nembedding tokens: {total_tokens:,}  "
          f"cost: USD {total_tokens/1000*0.00002:.5f}")


if __name__ == "__main__":
    main()
