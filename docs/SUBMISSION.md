**Submission: [your name]** · **Cloud and model used:** AWS `ap-southeast-2` · Amazon Nova Micro (classify), Nova Pro (draft + Ragas judge), Titan Embed v2 @ 512 dims

Repo: [link] · Live pilot: `ramco-payce-pilot.vercel.app` · Deck + full results: `docs/RESULTS.md`

---

### 1. Architecture and what changed from my A0

Diagram: `docs/deck` slide 3.

Nine boxes in order — PII gate → normalise → lookup → injection screen → classify → dedupe → router → cache → answer with citations → output check — on Bedrock AgentCore Runtime inside a VPC, reaching one Postgres + pgvector per region. The browser talks to API Gateway → Lambda → AgentCore; no AWS credential exists on the hosting side.

Two things changed from A0. **One AWS region, two databases**: an SCP on this account denies every regional action outside `ap-southeast-2`, so India and UAE are separate instances with separate data, not separate regions — a constraint of the account, documented rather than hidden. **A ninth box, the cache, exists because of a defect**: a semantic cache matched on meaning and ignored time, so a September question was served June's answer — the exact incident this pilot exists to prevent, reintroduced by my own optimisation. It now carries a validity window and gate G8 guards it.

### 2. Data loaded

| Source | Rows loaded | Region |
|---|---|---|
| `tickets_export.json` | 1,288 of 1,367 rows (1,353 distinct ids; 14 appear twice) | 912 India · 376 UAE |
| — tickets whose customer is not in `customers.csv` | 65, loaded to neither — they route to a human | n/a |
| `customers.csv` | 60 | 42 India · 18 UAE |
| `statutory_rules.json` | 4, with effective dates | 2 India · 2 UAE |
| `process/*.md` (runbook, SLA, triage SOP, escalation policy) | 4 documents, chunked 4 ways | both |
| `jira_escalations.json` | 38 of 62 — resolved, with a written root cause, customer names stripped | both |
| `kb_articles.json` | 24 rows, **0 usable** — every body is empty, pointing at Confluence | both |

Every ticket is masked before it is written; the loader raises rather than insert an unmasked row.

### 3. What I built

| Box | Built? | How |
|---|---|---|
| PII gate | yes | regex + roster-driven name matching, in code; runs first, on every field including event histories |
| Normalise | yes | code — channel, dates, category to a canonical taxonomy |
| Lookup | yes | Postgres, per region; an unknown customer is a hand-off, not a guess |
| Injection screen | yes | code, on the masked text, before any model call |
| Classify | yes | Nova Micro, nine queues |
| Dedupe | yes | same customer + canonical category + 24-hour window |
| Router | yes | code — money, P1, injection and unknown customer always go to a person |
| Answer | yes | pgvector HNSW + Postgres full-text, fused with RRF; Nova Pro drafts with citations |
| Human hand-off | yes | an escalation carries the reason and no draft |
| Output check | yes | eight checks (C1–C8) before anything is shown |

### 4. Eval results

| Gate | Target | Result | Test data |
|---|---|---|---|
| G1 PII | 0 leaks | **0 leaks**, 888 identifiers removed from 785 tickets | whole export |
| G2 routing | 95% | **90.0%** (CI 86.6–92.5, n=399) vs 86.0% intake baseline; fixes 50 of 56 misroutes | `g2_routing.jsonl`, 1,341 |
| G3 dedupe | 0 wrong merges | **112 clusters, 0 wrong merges** | repeat-incident set |
| G4 injection | 100% | **17/17**, 0 false positives on 40 benign | PN-101129 + planted |
| G5 faithfulness | 0.90 | **0.789** · precision 0.90, recall 0.89 | 35 golden questions |
| G5 dated answers | 5/5 | **5/5** | June/September set |
| G6 output check | 6/6 planted | **6/6**, 4 real drafts withheld | drafted replies |
| G7 latency p95 | 3 s | **2.51 s** (vendor baseline 19 s) | live endpoint |
| G7 cost per draft | USD 0.02 | **USD 0.00050** | measured usage |
| G8 cache | 0 stale | **0 wrong-period**, 4/4 reused in window | June/September |

Seven pass, two do not, and the two failures are the most useful rows in the table.

### 5. Cost

| Item | Value | How I got it |
|---|---|---|
| Today, per ticket | **USD 6.03** | USD 81,060 people + USD 9,400 tools = USD 90,460 ÷ 15,000 |
| Pilot run, per month | **≈ USD 87** | NAT USD 43, RDS two regions USD 36.50, storage USD 5.52, Bedrock USD 1.29 at 15,000 tickets |
| One-off build | **≈ USD 2.21** | embeddings USD 0.04, retrieval bake-off USD 0.31, gate runs USD 1.86 |
| Payback | **immediate on spend** | at 2% of the queue it saves USD 1,809/month against USD 87 |

Ninety-seven percent of the bill is always-on infrastructure. The models are USD 1.29 of USD 87 — optimising prompts here would save forty cents and waste a week.

### 6. What did not work, and what I would do next

| Item | Why |
|---|---|
| **G2 stalls at 90%, not 95%** | Payroll-Ops 68% and Integrations 70% are genuinely ambiguous against Statutory-Compliance. Two prompt rewrites made it worse. |
| **A trained router lost to the prompt** | TF-IDF + linear SVM scored 76.4% against the prompt's 90.0% — worse than the intake dropdown. The first run said 100.0%: those 1,341 tickets are only **402 distinct texts**, so an id-based split let the model memorise. There is no labelled dataset here, there are 402 labelled sentences wearing 1,341 hats. |
| **G5 is 0.789, and the judge is the drafter** | Nova Pro grades its own work because Claude is blocked on this account. It scored 0.0 on the pilot's founding question — September's EPF ceiling, answered correctly at INR 25,000, citing the passage that says so. Read the rows, not the mean. Next: unblock Claude and re-judge. |
| **One faithfulness failure is mine** | C8 forbids naming an unshipped fix version, so the model says "a fix is in progress" — which no passage supports. The leak fix bought an unsupported claim. An ungroundable known-issue answer should escalate instead. |
| **The KB has no bodies** | All 24 articles are index entries pointing at Confluence, so the coverage ceiling is in the data. Indexing the resolved escalation tracker lifted answered-without-a-person from **43.3% to 51.7%**; the rest needs the real Confluence export. |
| **Retrieval is not worth tuning** | recall@5 is 30/30 and MRR@5 0.973 on the golden set, so a cross-encoder, a pre-filter or parent-child chunking has about 0.03 MRR to win. The loss is generation and coverage. |
| **Judge independence and a UAE PDPL DPIA** | Both open, both blocked on approvals rather than engineering. |
