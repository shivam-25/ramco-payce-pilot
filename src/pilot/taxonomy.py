"""The nine categories and their queues, from process/ticket_triage_sop.md §1.

The SOP says the category "cannot be left blank" and that it "drives automatic
routing". Both are true. What the SOP does not say is that three source systems
spell the nine categories 35 different ways, and that 409 tickets had to be
reassigned to a different queue afterwards. The first is a normalisation
problem. The second is the problem this pilot exists to fix.
"""
from __future__ import annotations
import re

# category -> queue, exactly as the SOP table states it
QUEUES: dict[str, str] = {
    "Payroll":             "Payroll-Ops",
    "Statutory":           "Statutory-Compliance",
    "How-To":              "General-L1",
    "Reports":             "Reports-Analytics",
    "Leave & Attendance":  "Leave-Attendance",
    "Integration":         "Integrations",
    "Billing":             "Billing",
    "Access / Login":      "Access-Support",
    "Onboarding":          "Onboarding",
}
CATEGORIES = tuple(QUEUES)
QUEUE_NAMES = tuple(dict.fromkeys(QUEUES.values()))

# Every spelling seen in the export, folded to its category. Built by reading
# the 35 distinct raw labels, not guessed.
_ALIASES: dict[str, str] = {
    "payroll": "Payroll", "payrollissue": "Payroll", "payrollrun": "Payroll",
    "statutory": "Statutory", "statutorycompliance": "Statutory",
    "compliancepfwps": "Statutory",
    "howto": "How-To",
    "reports": "Reports", "reportexport": "Reports",
    "leave": "Leave & Attendance", "leaveattendance": "Leave & Attendance",
    "integration": "Integration", "integrations": "Integration",
    "apiintegration": "Integration", "integrationbug": "Integration",
    "billing": "Billing", "billinginvoices": "Billing",
    "access": "Access / Login", "accesslogin": "Access / Login",
    "loginissue": "Access / Login",
    "onboarding": "Onboarding", "newcustomersetup": "Onboarding",
}


def canonical_category(raw: str | None) -> str | None:
    """Fold any of the 35 spellings onto one of the nine categories."""
    if not raw:
        return None
    key = re.sub(r"[^a-z]", "", str(raw).lower())
    return _ALIASES.get(key)


def queue_for(category: str | None) -> str | None:
    return QUEUES.get(category) if category else None


def final_queue(events: list[dict]) -> str | None:
    """Where the ticket actually ended up.

    This is the ground truth for routing. The intake dropdown says where a
    customer or an agent *thought* it belonged; the last assignment says where
    a human finally put it. 409 tickets disagree, and those disagreements are
    the misrouting this pilot is measured on.
    """
    queue = None
    for e in events:
        data = e.get("meta") or e.get("data") or e.get("info") or {}
        action = str(e.get("event") or e.get("action") or e.get("evt") or "").lower()
        if action == "assigned" and data.get("queue"):
            queue = data["queue"]
        elif action == "reassigned" and data.get("to"):
            queue = data["to"]
    return queue


def intake_queue(events: list[dict]) -> str | None:
    """Where the intake dropdown sent it. The baseline to beat."""
    for e in events:
        data = e.get("meta") or e.get("data") or e.get("info") or {}
        action = str(e.get("event") or e.get("action") or e.get("evt") or "").lower()
        if action == "assigned" and data.get("queue"):
            return data["queue"]
    return None
