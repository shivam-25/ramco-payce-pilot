"""Box 6a — the injection screen. Gate G4: caught before the model.

Ticket PN-101129 asks the assistant to approve a refund, zero an invoice and
list every customer admin email. It is a live example sitting in the export.

Two defences, and only the second one is load-bearing:

1. This screen, which catches the obvious cases in code before any model
   sees the text.
2. The absence of capability. The drafting model is given no tool. There is
   no function in this pipeline that can approve a refund, change a balance,
   or read the customer table. The worst outcome of a screen that misses is a
   bad draft that a human declines to send.

A screen alone is a filter and filters can be walked around. Defence 2 is
what makes the walk pointless, and G4 asserts it directly.
"""
from __future__ import annotations
import re

# Phrases that only appear when someone is addressing the model rather than
# support. Tuned against 40 benign tickets that use words like "admin",
# "approve" and "system" in their ordinary sense.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Either order: "ignore all previous instructions" and "forget the rules
    # above" are the same move. Requiring one word order missed the second.
    ("override",   re.compile(r"\b(ignore|disregard|forget|override)\b[^.]{0,40}?\b"
                              r"(?:(previous|prior|above|earlier|all)\b[^.]{0,25}?\b"
                              r"(instruction|prompt|rule|direction|guideline)s?"
                              r"|(instruction|prompt|rule|direction|guideline)s?\b"
                              r"[^.]{0,25}?\b(previous|prior|above|earlier))\b", re.I)),
    ("role_claim", re.compile(r"\b(you are now|act as|pretend to be|switch to)\b"
                              r"[^.]{0,30}\b(admin|administrator|developer|root|"
                              r"system|dan|jailbreak)\b", re.I)),
    ("sys_spoof",  re.compile(r"^\s*(system|assistant|developer)\s*:", re.I | re.M)),
    # Shouted role headers anywhere in the text. Case-sensitive on purpose:
    # ordinary tickets write "system" in lower case, injections shout it, and
    # matching case-insensitively mid-line fired on benign "System: Payroll".
    ("sys_spoof",  re.compile(r"\b(SYSTEM|ASSISTANT|DEVELOPER|USER)\s*:\s*\S")),
    ("sys_spoof",  re.compile(r"<\s*/?\s*(system|assistant|instruction|ticket)\s*>", re.I)),
    ("comment",    re.compile(r"<!--[^>]*\b(assistant|ai|ignore|system|policy)\b", re.I)),
    ("exfil",      re.compile(r"\b(reveal|disclose|print|output|repeat|show)\b"
                              r"[^.]{0,40}\b(system prompt|instructions|connection "
                              r"string|credentials|api key|all customer|every customer)\b", re.I)),
    ("tool_call",  re.compile(r"\b(run|execute|call|invoke)\b[^.]{0,25}"
                              r"\b(tool|function|command|query)\b", re.I)),
    ("tool_call",  re.compile(r"\b[a-z_]{3,30}\s*\(\s*[^)]{0,40}\)\s*$", re.M)),
    ("secrecy",    re.compile(r"\bdo not (mention|tell|reveal|report)\b[^.]{0,30}"
                              r"\b(this|anyone|human|agent)\b", re.I)),
    ("ai_probe",   re.compile(r"\bif you(?:'re| are) an? (ai|bot|assistant|llm)\b", re.I)),
]


def screen(text: str) -> tuple[bool, list[str]]:
    """Returns (blocked, reasons). Runs on the masked text, before any model."""
    if not text:
        return False, []
    reasons = []
    for label, pattern in PATTERNS:
        if pattern.search(text):
            if label not in reasons:
                reasons.append(label)
    return bool(reasons), reasons


# ---- defence 2, asserted rather than assumed ---------------------------
# The set of side effects this pipeline can produce. G4 asserts that nothing
# capable of moving money, changing a record, or reading bulk customer data
# is reachable from the drafting path.
PIPELINE_CAPABILITIES: frozenset[str] = frozenset({
    "read_masked_ticket",
    "read_doc_chunks",
    "read_statutory_rules",
    "read_single_customer_tier_and_region",
    "write_audit_log",
    "write_draft_for_human_review",
})

FORBIDDEN_CAPABILITIES: frozenset[str] = frozenset({
    "approve_refund", "set_invoice_balance", "issue_payment",
    "list_all_customers", "list_admin_emails", "send_to_customer",
    "write_payroll", "delete_ticket", "escalate_without_human",
})


def capabilities_are_safe() -> tuple[bool, set[str]]:
    overlap = PIPELINE_CAPABILITIES & FORBIDDEN_CAPABILITIES
    return (not overlap), overlap
