# What it costs

Every price here comes from the AWS Price List API for `ap-southeast-2`, on
the list AWS published 2026-11-11. `scripts/fetch_prices.py` pulls the
Bedrock rows into `evals/reports/prices.json`, and gate G7 prices real token
counts against them — so if AWS moves a price, the gate moves with it
instead of quietly reporting last month's bill. Two figures are marked where
they are not machine-pulled.

## Today

| | |
|---|---|
| Tickets per month | 15,000 |
| Fully-loaded cost per ticket today | **USD 6.03** |
| Monthly | **USD 90,450** |

## Per ticket, measured

From a live run against the deployed pipeline, not an estimate:

| Path | Models called | Cost |
|---|---|---|
| Answered with citations | classify + embed + draft | **USD 0.000086** |
| Routed to a human | classify only | USD 0.000020 |
| Prompt injection | **none — refused before any model call** | USD 0.000000 |
| Cache hit | embed only | USD 0.000020 |

At 15,000 tickets a month, every one of them answered, the model bill is
**USD 1.29 a month**. That is the headline and it is also a trap, because
the models are not where the money goes.

## Where the money actually goes

| Line | Rate | Monthly |
|---|---|---|
| RDS `db.t4g.micro`, PostgreSQL, single-AZ × 2 | USD 0.025 /hr | 36.50 |
| RDS gp3 storage, 20 GB × 2 | USD 0.138 /GB-mo | 5.52 |
| NAT gateway | USD 0.059 /hr | 43.07 |
| NAT data processed | USD 0.059 /GB | ~1 |
| S3 gateway endpoint | free | 0.00 |
| Bedrock, 15,000 tickets | measured above | 1.29 |
| **Total** | | **≈ USD 87** |

Against USD 90,450 a month, the whole thing costs about **0.1%** of what it
replaces. The interesting part is the shape, not the size: **97% of the run
cost is always-on infrastructure and 1.5% is the models.** Doubling ticket
volume changes the bill by about a dollar. Leaving a database running over a
quiet weekend costs more than a month of inference.

Two consequences worth stating plainly.

**Optimising prompts here would be a waste of time.** Switching the drafter
from Nova Lite to Nova Micro would save roughly USD 0.40 a month. The same
effort spent on the two-database split or the NAT saves eighty times more.

**The NAT exists because the databases are private.** USD 43 a month buys
the property that no database in this pilot is reachable from the internet.
The alternative priced honestly: AWS's own VPC sample for AgentCore uses 13
interface endpoints, and interface endpoints bill **per availability zone** —
at roughly USD 0.013 per endpoint-AZ-hour (the one rate here taken from
AWS's published PrivateLink page rather than the price list), 13 endpoints
across 3 AZs is about **USD 370 a month**. The NAT does the same job for
USD 43. The cheaper-still option is to make the databases publicly
accessible, which is not an option the brief leaves open.

## One-off

| | |
|---|---|
| Loading and masking 1,341 tickets | USD 0.00 (no model calls — the gate is regex) |
| Embedding the corpus, four chunking strategies | USD 0.04 |
| Retrieval bake-off, 4 × 4 strategies | USD 0.31 |
| Gate runs G1–G7, full set | USD 1.86 |
| **Build total** | **≈ USD 2.21** |

## Payback

The pilot costs about **USD 87 a month** to run and replaces work costing
**USD 90,450 a month**. If it handles even 2% of the queue end to end it
saves USD 1,809 a month, which is twenty times its own cost. Payback on the
build spend is immediate; payback on the six weeks of engineering time is
the only number that matters, and that is a conversation about salaries, not
about AWS.

## What would change these numbers

- **Volume.** Nothing here scales with tickets until roughly 500,000 a
  month, where Bedrock's bill reaches the NAT's.
- **Stopping the databases when idle.** Two instances at USD 0.025/hr cost
  nothing while stopped. A pilot that runs business hours in two regions
  could halve the RDS line.
- **One region instead of two.** The split is a data-residency requirement,
  not a technical one. It costs USD 21 a month to honour.
- **A stronger drafter.** Claude Haiku 4.5 at USD 0.001/0.005 per 1K is
  about 16× Nova Lite per token — which would take the model line from
  USD 1.29 to roughly USD 21 a month. Still a rounding error against the
  infrastructure, so if a stronger drafter measurably improves G5, cost is
  not a reason to refuse it.
