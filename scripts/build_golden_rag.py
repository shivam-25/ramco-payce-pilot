"""The G5 golden set: questions whose answers are verifiable in the corpus.

Written by hand against the source documents, because a RAG golden set
generated from the same retriever it grades is a mirror, not a test. Every
`ground_truth` below was read out of the file named in `source_ids`.

Three kinds of question, on purpose:

  answerable   the corpus contains the answer; the system must find it
  dated        the answer DEPENDS on when it is asked; same question, two
               different correct answers either side of 2026-08-01
  unanswerable the corpus does NOT contain the answer; the right behaviour is
               to say so and hand off. These are the ones that catch a system
               that would rather invent than abstain — which is precisely
               what the last vendor pilot did when it quoted a UAE gratuity
               figure that appears in no article.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "golden" / "g5_rag.jsonl"

Q: list[dict] = [
    # ---------------------------------------------------- SLA contract
    dict(id="SLA-01", region="IN", as_of="2026-09-15", kind="answerable",
         question="What is the first response target for a P1 ticket on the Enterprise tier?",
         ground_truth="One hour. Enterprise first-response targets are P1 1 h, P2 4 h, P3 8 h, measured in elapsed calendar hours from ticket creation.",
         source_ids=["sla_contract_excerpt.md"]),
    dict(id="SLA-02", region="IN", as_of="2026-09-15", kind="answerable",
         question="What resolution target applies to a P2 ticket for a Business tier customer?",
         ground_truth="48 hours. Business resolution targets are P1 12 h, P2 48 h, P3 96 h.",
         source_ids=["sla_contract_excerpt.md"]),
    dict(id="SLA-03", region="UAE", as_of="2026-09-15", kind="answerable",
         question="Does a Standard tier customer get SLA credits?",
         ground_truth="No. Standard tier is best effort for both first response and resolution, and the contract gives it no SLA credits.",
         source_ids=["sla_contract_excerpt.md"]),
    dict(id="SLA-04", region="IN", as_of="2026-09-15", kind="answerable",
         question="How is the SLA credit calculated if an Enterprise P1 ticket is resolved in 20 hours against an 8 hour target?",
         ground_truth="The base credit for Enterprise P1 is 10% of the monthly subscription fee. Because 20 hours is more than twice the 8 hour target, the severity multiplier of 1.5 applies, giving 15%.",
         source_ids=["sla_contract_excerpt.md"]),
    dict(id="SLA-05", region="IN", as_of="2026-09-15", kind="answerable",
         question="Is there a monthly cap on SLA credits?",
         ground_truth="Not under template v3.2, which has no monthly cap. Earlier templates capped credits at 25%.",
         source_ids=["sla_contract_excerpt.md"]),
    dict(id="SLA-06", region="IN", as_of="2026-09-15", kind="answerable",
         question="If a customer reopens a ticket after five days, does the resolution clock restart?",
         ground_truth="No. If a customer reopens within 7 days the clock is neither paused nor restarted; the reopened time counts towards the original resolution time.",
         source_ids=["sla_contract_excerpt.md"]),
    dict(id="SLA-07", region="UAE", as_of="2026-09-15", kind="answerable",
         question="When is time waiting for the customer excluded from the SLA clock?",
         ground_truth="Only where the ticket carries the status 'Pending - customer' for that interval. Planned maintenance and force majeure are also excluded.",
         source_ids=["sla_contract_excerpt.md"]),
    dict(id="SLA-08", region="IN", as_of="2026-09-15", kind="answerable",
         question="What makes a ticket P1 rather than P2?",
         ground_truth="P1 means payroll cannot be run, employees will not be paid on time, or a statutory filing will be missed. P2 is a material error in payroll output, or a core module degraded with a workaround available.",
         source_ids=["sla_contract_excerpt.md"]),

    # ------------------------------------------------ escalation policy
    dict(id="ESC-01", region="IN", as_of="2026-09-15", kind="answerable",
         question="How quickly must a P1 ticket be escalated from L1 to L2 if L1 cannot resolve it from the KB?",
         ground_truth="Within 1 hour for P1, and within 4 hours otherwise. The escalation must include a summary of what was tried.",
         source_ids=["escalation_policy.md"]),
    dict(id="ESC-02", region="UAE", as_of="2026-09-15", kind="answerable",
         question="A customer's payslip was shown to the wrong user. Who must be told and how fast?",
         ground_truth="The DPO and Security must be notified within 1 hour; a privacy concern such as data shown to the wrong user or PII exposure is a security escalation.",
         source_ids=["escalation_policy.md"]),
    dict(id="ESC-03", region="IN", as_of="2026-09-15", kind="answerable",
         question="How often must support update the customer on a P1?",
         ground_truth="At least every 4 hours on P1s, and every 24 hours on P2s.",
         source_ids=["escalation_policy.md"]),
    dict(id="ESC-04", region="IN", as_of="2026-09-15", kind="answerable",
         question="How many customers reporting the same symptom triggers an incident?",
         ground_truth="Five or more customers reporting the same symptom; the Support Lead declares an incident and pages Engineering on-call immediately.",
         source_ids=["escalation_policy.md"]),
    dict(id="ESC-05", region="IN", as_of="2026-09-15", kind="answerable",
         question="Can an L1 agent message an engineer directly on Slack?",
         ground_truth="No. Skipping L2 to ping engineers on Slack directly is explicitly not allowed.",
         source_ids=["escalation_policy.md"]),

    # ------------------------------------------------ payroll week runbook
    dict(id="PWK-01", region="IN", as_of="2026-09-15", kind="answerable",
         question="What dates count as payroll week?",
         ground_truth="The 25th of the month to the 3rd of the next month, when most customers run payroll, file statutory returns and release salaries.",
         source_ids=["payroll_week_runbook.md"]),
    dict(id="PWK-02", region="IN", as_of="2026-09-28", kind="answerable",
         question="During payroll week, what is the first response target for a P1 payroll ticket?",
         ground_truth="30 minutes. First response targets are halved for P1 payroll tickets during payroll week.",
         source_ids=["payroll_week_runbook.md"]),
    dict(id="PWK-03", region="IN", as_of="2026-09-15", kind="answerable",
         question="How must the Payroll-Ops queue be staffed during payroll week?",
         ground_truth="At least 40% of L1 agents plus 3 L2 specialists at all times.",
         source_ids=["payroll_week_runbook.md"]),
    dict(id="PWK-04", region="UAE", as_of="2026-09-15", kind="answerable",
         question="When should the Content Team update KB articles for a statutory change?",
         ground_truth="Before the change goes live. At T-10 days Compliance sends a statutory change notice, and the Content Team updates the affected KB articles and their last reviewed date ahead of the effective date.",
         source_ids=["payroll_week_runbook.md"]),
    dict(id="PWK-05", region="IN", as_of="2026-09-28", kind="answerable",
         question="Can an agent re-categorise a ticket during payroll week?",
         ground_truth="Not without Lead approval. During payroll week agents must not re-categorise tickets without the Lead's approval.",
         source_ids=["payroll_week_runbook.md"]),

    # ------------------------------------------------------ triage SOP
    dict(id="SOP-01", region="IN", as_of="2026-09-15", kind="answerable",
         question="Which queue handles a How-To ticket?",
         ground_truth="General-L1. The triage SOP routes the How-To category to the General-L1 queue.",
         source_ids=["ticket_triage_sop.md"]),
    dict(id="SOP-02", region="UAE", as_of="2026-09-15", kind="answerable",
         question="Which queue owns statutory and compliance tickets?",
         ground_truth="Statutory-Compliance.",
         source_ids=["ticket_triage_sop.md"]),
    dict(id="SOP-03", region="IN", as_of="2026-09-15", kind="answerable",
         question="How often must KB articles be reviewed?",
         ground_truth="At least every 90 days by their owner. Articles not reviewed within 90 days are flagged in the monthly KB report.",
         source_ids=["ticket_triage_sop.md"]),
    dict(id="SOP-04", region="IN", as_of="2026-09-15", kind="answerable",
         question="How many times may a ticket be reassigned?",
         ground_truth="At most once. A second move requires the Support Lead to approve and re-categorise it.",
         source_ids=["ticket_triage_sop.md"]),
    dict(id="SOP-05", region="IN", as_of="2026-09-15", kind="answerable",
         question="Can an agent answer from memory instead of the knowledge base?",
         ground_truth="No. The Confluence KB is the single source of truth and agents must not answer from memory. Replies should cite the relevant KB article ID.",
         source_ids=["ticket_triage_sop.md"]),

    # ---------------------------------------- statutory, DATE DEPENDENT
    # The same question, asked about two different months, has two different
    # correct answers. This is the case the last vendor pilot got wrong.
    dict(id="PF-JUNE", region="IN", as_of="2026-06-15", kind="dated",
         question="What is the EPF statutory wage ceiling I should use for this month's payroll?",
         ground_truth="INR 15,000 per month. The ceiling rose to INR 25,000 only from 1 August 2026, so a June 2026 payroll uses INR 15,000.",
         source_ids=["R-IN-PF-2026-08"],
         must_contain=["15,000"], must_not_contain=["25,000"]),
    dict(id="PF-SEPT", region="IN", as_of="2026-09-15", kind="dated",
         question="What is the EPF statutory wage ceiling I should use for this month's payroll?",
         ground_truth="INR 25,000 per month, in force from 1 August 2026, raised from the previous INR 15,000.",
         source_ids=["R-IN-PF-2026-08"],
         must_contain=["25,000"]),
    dict(id="PF-JULY31", region="IN", as_of="2026-07-31", kind="dated",
         question="We are running payroll dated 31 July 2026. Which EPF wage ceiling applies?",
         ground_truth="INR 15,000. The increase to INR 25,000 takes effect on 1 August 2026, the day after.",
         source_ids=["R-IN-PF-2026-08"],
         must_contain=["15,000"], must_not_contain=["25,000"]),
    dict(id="WPS-JUNE", region="UAE", as_of="2026-06-20", kind="dated",
         question="Which WPS SIF file version should we send to the bank?",
         ground_truth="SIF v2, with 14 columns. SIF v3 becomes mandatory only from 15 July 2026.",
         source_ids=["R-UAE-WPS-2026-07"],
         must_contain=["v2"]),
    dict(id="WPS-SEPT", region="UAE", as_of="2026-09-20", kind="dated",
         question="Which WPS SIF file version should we send to the bank?",
         ground_truth="SIF v3, which has 16 columns including a new mandatory establishment-reference column and a revised EDR record layout. It has been in force since 15 July 2026.",
         source_ids=["R-UAE-WPS-2026-07"],
         must_contain=["v3"]),
    dict(id="GRAT-01", region="UAE", as_of="2026-09-15", kind="answerable",
         question="Did the end-of-service gratuity calculation change in 2026?",
         ground_truth="No. The January 2026 MoHRE guidance was a clarification only, covering part-time and fixed-term calculation; the entitlement is unchanged at 21 days basic pay per year for the first five years.",
         source_ids=["R-UAE-GRAT-2026-01"]),
    dict(id="TDS-01", region="IN", as_of="2026-09-15", kind="answerable",
         question="Which TDS slabs apply for FY2026-27 under the new regime?",
         ground_truth="The FY26-27 slabs, effective from 1 April 2026, replacing the FY25-26 slabs.",
         source_ids=["R-IN-TDS-2026-04"]),

    # -------------------------------------------- MUST ABSTAIN / HAND OFF
    dict(id="ABS-01", region="UAE", as_of="2026-09-15", kind="unanswerable",
         question="Exactly how many days of basic pay is gratuity for an employee with 9 years of service?",
         ground_truth="This system does not hold that figure. The statutory record confirms 21 days per year for the first five years but says nothing about years beyond five, so this must go to a human rather than be estimated.",
         source_ids=[], must_abstain=True),
    dict(id="ABS-02", region="IN", as_of="2026-09-15", kind="unanswerable",
         question="What are the exact steps in KB-0105 for adding a taxable salary component?",
         ground_truth="The knowledge base article KB-0105 exists but its body is not available in this system; only Confluence holds it. The steps cannot be quoted from here.",
         source_ids=["KB-0105"], must_abstain=True),
    dict(id="ABS-03", region="IN", as_of="2026-09-15", kind="unanswerable",
         question="What is the ESI contribution rate for this customer?",
         ground_truth="No ESI rule is held in the statutory store, so this system cannot state the rate. It must hand off to a human.",
         source_ids=[], must_abstain=True),
    dict(id="ABS-04", region="UAE", as_of="2026-09-15", kind="unanswerable",
         question="Why was customer C-1003's salary payment of AED 1,000 not credited last Tuesday?",
         ground_truth="This is a money question about a specific payment. The system has read-only access and no view of the payment itself; it must summarise and hand off to a human for approval.",
         source_ids=[], must_abstain=True),
    dict(id="ABS-05", region="IN", as_of="2026-09-15", kind="unanswerable",
         question="What will the PF ceiling be in 2027?",
         ground_truth="No rule with a 2027 effective date exists in the statutory store, so the system cannot say. It must decline rather than extrapolate.",
         source_ids=[], must_abstain=True),
]

if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as fh:
        for q in Q:
            fh.write(json.dumps(q, ensure_ascii=False, sort_keys=True) + "\n")
    kinds = {}
    for q in Q:
        kinds[q["kind"]] = kinds.get(q["kind"], 0) + 1
    print(f"wrote {len(Q)} golden RAG questions -> {OUT}")
    for k, v in sorted(kinds.items()):
        print(f"  {k:14} {v}")
