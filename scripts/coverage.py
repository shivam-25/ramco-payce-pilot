"""How often does the pilot answer, rather than hand over?

The brief's coverage question is not "is the answer faithful" -- G5 measures
that -- but "how many tickets never reach a person at all". This replays real
tickets through the deployed endpoint and counts the outcomes.

It exists to measure one change honestly: indexing 38 resolved escalations as
a known_issue source. The questions that source answers are not ones I would
have thought to write, so measuring it on questions I wrote would prove
nothing. These are the export's own tickets, sampled with a fixed seed.

Tickets that route to a human for a reason the corpus cannot change -- money,
an injection attempt, an unknown customer -- are reported separately, because
no amount of indexing moves them and averaging them in hides the effect.
"""
from __future__ import annotations
import argparse, json, os, random, sys, urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SEED = 20261120


def sample(n: int) -> list[dict]:
    from pilot.config import DATA_ROOM
    raw = json.loads((DATA_ROOM / "data" / "tickets_export.json").read_text())["tickets"]
    usable = [t for t in raw if (t.get("message") or "").strip()]
    rng = random.Random(SEED)
    rng.shuffle(usable)
    return usable[:n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=60)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    url, key = os.environ["PILOT_API_URL"], os.environ["PILOT_API_KEY"]

    routes, reasons, cited_sources = Counter(), Counter(), Counter()
    rows = []
    for i, t in enumerate(sample(a.n), 1):
        body = json.dumps({
            "subject": t.get("title") or "", "body": t.get("message") or "",
            "customer_id": t.get("org_id"), "use_cache": False,
        }).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"content-type": "application/json", "x-pilot-key": key})
        try:
            d = json.load(urllib.request.urlopen(req, timeout=180))
        except Exception as exc:                       # noqa: BLE001
            routes["error"] += 1
            print(f"    {i}/{a.n} error {type(exc).__name__}", flush=True)
            continue
        routes[d.get("route", "?")] += 1
        if d.get("route") == "human":
            r = d.get("reason", "")
            tag = ("money" if "money" in r else
                   "injection" if "injection" in r else
                   "unknown customer" if "customer not found" in r else
                   "P1" if "P1" in r else
                   "no grounds")
            reasons[tag] += 1
        for c in d.get("citations") or []:
            cited_sources[c.split("-")[0]] += 1
        rows.append({"ticket": t.get("tkt"), "route": d.get("route"),
                     "reason": d.get("reason"), "citations": d.get("citations")})
        if i % 10 == 0:
            print(f"    {i}/{a.n}", flush=True)

    done = sum(routes.values()) - routes["error"]
    answered = routes["answer"] + routes["cache"]
    # Tickets a corpus change can never move.
    structural = reasons["money"] + reasons["injection"] + reasons["unknown customer"] + reasons["P1"]
    eligible = done - structural

    print(f"\n  replayed {done} real tickets")
    for r, c in routes.most_common():
        print(f"    {r:10} {c:4}  ({c/max(1,done)*100:4.1f}%)")
    print(f"\n  answered without a person      {answered}/{done} = "
          f"{answered/max(1,done)*100:.1f}%")
    print(f"  of those a corpus can reach    {answered}/{eligible} = "
          f"{answered/max(1,eligible)*100:.1f}%  "
          f"({structural} went to a person for money, injection, P1 or an "
          f"unknown customer)")
    print("\n  hand-off reasons")
    for r, c in reasons.most_common():
        print(f"    {r:18} {c}")
    print("\n  citations by source family")
    for r, c in cited_sources.most_common():
        print(f"    {r:12} {c}")

    if a.out:
        Path(a.out).write_text(json.dumps(
            {"n": done, "routes": dict(routes), "reasons": dict(reasons),
             "answered": answered, "eligible": eligible,
             "cited_sources": dict(cited_sources), "seed": SEED,
             "rows": rows}, indent=1))
        print(f"\n  wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
