## Submission: Shivam  ·  Cloud used: AWS (`ap-southeast-2`)

**Repo:** https://github.com/shivam-25/ramco-payce-pilot · **Live pilot:** https://ramco-payce-pilot.vercel.app · **Diagram + deck:** [`docs/deck`](https://github.com/shivam-25/ramco-payce-pilot/tree/master/docs/deck) · **Full results:** [`docs/RESULTS.md`](https://github.com/shivam-25/ramco-payce-pilot/blob/master/docs/RESULTS.md)

🧭 **Approach.** Rules in code, judgement in the model: PII, injection, money and dates are decided by code that can be tested, and only the drafting is left to a model. Every box can refuse. Each gate is a number with a pass mark, run on the real export rather than on examples I wrote, and the two gates that fail are reported as failures with the cause measured.


📝 **Corrected 20 Nov.** An earlier version of this submission reported coverage of **51.7%**. Testing the console by hand afterwards caught the pilot stating product behaviour that no passage supports — *"the payroll system does not support exporting all payslips as a single zip file"*, cited to a runbook that never says so. With that forbidden, and tracker notes carrying nothing a customer can use no longer indexed, the honest figure is **25.0%**. The full ladder and the three findings behind it are in [RESULTS.md](https://github.com/shivam-25/ramco-payce-pilot/blob/master/docs/RESULTS.md).

---

### 1. Architecture and what changed from my A0

Ten boxes in order — PII gate → normalise → lookup → injection screen → classify → dedupe → router → cache → answer with citations → output check — on Bedrock AgentCore Runtime in a VPC, one Postgres + pgvector per region. Browser → API Gateway → Lambda → AgentCore; no AWS credential exists on the hosting side.

Two changes from A0. **One AWS region, two databases:** an SCP on this account denies every regional action outside `ap-southeast-2`, so India and UAE are separate instances with separate data — a constraint of the account, documented rather than hidden. **A tenth box, the cache, exists because of a defect:** a semantic cache matched on meaning and ignored time, so a September question was served June's answer — the exact incident this pilot exists to prevent, reintroduced by my own optimisation. Entries now carry a validity window, guarded by gate G8.

### 2. What I built

All ten boxes, end to end and deployed. PII gate (regex + roster name matching, runs first on every field including event histories), normalise, lookup (unknown customer ⇒ hand-off, never a guess), injection screen (on masked text, before any model call), classify (Nova Micro, nine queues), dedupe (customer + canonical category + 24h window), router (money, P1, injection and unknown customer always go to a person), answer (pgvector HNSW + Postgres full-text fused with RRF, Nova Pro drafts with citations), human hand-off (carries the reason, no draft), output check (eight checks before anything is shown).

**Data loaded**

| Source | Rows loaded | Region |
|---|---|---|
| `tickets_export.json` | 1,288 of 1,367 rows (1,353 distinct ids; 14 appear twice) | 912 IN · 376 UAE |
| …tickets whose customer is not in `customers.csv` | 65 — loaded to neither, they route to a human | — |
| `customers.csv` | 60 | 42 IN · 18 UAE |
| `statutory_rules.json` | 4, with effective dates | 2 IN · 2 UAE |
| `process/*.md` (runbook, SLA, triage SOP, escalation policy) | 4 documents, chunked four ways | both |
| `jira_escalations.json` | 38 of 62 — resolved, with a written root cause, customer names stripped | both |
| `kb_articles.json` | 24 rows, **0 usable** — every body is empty, pointing at Confluence | both |

Chunking and retrieval were chosen by bake-off, not assumption: four chunking strategies × four retrieval strategies, scored with set arithmetic and no model judge. Heading-split chunks prefixed with their document, region and effective dates won (hit@5 0.97, MRR 0.87). Plain hybrid beat date-aware hybrid on MRR while getting 3 of 5 dated questions wrong — which is why the winner is not the top row of the table.

Every ticket is masked before it is written; the loader raises rather than insert an unmasked row.

### 3. Eval results

| Gate | Target | Result |
|---|---|---|
| G1 PII gate | 0 leaks | ✅ **0 leaks**, 888 identifiers removed from 785 tickets |
| G2 routing | 95% | ❌ **90.0%** (CI 86.6–92.5, n=399) vs 86.0% intake baseline; fixes 50 of 56 misroutes |
| G3 dedupe | 0 wrong merges | ✅ **112 clusters, 0 wrong merges** |
| G4 injection | 100%, before a model | ✅ **17/17**, 0 false positives on 40 benign |
| G5 faithfulness (Ragas) | 0.90 | ❌ **0.789** · precision 0.90, recall 0.89 |
| G5 dated answers | 5/5 | ✅ **5/5** |
| G6 output check | 6/6 planted | ✅ **6/6**, plus 4 real drafts withheld |
| G7 first reply p95 | 3 s | ✅ **2.51 s** (vendor baseline 19 s) |
| G7 cost per draft | USD 0.02 | ✅ **USD 0.00050** |
| G8 cache vs policy change | 0 stale | ✅ **0 wrong-period**, 4/4 reused in window |

Eight pass, two do not, and the two failures are the most useful rows in the table.

### 4. Cost

| Today, per ticket | Pilot run, per month | One-off build | Payback |
|---|---|---|---|
| **USD 6.03** (USD 90,460 ÷ 15,000) | **≈ USD 87** at 15,000 tickets | **≈ USD 2.21** | immediate on spend |

97% of the bill is always-on infrastructure — NAT USD 43, RDS two regions USD 36.50, storage USD 5.52. The models are **USD 1.29 of USD 87**, so optimising prompts here would save forty cents and waste a week. At 2% of the queue handled end to end it saves USD 1,809 a month against USD 87 to run.

### 5. What did not work

- **G2 stalls at 90%, not 95%.** Payroll-Ops (68%) and Integrations (70%) are genuinely ambiguous against Statutory-Compliance. Two prompt rewrites made it worse.
- **A trained router lost to the prompt.** TF-IDF + linear SVM scored **76.4%** against the prompt's 90.0% — worse than the intake dropdown. The first run said 100.0%, which was the useful part: those 1,341 tickets are only **402 distinct texts**, so an id-based split let it memorise. There is no labelled dataset here, there are 402 labelled sentences wearing 1,341 hats.
- **G5 is 0.789, and the judge is the drafter.** Nova Pro grades its own work, because Claude is blocked on this account and the design called for a judge a class above the writer. It is not a theoretical worry: asked September's EPF ceiling, the pilot answered **INR 25,000** citing the passage that says exactly that — the founding question of the case, answered correctly — and Ragas scored it **0.0**. Read the rows, not the mean.
- **One faithfulness failure is mine.** My own output check forbids naming an unshipped fix version, so the model falls back on "a fix is in progress", which no passage supports. The leak fix bought an unsupported claim; that answer should escalate instead.
- **The KB has no bodies.** All 24 articles are index entries pointing at Confluence, so the coverage ceiling is in the data, not the pipeline. I indexed the resolved escalation tracker instead and reported a lift from **43.3% to 51.7%** — then found that number was bought with sentences the corpus does not support. 36 of the 38 tracker notes are an engineer's verdict ("could not reproduce", "fixed in next release"), the prompt was asking the model to promise customers a fix, and in the worst case the pilot was inventing product behaviour: *"The payroll system does not support exporting all payslips as a single zip file"*, cited to a runbook that says nothing of the kind. With unusable notes dropped and ungrounded capability claims forbidden, the honest figure is **25.0%**. The 18-point drop is how much confident guessing the coverage metric had been counting as success. Full working in [RESULTS.md](RESULTS.md).
- **Retrieval was not worth tuning.** I measured before building: recall@5 is 30/30 and MRR@5 0.973 on the golden set, so a cross-encoder, a pre-filter or parent-child chunking had about 0.03 MRR to win. The loss is generation and coverage.

🚧 **Unfinished.** Judge independence (blocked on the Anthropic use-case form, not on engineering) and a UAE PDPL DPIA. G2 and G5 remain below their marks and I would rather submit them failing with the cause measured than tuned until they pass.
