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

### Later: keeping it warm, and why the gate now passes

"Keep the runtime warm" turned out to name one line. The proxy Lambda called
`invoke_agent_runtime` with `runtimeSessionId=str(uuid.uuid4())` — a fresh
id per request. AgentCore routes same-session invokes to the same microVM and
holds it through 15 minutes of idle, so a new id every time *guarantees* a
miss. Every request paid a full cold start: interpreter, boto3 and psycopg
imports, a Secrets Manager fetch for the database password, a new Postgres
connection, and a full read of the customer table — all before box 1.

Measuring it first was worth doing, because the obvious change measured as
doing nothing at all:

| arm (old runtime) | wall p50 | pipeline p50 | overhead p50 |
|---|---|---|---|
| fresh session id per request | 5.54 s | 1.42 s | 4.22 s |
| one session id, reused | 5.55 s | 5.02 s | **0.44 s** |

Session reuse removed the cold start exactly as predicted — 4.22 s to 0.44 s
— and the wall clock did not move, because the saving was consumed by the
pipeline getting slower by the same amount. That second number is the rate
limiter: a fresh microVM starts with an empty token bucket, a reused one
inherits the previous request's reservation. At a 2 s pace that is a ~5.5 s
wait, and 5.02 s is what it measured.

So the two fixes only pay off together. The limiter is eval scaffolding and
is now off outside a sweep; with both in place, through the public endpoint:

| | before | after |
|---|---|---|
| wall p50 | 5.50 s | **2.05 s** |
| **wall p95** | **6.17 s** | **2.51 s** |
| pipeline p50 / p95 | 1.28 / 1.43 s | 0.82 / 1.08 s |
| overhead p50 / p95 | 4.26 / 4.79 s | 1.17 / 1.46 s |

**G7's latency target is met: 2.51 s at p95 against a 3 s mark**, from a 19 s
vendor baseline. The pipeline also got faster — the question's embedding is
now computed alongside the classifier rather than after it, and the limiter
no longer sleeps in production.

The measurement is in `scripts/latency.py`, so it reruns rather than being a
number I once saw.

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

## The routing fix, applied and measured twice. Both worse. Reverted.

The diagnosis on record — the classifier routes by the symptom the customer
reports, the humans route by the system that must change — was left unapplied
because it was untested. It has now been tested. It is correct, and acting on
it still made the gate worse, twice.

Same seeded stratified sample each time, 399 of 1,341 tickets, seed 20261119,
so the three runs are directly comparable.

| | on record | attempt 1 | attempt 2 |
|---|---|---|---|
| **Overall** | **90.0%** | 85.5% | 88.7% |
| Access-Support | 100 | 100 | 100 |
| Billing | 100 | 41.7 | 100 |
| General-L1 | 100 | 100 | 100 |
| Integrations | 70.3 | **97.3** | 70.3 |
| Leave-Attendance | 100 | 60.0 | 100 |
| Onboarding | 100 | 61.5 | 61.5 |
| Payroll-Ops | 68.1 | **84.1** | 78.3 |
| Reports-Analytics | 100 | 100 | 100 |
| Statutory-Compliance | 79.4 | 70.6 | 73.5 |

**Attempt 1** stated the rule explicitly and gave the four confusable pairs as
worked examples. On the queues it aimed at it worked, and not by a little:
Integrations 70.3 → 97.3, Payroll-Ops 68.1 → 84.1. It also pulled three
untargeted queues off 100%. The examples were vivid about "a wrong figure is
Payroll" and "a file that will not upload is Integration", and the model
duly applied them to invoices, leave balances and go-live migrations. A rule
was widened without being bounded.

**Attempt 2** bounded it: the four pairs became tie-breakers to be used only
after Billing, Leave & Attendance and Onboarding have been ruled out, since
those are defined by what the ticket is about rather than by where the wrong
data came from. Billing and Leave-Attendance returned to 100%. Onboarding did
not move at all. And the Integrations gain — the single largest improvement
either attempt produced — disappeared completely, back to exactly 70.3%.

That last number is the interesting one. Adding a precedence step in front of
the pairs did not merely scope them; it stopped them firing. The model reads
the guard, works through three exclusions, and by the time it reaches the
tie-breaker it has already settled. The instruction that fixed Integrations
and the instruction that protects Billing cannot both be live in a single
pass of a model this small.

Reverted to the prompt measuring 90.0%. Keeping 88.7% would have traded 1.3
points of the number the gate actually measures for two queues that look
better in a per-queue table nobody is graded on.

### What this cost and what it is worth

Two eval runs, about fifty minutes of the account's 20 rpm quota and USD
0.015 of classification. No production exposure: the change sat uncommitted
throughout and the deployed runtime, v13, predates it.

The honest read is that this is no longer a prompt problem. Three measured
attempts (the dropdown at 84.8%, and these two) say the mapping from symptom
to owning system is knowledge the triage SOP holds and the ticket text does
not contain. A model choosing one of nine labels from two sentences cannot
infer it, and writing more of the SOP into the system prompt trades one
queue against another rather than adding information.

Two options that would add information, neither attempted here:

* **Retrieve the SOP rather than paraphrase it.** The triage SOP is already
  in the corpus. Giving the classifier the three or four most similar SOP
  passages for the ticket in hand puts the actual mapping in front of it
  instead of a summary that has to cover all nine queues at once.
* **Treat it as a classification problem, not a prompting one.** 1,341
  tickets with human-assigned queues is a labelled training set. A small
  supervised classifier over the ticket text would learn the symptom-to-owner
  mapping from the data that already encodes it.

The gate stays honestly failing at 90.0%, against an intake dropdown at
84.8%. The pilot still fixes 43 of the 56 misroutes in the sample.

## An escalation is not a reply, and the drafter was too cheap

Two optimisations, found by chasing a number the console put on screen.

### The console said 50% of replies carried a citation. It was right to.

The brief wants 100% of replies carrying a verifiable citation. The new
scoreboard read 50%, which looked like a metric bug and was not.

When the drafting model cannot answer it is told to write ESCALATE. It does,
but it writes its reasoning first, and `answer()` returned all of it as the
draft:

    The current ESI employee contribution rate for India staff is not
    provided in the passages. Passage [1] discusses the EPF wage ceiling,
    and passage [3] discusses TDS slabs, but neither mention ESI rates.
    [...] ESCALATE

The console renders `draft` as the reply, so internal passage numbering and
the word ESCALATE sat in the field a support agent reads as text to send.
Worse, the `[1]` and `[3]` in that paragraph were being harvested as
citations — the passages the model was explicitly ruling *out* would have
been attached to a refusal as though they supported it.

Every other escalation path in `answer.py` returns an empty draft. This one
was the exception, and nothing downstream could catch it, because an
escalation that carries a draft still routes to a human and still looks
correct from the outside.

Fixed: an escalation returns no draft and no citations, and the model's
explanation becomes the reason the colleague picking it up reads. Measured
over the six console tickets afterwards: **2 of 2 replies carry a citation,
100%**, with four escalations correctly carrying nothing.

This did not move faithfulness — G5 already excludes escalated rows from
scoring, which was checked rather than assumed.

### The drafting model was chosen to save money that did not need saving

Cost per ticket is USD 0.00004 against the brief's cap of USD 4.50. That is
about a hundred thousand times of head room, and the pilot was running the
cheapest drafter on the account while faithfulness sat at 0.814 against a
0.90 gate.

Both drafters over the same 35 golden questions, same corpus, same retrieval,
drafted inside the VPC through a new runtime eval path and scored outside it:

| | Nova Lite | Nova Pro |
|---|---|---|
| answered | 29 | 29 |
| withheld by output check | 3 | 3 |
| **must_contain** | 4/5 | **5/5** |
| must_not_contain | 2/2 | 2/2 |
| **must_abstain** | 4/5 | **5/5** |
| ~~p50 latency~~ | ~~7.40s~~ | ~~7.40s~~ |
| ~~p95 latency~~ | ~~7.73s~~ | ~~7.88s~~ |
| cost per question | USD 0.000067 | USD 0.000537 |
| faithfulness (judged) | 0.699 | 0.817 |

`must_contain` and `must_abstain` are string checks against the golden set.
No model grades them, and they are the reason this change was made: the
judged numbers alone would not have been enough.

**The two latency rows are struck through because they measure nothing.** I
read the matching 7.40s as "the upgrade is free on speed". It is not a speed
at all. Neither Nova Lite nor Nova Pro appears in the rate limiter's RPM
table, so both fell through to its default of 10 requests a minute, and
60 / (10 × 0.8) is 7.5 seconds. Both arms were reporting the limiter's own
sleep, and they agreed to two decimal places because they shared a default
rather than because the models are comparably fast.

What should have caught it is the agreement itself: two models an order of
magnitude apart in price, matching to 0.01s on a wall-clock, is not a
finding, it is an artefact. A model id missing from a dict became a published
number. The limiter now prints a warning the first time it falls back to the
default, and no longer runs at all outside a sweep.

How the two drafters' latencies actually compare is unmeasured. It did not
move the decision, which rests on the two string checks.

**Three honest caveats.**

The judged faithfulness figures are *not* comparable to the 0.814 on record
further up this document. That was a different run under different
conditions; Lite scored 0.699 here. Only the within-run comparison holds.

The judge is Nova Pro, so the Pro arm is partly grading its own homework.
The same run puts a number on how much that matters: context precision and
recall are retrieval metrics, and both arms retrieved identically, so they
should have scored the same twice. They came back 0.934/0.904 and
0.895/0.942 — a judge-noise floor around ±0.04. The faithfulness gap of
0.118 is roughly three times that, but self-preference is inside it.

The deterministic wins are one question each out of five. Directional, not
conclusive.

**Adopted anyway**, because the evidence points one way on every measure that
does not depend on a judge, there is no measured downside — same answers
withheld, same latency — and the cost argument for Lite does not survive
contact with the cap.

The price is that the drafter and the judge are now the same model, which the
note in `config.py` previously warned against in as many words. G5's
faithfulness number is self-graded from here and should be read as
indicative rather than as a gate. When the Claude use-case form clears, move
the judge up a class first; that restores the gate and costs nothing else.

## Raising the coverage ceiling, and the two things it tried to leak

The 24 KB articles are the limit on how much this pilot can answer, and they
cannot be fixed from this export. Every one has an empty body and the excerpt
"See full article in Confluence space SUP-KB." There is nothing to ingest.
Writing the bodies would be inventing payroll guidance, which is the failure
the pilot exists to prevent, so the ceiling stays until Confluence is
ingested for real.

The escalation tracker is the one place in the export holding written
answers: 38 resolved issues with a human-written root cause. Indexed as a
`known_issue` source, measured by replaying 60 real tickets from the export
through the deployed endpoint, same seed both times:

| | without known issues | with |
|---|---|---|
| answered without a person | 26/60 — 43.3% | **31/60 — 51.7%** |
| of the tickets a corpus can reach | 26/52 — 50.0% | **31/52 — 59.6%** |
| handed over for "no grounds" | 26 | 21 |

Five more tickets answered. Eight of the 60 go to a person for money, an
injection attempt or a P1 and no corpus change can move them, which is why
the second row exists.

**The result worth arguing with is the citation mix.** Before, process
documents supplied every citation — the payroll-week runbook, the SLA
excerpt, the triage SOP. After, 36 of 37 citations are Jira keys. The new
source is 38 of 84 chunks in the Indian corpus and it is winning almost
every top-5 slot. That may well be right: a real support ticket is much more
often a product fault than a question about payroll week, and the tracker is
written in the same vocabulary as the tickets. But "the new source answers
everything" is what crowding out looks like too, and the honest statement is
that coverage rose while the basis of the answers shifted wholesale. What
settles it is a faithfulness run on the known-issue slice, which is not done.

### Two leaks it introduced, and a deploy that hid the fix

Indexing internal engineering notes gave the pilot new ways to say something
it should not.

The tracker key: *"This is a duplicate of PNJ-4412"* is true, cites
correctly, passes every other check, and tells a customer how our issue
tracker is numbered. Worse, PNJ-4412's note ends *"Fix in 2.7.3, rollout
pending customer UAT"*, and the first draft from it told the customer exactly
that — a release commitment the support desk never agreed to make. My rule of
only indexing resolved issues was supposed to prevent this and did not: the
issue is closed and its note is still forward-looking.

So C8 rejects a draft carrying an internal tracker id or a promise about an
unshipped fix, and the prompt asks for "a fix is in progress" — no version,
no date. The first C8 banned that sentence too, matching "fix is in" without
requiring a digit, so the check rejected the exact wording the system had
been told to produce. It now requires the specifics that make a promise, and
is tested in both directions.

Deploying the fix then appeared not to work, which turned out to be the
price of the latency win: a warm AgentCore session keeps serving the runtime
version it opened on, so the corrected check was live and not running.
`deploy_runtime.sh` now recycles the proxy's execution environments, which
drops their sessions with them.

## Routing: the trained classifier is worse, and the data says why

G2 sits at 90.0% against a 95% target, and two prompt rewrites made it worse.
The obvious next move is to stop prompting and train on the 1,341 tickets
whose queue a human assigned. Measured on exactly the tickets G2 scores —
same seed, same sampler, asserted identical — a TF-IDF word-and-char model
with a linear SVM gets:

| | accuracy on G2's 399 test tickets |
|---|---|
| majority class | 17.3% |
| intake dropdown, today | 86.0% |
| **Nova Micro prompt (G2)** | **90.0%** |
| trained classifier | 76.4% |

It loses, and not narrowly — it is worse than the dropdown it was meant to
replace.

The first run said 100.0%, which is the useful part. A support export repeats
itself: these 1,341 tickets contain **402 distinct texts**, and an id-based
split leaves 71.7% of the test tickets with their exact wording also in
training. The model was a lookup table reporting that it can remember 402
strings. Grouping by text so a wording lands wholly in train or wholly in
test gives the 76.4% above, and leaves just **234 distinct texts** to learn
nine classes from. That is the answer: there is no labelled data here, there
are 402 labelled sentences wearing 1,341 hats, and the language model's prior
about payroll is worth more than 234 examples.

It also puts a caveat on G2's own number. Its 95% confidence interval
(86.6–92.5%) assumes independent samples, and 399 tickets drawn from 402
distinct texts are not independent. The point estimate is a fair measure of
accuracy over the ticket *stream*, which is what production sees; the
interval is narrower than the evidence supports.

