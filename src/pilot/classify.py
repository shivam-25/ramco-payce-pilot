"""Box 4 — classify. Gate G2: >= 95%.

Today the intake dropdown routes correctly 84.8% of the time (1,137 of 1,341
tickets with a recorded outcome). 204 tickets had to be moved by a human.
That 15.2% is the problem this box exists to fix.

The model reads the MASKED text only. It never sees the dropdown category,
the queue, or the event history — those are the answer. A small, cheap model
is enough: picking one of nine labels from two sentences is an easy task, and
the money is better spent on the drafting model.
"""
from __future__ import annotations
import re

from .bedrock import converse, Usage
from .config import CLASSIFY_MODEL
from .taxonomy import CATEGORIES, QUEUES, queue_for

SYSTEM = """You route payroll-software support tickets to one of nine queues.

Apply these two steps IN ORDER. Step 1 overrides step 2.

STEP 1 - Is the customer asking how to USE a working product?
Signals: "how do I", "how to", "where can I find", "is there a way",
"can someone confirm", "what is the process", asking for a template, a
setting, a checklist or steps. Nothing is broken; they do not know where to
click.
  -> category is How-To. Stop. Do not read the subject matter.
  "How to download all payslips together" is How-To, NOT Reports.
  "How do I restrict who sees salary data" is How-To, NOT Access / Login.
  "How do I add employees in bulk" is How-To, NOT Onboarding.

STEP 2 - Something is wrong, or something must be set up. Route to the team
that owns THE SYSTEM WHERE THE WRONG DATA ORIGINATES, not the system where it
was noticed.

- Payroll: a pay run failed, was late, or produced a wrong amount; arrears,
  net pay, payslip values wrong. A blank payslip with net pay 0 is Payroll:
  the pay data is wrong, not the report.
- Statutory: a statutory RULE or RATE itself - PF, ESI, TDS, WPS file format,
  gratuity, end-of-service, a filing or a circular. Choose this only when the
  question is about the rule, not about a run that used it.
- How-To: see step 1.
- Reports: generating, exporting or downloading a report, where the report
  tool itself misbehaves and the underlying data is fine.
- Leave & Attendance: leave balance, accrual, attendance, regularisation,
  shifts. Attendance that does not reach payroll is Leave & Attendance: the
  attendance side is the source.
- Integration: API, webhook, SFTP, bank file, a nightly sync, or any
  third-party system. A sync job misbehaving is Integration even when the
  data it moves is attendance or payroll.
- Billing: invoices, charges, refunds, subscription, payment terms.
- Access / Login: sign-in, password, MFA, roles and permissions that do not
  work. If they are asking how to configure roles, that is How-To.
- Onboarding: a NEW customer, entity or go-live - initial setup, data
  migration from a previous vendor, bulk first import, go-live checklists.
  Anything before go-live is Onboarding even when the topic is statutory.

Text may contain [REDACTED:...] placeholders; treat them as removed personal
data and ignore them. Tickets may be in English, Arabic or Hindi; route them
the same way.

Reply with exactly one category name from this list and nothing else:
Payroll | Statutory | How-To | Reports | Leave & Attendance | Integration |
Billing | Access / Login | Onboarding"""


_NORM = {re.sub(r"[^a-z]", "", c.lower()): c for c in CATEGORIES}


def _parse(reply: str) -> str | None:
    key = re.sub(r"[^a-z]", "", reply.strip().lower())
    if key in _NORM:
        return _NORM[key]
    for norm, cat in _NORM.items():          # model added a sentence around it
        if norm in key:
            return cat
    return None


def classify(text: str) -> tuple[str | None, str | None, Usage]:
    """Returns (category, queue, usage). A None category means the model gave
    an answer outside the nine, and the router sends it to a human rather
    than guessing."""
    reply, usage = converse(CLASSIFY_MODEL, text[:4000], system=SYSTEM,
                            max_tokens=12, temperature=0.0)
    cat = _parse(reply)
    return cat, queue_for(cat), usage
