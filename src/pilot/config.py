"""One place for the knobs. Everything reads from the environment so the same
code runs locally, in the eval harness and inside the AgentCore runtime."""
from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_ROOM = Path(os.environ.get("RAMCO_DATA_ROOM", ROOT / "data" / "data-room"))

AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")

# Models. Verified callable on account <account> by direct Converse calls
# on 19 Nov 2026.
#
# Claude is NOT available on this account: every anthropic.* id returns
#   "Model use case details have not been submitted for this account."
# That form is a two-minute console step (Bedrock -> Model access -> Anthropic
# use case details). Until it is submitted the pilot runs on the Nova family,
# which needs no form. The brief's own instruction settles the choice: a pilot
# that works end to end beats a clever one that does not.
#
# When the form IS submitted, change two lines and rerun the gates:
#   DRAFT_MODEL -> "au.anthropic.claude-haiku-4-5-20251001-v1:0"
#   JUDGE_MODEL -> a Claude model one class above the drafter
#
# Nova Micro classifies: picking one of nine labels from two sentences is an
# easy task and this is the cheapest token on the account.
#
# Nova Pro drafts. It was Lite, chosen to be frugal, and measuring that choice
# showed there was nothing to be frugal about: cost per ticket is USD 0.00004
# against the brief's cap of USD 4.50, about a hundred thousand times of head
# room. Drafted both arms over the 35-question golden set, same corpus, same
# retrieval, same questions. Pro answered the same 29 and withheld the same 3,
# cost eight times more -- USD 0.000537 a question, still four orders of
# magnitude under the cap -- and got more right: must_contain 5/5 against 4/5,
# must_abstain 5/5 against 4/5. Those two are string checks against the golden
# set, so no model graded them, and they are what the choice rests on.
#
# That run also reported "took the same time, p50 7.40s both", and that part
# was wrong. Neither model appears in pilot.bedrock's RPM table, so both fell
# to its default of 10 rpm, and 60 / (10 * 0.8) is 7.5 s. The figure was the
# rate limiter's own sleep; it matched across the arms because the arms shared
# a default, not because the models are equally fast. How their latencies
# actually compare is unmeasured. The limiter no longer runs outside a sweep,
# so a rerun would measure the models rather than the quota.
#
# Nova Pro also judges, which is now the same model drafting and grading. That
# is the thing the previous note here warned against, and it is a real cost of
# this change: G5's faithfulness number is self-graded from here and should be
# read as indicative, not as a gate, until a stronger judge exists. The same
# run measured the judge's own noise at about +/-0.04, by scoring context
# precision and recall -- retrieval metrics, identical inputs in both arms --
# and getting 0.934/0.904 for one and 0.895/0.942 for the other.
#
# When the Claude use-case form clears, the judge moves up a class and the
# gate becomes trustworthy again. That is the first thing to do with it.
CLASSIFY_MODEL = os.environ.get("BEDROCK_CLASSIFY_MODEL", "apac.amazon.nova-micro-v1:0")
DRAFT_MODEL = os.environ.get("BEDROCK_DRAFT_MODEL", "apac.amazon.nova-pro-v1:0")
JUDGE_MODEL = os.environ.get("BEDROCK_JUDGE_MODEL", "apac.amazon.nova-pro-v1:0")
EMBED_MODEL = os.environ.get("BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0")
EMBED_DIMS = int(os.environ.get("EMBED_DIMS", "512"))

# Bedrock on-demand prices, USD per 1,000 tokens, ap-southeast-2.
# Pulled from the AWS Price List API by scripts/fetch_prices.py; the list
# AWS published on 2026-11-11 is saved in evals/reports/prices.json. Gate G7
# prices real token counts with these, so if AWS moves a price the gate moves
# with it rather than quietly reporting last month's bill.
PRICES = {
    "apac.amazon.nova-micro-v1:0":       {"in": 0.000018, "out": 0.000074},
    "apac.amazon.nova-lite-v1:0":        {"in": 0.000063, "out": 0.000126},
    "apac.amazon.nova-pro-v1:0":         {"in": 0.000420, "out": 0.003360},
    # Titan embeddings are not listed under the same product attributes in
    # the price list; this is the published on-demand rate and is flagged as
    # the one number here that is not machine-pulled. It contributes about
    # half a tenth of a cent to the whole corpus, so it cannot move a
    # conclusion either way.
    "amazon.titan-embed-text-v2:0":      {"in": 0.000020, "out": 0.0},
    # Kept so a switch back to Claude reprices without code changes.
    "au.anthropic.claude-haiku-4-5-20251001-v1:0": {"in": 0.001, "out": 0.005},
}

# The regions whose data must not mix. One database each.
REGIONS = ("IN", "UAE")

# Semantic cache: cosine similarity at or above this is a hit. The study note
# says start at 0.90 and tune on your own tickets; G7 reports the hit rate.
CACHE_THRESHOLD = float(os.environ.get("CACHE_THRESHOLD", "0.90"))

# Retrieval: how many chunks reach the model. This is the cost dial.
TOP_K = int(os.environ.get("TOP_K", "5"))


def secret_name(region: str) -> str:
    return f"ramco/pilot/db/{region}"
