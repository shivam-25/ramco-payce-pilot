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
