# Results

Everything here was measured against the real export on 19 November 2026.
Where a number is a sample, it says so and carries an interval.

## The gates

| Gate | Measures | Pass mark | Measured | |
|---|---|---|---|---|
| G1 | personal data removed before any model | 0 leaks | 0 leaks, 888 identifiers removed from 785 tickets | pass |
| G2 | routing accuracy vs where humans filed it | 95% | **90.0%**, 95% CI 86.6–92.5%, n=399 of 1,341 | **fail** |
| G3 | duplicates merged, none merged wrongly | 0 wrong merges | 112 clusters, 0 wrong merges, 12.6% reduction | pass |
| G4 | injections blocked before a model call | 100% | 17/17, 0 false positives on 40 benign tickets | pass |
| G5 | Ragas faithfulness | 0.90 | **0.814** (from 0.756) | **fail** |
| G5 | date-dependent answers correct | 5/5 | 5/5 | pass |
| G6 | output check catches bad drafts | 6/6 planted | 6/6, plus 4 real drafts withheld with reasons | pass |
| G7 | cost per draft | USD 0.02 | USD 0.00025 | pass |
| G8 | cache may not serve a date it was not true for | 0 stale | 0 wrong-period, 4/4 reused inside window | pass |

Retrieval quality, measured by Ragas on Bedrock: context precision 0.89,
context recall 0.93.

## The three failures

**Routing, 90.0% against 95%.** It is better than what exists — the intake
dropdown gets 86.0% right on the same tickets — and it fixes 50 of the 56
misroutes a human had to undo by hand. But the interval's upper bound is
92.5%, so the sample settles the question rather than inviting another hour
of sweeping.

The loss is not spread evenly. Six of the nine queues are at 100%. Three
carry all of it:

| Queue | Recall | n |
|---|---|---|
| Payroll-Ops | 68.1% | 69 |
| Integrations | 70.3% | 37 |
| Statutory-Compliance | 79.4% | 34 |

Classifying 150 more tickets drawn only from those three queues says what
they are confused *with*, which is the part worth knowing:

| Confusion | Rate |
|---|---|
| Integrations → Access-Support | 13/39 |
| Payroll-Ops → Reports-Analytics | 8/76 |
| Payroll-Ops → Integrations | 7/76 |
| Payroll-Ops → Statutory-Compliance | 6/76 |
| Statutory-Compliance → Integrations | 5/35 |

One rule explains all five rows. **The classifier routes by the symptom the
customer reports; the humans route by the system that has to change.** A
failing SFTP connector is reported as "I can't log in", so it reads as
Access-Support and is filed under Integrations. A payroll run that produced
a wrong figure is reported as "the report is wrong", so it reads as
Reports-Analytics and is filed under Payroll-Ops. The ticket text genuinely
describes the symptom; only the triage SOP knows the mapping.

That is a prompt and SOP problem rather than a model-capability one, and the
fix is to state the rule explicitly — route by the system that must change,
not by the symptom described — and give the three confusable pairs as worked
examples.

**This fix is not applied.** The hypothesis is untested, and shipping an
unmeasured change to the production classifier would be worse than leaving
the gate honestly failing. It needs one re-measure against the same seeded
sample, which is about thirty minutes of the account's 20 rpm quota. The
diagnosis is the deliverable here; the fix is the next session's first job.

**Faithfulness, 0.814 against 0.90.** It moved from 0.756 when the output
check began withholding pointer answers, and that is as far as that fix
goes. Retrieval is not the cause: precision 0.89 and recall 0.93 say the
system finds the right document and then drifts inside it. The drafting
model is Nova Lite, chosen because every Anthropic model on this account
returns "Model use case details have not been submitted". A stronger drafter
is the obvious next experiment and is currently blocked on a console form.

## G7 latency: measured wrong twice, then measured properly

The sweep first reported 48 seconds, then 25, against a 3-second mark, and
neither number was the pilot. Bedrock gives this account 20 requests a
minute; the sweep asks for more, so most of those seconds were the eval
harness asleep in a rate limiter.

The first attempt to separate the two was wrong in an instructive way. It
summed each box's own timing on the assumption that box time was work time.
It is not: the limiter sleeps *inside* the call the box is timing, so every
box carries its own wait and the sum changed nothing.

The measurement that settles it runs through the deployed public endpoint,
sequentially, at production pace, with the cache disabled so every request
does the full job. Nothing queues, because production volume is 1.4 requests
a minute against a 10–20 rpm quota. Twelve requests:

| | p50 | p95 |
|---|---|---|
| Wall, what a customer waits | 5.50 s | **6.17 s** |
| The pipeline's own work | 1.28 s | 1.43 s |
| Platform invoke path | 4.26 s | 4.79 s |

**The gate fails, and the pipeline is not why.** Its nine boxes finish in
1.43 seconds at p95, comfortably inside the mark. The remaining 4.8 seconds
is the path a request takes to reach them: API Gateway, then Lambda, then
the AgentCore invoke call. Three quarters of what the customer waits for is
spent before any of this code runs.

That makes the fix an infrastructure one rather than a pipeline one — keep
the runtime warm, or drop the Lambda hop and let API Gateway reach AgentCore
directly. Neither is attempted here, and a faster prompt would not move this
number by a tenth of a second.

## Chunking and retrieval, chosen by measurement

35 golden questions, k=5, scored with set arithmetic rather than a judge
model so the measurement cannot flatter itself.

| Chunking × Retrieval | hit@5 | MRR | precision | date correct |
|---|---|---|---|---|
| **D_heading_context × R4_hybrid_dated** | 0.97 | 0.87 | 0.46 | **1.00** |
| D_heading_context × R3_hybrid | 0.97 | 0.92 | 0.50 | 0.40 |
| D_heading_context × R1_dense | 0.97 | 0.89 | 0.51 | 0.40 |
| D_heading_context × R2_lexical | 0.23 | 0.23 | 0.21 | 0.00 |

The second row is the interesting one. By every metric a retrieval bake-off
normally reports, plain hybrid beats the winner — better MRR, better
precision. It is also wrong on three of the five date-dependent questions,
which are exactly the questions that caused the incident this pilot exists
to prevent.

A hypothesis died here too: lexical retrieval scored 0.23. Payroll tickets
say "salary not credited"; the rules say "wage ceiling". Keyword matching had
nothing to hold on to. It is kept in the bake-off because a disproved
hypothesis is worth more written down than quietly deleted.

## Six defects that measurement found and review did not

1. **The abstain threshold compared two different scales.** Rank-fusion
   scores sit near 0.016 and the floor was a 0.55 cosine similarity, so
   every hybrid result looked irrelevant and everything escalated.
2. **Similarity cannot separate answerable from unanswerable.** Measured on
   the golden set: answerable 0.20–0.65, unanswerable 0.28–0.50. The
   distributions overlap across nearly their whole range. Abstention moved
   to the model, with code verifying every claim is cited.
3. **The prior-period chunk named its own replacement.** A chunk describing
   June also said "replaced by INR 25,000", and the model drafting a June
   answer quoted it.
4. **The cache ignored time.** A September question matched a June one at
   0.958 and was served June's answer — the original incident, reintroduced
   by an optimisation. Now gate G8.
5. **A cited pointer passed as an answer.** "The rate is in KB-0140, see
   Confluence" satisfied the citation check while telling the customer
   nothing. Also the largest single cause of the faithfulness score.
6. **The PII gate understated its own work.** Notes buried in event
   histories were masked correctly and then reported as "no personal data
   found" — the direction of error nobody notices.

Four of the six were found by a test the code had just passed.

## Cost

Priced from the AWS Price List API for `ap-southeast-2`, published
2026-11-11. See [COST.md](COST.md).

| | |
|---|---|
| Today, per ticket | USD 6.03 |
| Pilot, per ticket answered | USD 0.000086 |
| Pilot, monthly all-in at 15,000 tickets | ≈ USD 87 |
| Of which models | USD 1.29 (1.5%) |

Ninety-seven percent of the run cost is always-on infrastructure. Doubling
ticket volume changes the bill by about a dollar.

## G9: dropping stubs before the draft, and what it did not buy

KB index entries are now removed from the retrieved set before the draft
call, instead of being caught after generation by output check C7. The
implementation is correct and G9 pins four properties, including the one
that would have failed silently: `[1]` in a citation must mean the first
*surviving* passage, not the first retrieved one.

The predicted benefit did not materialise, and the measurement says why.

| | before | after |
|---|---|---|
| wall p50 / p95 | 5.50 s / 6.17 s | 5.34 s / 6.77 s |
| pipeline p50 / p95 | 1.28 s / 1.43 s | 1.31 s / 1.52 s |
| prompt tokens saved | — | ~38 of ~1,541 (2.5%) |

Latency is unchanged within noise. The saving was supposed to come from
abstaining before paying for a draft, and **that path essentially never
fires**: it needs every passage in the top 5 to be a stub, but stubs are
only 43% of the Indian corpus, so a substantive chunk always fills a slot
even when it is irrelevant. The condition is close to unreachable at k=5.

What did improve is honesty. Asked for the ESI rate, the pilot used to read
a stub and draft "the rate is available in KB-0140", which C7 then withheld.
It now says it cannot provide the rate. Same routing decision, same cost, a
better reason — and stub text can no longer mislead the model into
manufacturing a pointer.

The change is kept because noise in a grounding prompt is categorically
wrong, not because it paid for itself. It did not.

The measurement also sharpens the top recommendation. **All 24 KB articles
have no body** — the knowledge base is 100% pointers. Every KB-shaped
question is unanswerable no matter what the pipeline does, and 43% of the
index is occupied by chunks that cannot ground an answer, which also costs
retrieval precision. Getting the Confluence bodies is worth more than any
remaining pipeline work.

## Does filtering stubs in SQL help? Yes, but only the ordering

Measured on the deployed runtime (the databases are private, so the sweep
runs inside the VPC where the data already is), 35 golden questions, same
set arithmetic, the only variable being the groundable predicate.

| Strategy | filter | hit@5 | MRR | nDCG@5 | precision@5 |
|---|---|---|---|---|---|
| R1_dense | off | 0.97 | 0.89 | 0.91 | 0.51 |
| R1_dense | **on** | 0.97 | 0.91 | 0.93 | 0.51 |
| R3_hybrid | off | 0.97 | 0.92 | 0.93 | 0.50 |
| R3_hybrid | **on** | 0.97 | 0.95 | 0.95 | 0.50 |
| **R4_hybrid_dated** (production) | off | 0.97 | 0.89 | 0.91 | 0.46 |
| **R4_hybrid_dated** (production) | **on** | 0.97 | **0.95** | **0.95** | 0.46 |
| R2_lexical | off | 0.23 | 0.23 | 1.00 | 0.21 |
| R2_lexical | **on** | 0.23 | 0.23 | 1.00 | 0.21 |

The production strategy gains **MRR 0.89 → 0.95 and nDCG 0.91 → 0.95**. The
right document moves up the ranking because stubs no longer occupy the slots
above it. The filter is kept on that evidence.

Three things did **not** happen, and each is worth more than the gain.

**hit@5 did not move.** It was already 0.97. The right document was always
being retrieved; stubs were burying it, not excluding it.

**precision@5 did not move.** Removing a stub frees a slot that the next
chunk immediately fills, so the ratio is unchanged. This measure is also
chunk-level, not document-level: every golden question has exactly one gold
document, and a chunked document contributes several rows, so 0.46 means
roughly half the retrieved chunks come from the right document. It is not
the pile of slack an earlier draft of this document implied.

**Lexical retrieval was completely unaffected** — 0.23 before and after, to
two decimals. The hypothesis was that keyword-dense KB titles were winning
lexical matches and poisoning that arm. The data says no. Lexical fails here
because `tsv @@ plainto_tsquery` simply does not match at all on 77% of
these questions: tickets say "salary not credited", rules say "wage ceiling".
Its nDCG of 1.00 is a trap — when it retrieves anything the hit is at rank
one, but it usually retrieves nothing relevant.

## Was a re-ranker used? No. Would a cross-encoder help?

No re-ranker is used. Retrieval is dense (Titan Embed v2, HNSW cosine) and
lexical (Postgres `ts_rank_cd`) fused with Reciprocal Rank Fusion at k=60.
Fusion reorders; it does not re-score a query against a passage jointly, so
it is not a cross-encoder in any sense.

**No re-ranker model is available on this account.** `ap-southeast-2` offers
no `amazon.rerank` or `cohere.rerank`, only Cohere embedding models, and the
account SCP pins the pilot to that one region. Self-hosting a cross-encoder
in the runtime would mean shipping Torch and a model into a 24 MB artifact
and paying CPU inference on the request path.

The viable substitute is an LLM re-ranker: one Nova Micro call scoring 15
candidates and returning the best 5. It is affordable, roughly USD 0.00002
and 300 ms.

It is not recommended, because the numbers say it would optimise the part
that already works. A re-ranker improves ordering, and ordering is now MRR
0.95 and nDCG 0.95 with hit@5 at 0.97 — about 0.05 of headroom. Meanwhile
faithfulness sits at 0.814 against a 0.90 mark, and 300 ms would land on a
request path already carrying 4.8 s of platform overhead.

The retrieval stack is not the bottleneck. The knowledge base having no
bodies is.

## A correct citation, a current document, and the wrong number

Found while building the period-comparison control, and the most serious
defect in the pilot so far. It is the brief's original incident, reproduced
with every date gate working.

Ask the EPF wage ceiling question for a September period, but with a subject
line that mentions June, and the reply came back:

    The EPF wage ceiling for June payroll is INR 15,000 per month [1].

INR 15,000 is the superseded figure. The correct September answer is 25,000.
And yet:

* retrieval returned the **right** document, R-IN-PF-2026-08;
* that document is genuinely **in force** from 2026-08-01, so the date
  filter was correct to return it;
* the citation **points at it correctly**;
* the grounding check **passed**, because the number really was in the cited
  passage.

Every gate did its job and the answer was still wrong. The cause was in the
chunk text:

    The value in force is: INR 25,000 per month. This replaced: INR 15,000
    per month.

One chunk, two numbers, only one of them the answer. A question phrased
around the earlier period pulls the model to the wrong one, and because that
figure is in the cited passage, nothing downstream can tell.

What makes this worth writing down: the identical bug had already been found
and fixed on the *prior-period* row, and the comment explaining the fix was
sitting four lines below the line that still had it. Fixing a bug on one side
of a symmetry is not fixing it.

### The fix

The superseded value is no longer carried on the in-force chunk. It is not
lost — the prior period is its own row with its own date window, which is
what makes "what applied in June" a lookup rather than an inference.

Applied to the live corpus through a runtime migration: 16 statutory chunks
rewritten **and re-embedded** (the text is what was embedded, so editing it
without re-embedding would leave retrieval matching a sentence that is no
longer there), and 23 cache entries dropped, since they had been drafted
from the old text and would otherwise have kept serving the old answer.

### Verified after

| case | before | after |
|---|---|---|
| June-flavoured subject, September period | INR 15,000 ✗ | **INR 25,000** ✓ |
| June-flavoured subject, June period | INR 15,000 ✓ | **INR 15,000** ✓ |
| neutral subject, September period | INR 25,000 ✓ | **INR 25,000** ✓ |

The June case still answers 15,000, from the prior-period row. The fix
removes the trap without flattening the history.

### No retrieval regression

Re-measured on the 35-question golden set through the runtime, R4_hybrid_dated:

| | after | on record |
|---|---|---|
| hit@5 | **1.00** | 0.97 |
| MRR | **0.98** | 0.95 |
| nDCG@5 | **0.98** | 0.95 |

Scored over the **30 answerable** questions. The first pass scored all 35 and
read 0.86/0.84/0.84 — the five misses were all `kind=unanswerable`, questions
with no correct document by construction. They belong to the abstention
measure, not this one; counting them here would mark correct abstentions as
retrieval failures. All 7 statutory questions still retrieve their rule.

### What this says about the gates

The citation check answers "is this claim in the cited passage". It cannot
answer "is this the passage's *operative* claim". That gap is not closable by
checking harder at output time — it closes at indexing time, by not putting
two competing answers in one retrievable unit.
