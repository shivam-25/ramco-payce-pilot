# Ramco Payce Pilot — Build, Test and Cost Plan

**Assignment:** [C3-Hub discussion #93](https://github.com/FDE-Academy-Hub/C3-Hub/discussions/93)
**Owner:** Shivam Dixit · **Cloud:** AWS (account `<account>`, project region `ap-southeast-2`)
**Builds on:** `../../26th_oct/ramco-payce-a0-architecture-submission.md` (A0→A3)

---

## 0. The numbers this pilot must hit

| Gate | Target | Where it is tested |
|---|---|---|
| G1 PII gate | **0 leaks** | `evals/test_g1_pii.py` · 104 real tickets carrying identifiers |
| G2 Classify / route | **≥95%** | `evals/test_g2_routing.py` · 1,367 tickets, gold labels |
| G3 Dedupe | one client = one case | `evals/test_g3_dedupe.py` · repeat-incident set |
| G4 Injection | caught **before** the model | `evals/test_g4_injection.py` + Promptfoo CI · PN-101129 + hidden lines |
| G5 Answer (RAG) | **faithfulness ≥0.90**, right document | `evals/test_g5_rag_ragas.py` · Ragas, stale-KB set |
| G6 Output check | no false / biased / harmful draft | `evals/test_g6_output.py` · draft replies |
| G7 Speed & cost | **first reply ≤3 s p95**, **≤USD 0.02 / draft** | `evals/test_g7_speed_cost.py` · peak-week replay |

No gate for Normalise or Lookup — they do not drive the problem.

**Cost benchmark (recounted from `monthly_cost_sheet.csv`):**
USD 81,060 people + USD 9,400 tools = **USD 90,460 / month ÷ 15,000 tickets = USD 6.03 a ticket**
(USD 6.16–6.73 including SLA credits). Vendor baselines to beat: **19 s p95**, **USD 41,200** in June.

---

## 1. Decisions taken, and why

### D1 — Two regional databases, one AWS region *(forced deviation, documented)*
The brief asks for one Postgres + pgvector **per region (India, UAE)**. This AWS project
is governed by an SCP that **denies every regional action outside `ap-southeast-2`**
(verified: `us-east-1` call returns `explicit deny in a service control policy`).

**What is built instead:** two *separate RDS PostgreSQL instances* — `ramco-in` and
`ramco-uae` — with separate credentials, separate security groups, separate
Secrets Manager secrets, and a hard `region` guard in the router. No row of one
region's data is reachable from the other connection.

**What this proves and what it does not.** It proves the isolation *boundary* and
the two-connection routing. It does **not** prove data residency, because both
instances sit in Sydney. In production the same CloudFormation template deploys to
`ap-south-1` and `me-central-1` with no code change. This is listed under
"what did not work" in the submission — it is a constraint of the account, not a
design choice.

### D2 — Models
| Job | Model | Why |
|---|---|---|
| Classify | `apac.amazon.nova-micro-v1:0` | cheapest token price available; classification is an easy task |
| Draft answer | `au.anthropic.claude-haiku-4-5-20251001-v1:0` | best accessible Claude; drives the USD 0.02/draft gate |
| Embeddings | `amazon.titan-embed-text-v2:0` @ 512 dims | 512 dims keeps the pgvector index small and is enough for a 60-chunk corpus |
| Ragas judge | `au.anthropic.claude-haiku-4-5-20251001-v1:0` | a *different, stronger* judge is preferred — see blocker B1 |

**Verified working** by direct `bedrock-runtime converse` calls. Claude Haiku 5.5 /
Sonnet 5.5 are listed in the account but return `AccessDenied` — they need the
Anthropic use-case form (blocker B1).

### D3 — Compute: Bedrock AgentCore Runtime
A second AgentCore runtime (`ramco_payce_pilot`) alongside the existing `kiosk`
runtime, using the same proven pattern: FastAPI app → `runtime.py` launcher →
Linux ARM64 / Python 3.12 ZIP → `CreateAgentRuntime`. Serverless, pay per
invocation, nothing idling.

**Open item:** AgentCore `PUBLIC` network mode cannot reach a private RDS instance.
Resolution order: (a) AgentCore VPC network mode if the account supports it;
(b) fall back to Aurora Serverless v2 + RDS Data API (HTTP, IAM-auth, no VPC).
Decided at build step 2 on the evidence, not now.

### D4 — Chunking: decided by measurement, not by taste
The corpus is small and oddly shaped: 24 KB articles that are **metadata only**
(`excerpt` = "See full article in Confluence space SUP-KB." — there are no bodies),
4 process documents (~1,200 words total), 4 statutory rules. Four strategies are
built and scored on the same golden set:

| | Strategy | Hypothesis |
|---|---|---|
| **C-A** | whole document, no splitting | corpus is tiny; splitting may only destroy context |
| **C-B** | fixed 512 tokens / 64 overlap | the default everyone reaches for; the control |
| **C-C** | markdown heading-aware | process docs are headed; sections are the real unit |
| **C-D** | heading-aware + one-line contextual prefix | fixes the "which document is this from" failure |

Winner chosen on **context_precision** and **context_recall** (Ragas). The result
is reported with the losing numbers, not just the winner.

### D5 — Retrieval: also decided by measurement
| | Strategy | Hypothesis |
|---|---|---|
| **R-1** | dense only (pgvector HNSW, cosine) | the default |
| **R-2** | BM25 only (Postgres `tsvector`) | statutory queries are keyword-shaped ("PF ceiling", "WPS SIF") |
| **R-3** | hybrid, Reciprocal Rank Fusion | expected winner on a mixed corpus |
| **R-4** | hybrid RRF **+ `as_of` date filter + region filter** | the only one that can answer a June question with the June rule |

R-4 is expected to win **not on relevance but on correctness**: the PF wage ceiling
rose from INR 15,000 to 25,000 on 2026-08-01, so a June payslip question answered
with the July rule is wrong even when faithfulness scores perfectly. That is the
exact failure the vendor pilot shipped (postmortem §2) and the reason effective
dates are columns, not embeddings.

### D6 — Cost posture
Free-plan account with **USD 99.98 of credits remaining**. Everything is sized to
stay inside it: `db.t4g.micro` instances (free-tier class), 20 GB gp3, single-AZ,
no NAT gateway, no ElastiCache (the semantic cache lives in the same Postgres,
as the study note recommends — one database, one hop), AgentCore serverless, and
**both RDS instances stopped when not building or demoing**.

### D7 — Frontend
Next.js on Vercel (GitHub `shivam-25`), calling a **Lambda Function URL** with a
shared-secret header, which invokes the AgentCore runtime with SigV4.
No AWS credentials ever reach Vercel. The 12-hour session credentials this
machine uses would expire on Vercel anyway.

---

## 2. Build order

Smoke-test after each step.

| # | Step | Output |
|---|---|---|
| 1 | Data audit + golden dataset build | `evals/golden/*.jsonl`, counts verified |
| 2 | Infra: 2× RDS + pgvector + Secrets Manager + S3 artifact bucket | CloudFormation stack |
| 3 | Schema + load: tickets, customers, KB, rules, chunks, cache, audit | row counts per region |
| 4 | Box 1 PII gate (plain code) → **G1** | 0 leaks on 104 tickets |
| 5 | Box 2 Normalise, Box 3 Lookup (no gate) | 3 schemas → 1; tier/region/SLA joined |
| 6 | Box 4 Classify (Nova micro) → **G2** | ≥95% on 1,367 |
| 7 | Box 5 Dedupe → **G3** | one client = one case |
| 8 | Box 6 Router (code, not a prompt) + injection screen → **G4** | caught before the model |
| 9 | Chunking + retrieval bake-off (C-A..D × R-1..4) | `evals/reports/retrieval_bakeoff.md` |
| 10 | Box 7 Answer: RAG + citations + `as_of` → **G5** | Ragas faithfulness ≥0.90 |
| 11 | Box 8 Output check → **G6**, Box 9 Human hand-off | audit row per draft |
| 12 | Semantic cache (same Postgres, threshold 0.90) | hit rate measured |
| 13 | Package + deploy AgentCore runtime | runtime READY, invoked |
| 14 | Peak-week replay → **G7** | p95 and USD/draft |
| 15 | Promptfoo in CI (GitHub Actions) | PR blocked on failure |
| 16 | Next.js UI → Vercel | public URL |
| 17 | Cost sheet: today / run / one-off / payback | `docs/COST.md` |
| 18 | Screenshots via Claude in Chrome + deck | `docs/presentation/` |

---

## 3. What I need from you

| | Item | Status |
|---|---|---|
| **B1** | **Bedrock: Anthropic use-case form.** Haiku 5.5 / Sonnet 5.5 return `AccessDenied` until it is submitted in the Bedrock console (Model access → Anthropic). Takes two minutes. Without it the Ragas judge is the *same* model that writes the drafts, which is a weaker eval and I will flag it in the submission. | **needed** |
| **B2** | **Ragas API key — not needed.** Ragas is an open-source library with no hosted key. It needs a judge LLM and an embedder, and both come from Bedrock on this account. Nothing to buy. | resolved |
| **B3** | **Vercel.** The CLI is not installed and no token is present. I will deploy with `npx vercel` — you will get one browser login prompt, or paste a token. | **needed at step 16** |
| **B4** | **GitHub.** `gh` is already authenticated as **shivam-25**. I will create `ramco-payce-pilot` as a **private** repo (case data must stay off public services, per the brief). | ready |
| **B5** | **Spend.** USD 99.98 credits remain. Planned burn is single-digit dollars; I will report actuals. Say the word if you want a lower ceiling. | assumed OK |

---

## 4. Known risks

- **The deadline was 6 PM Fri 13 Nov 2026; today is 19 Nov.** This is late. Submission is still a single comment on the discussion, and it is yours to post.
- **KB articles have no bodies.** The 24 articles are metadata stubs. The answerable
  corpus is really the 4 process docs + 4 statutory rules. The pilot will answer
  from what exists and say so, rather than invent article text — this is itself a
  finding worth putting in the submission.
- **67 of 1,367 tickets reference a `customer_id` absent from `customers.csv`**, so
  they cannot be assigned a region. They route to human by default. Named, not hidden.
