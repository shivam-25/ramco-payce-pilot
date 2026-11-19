"""Box 8 — the output check. Gate G6: no false, biased or harmful draft.

This runs in code AFTER the model, on the draft the model actually produced.
It is not an instruction in the prompt, because a prompt is followed most of
the time and the fraction that is not is the incident.

Seven checks, each tracing to something that actually went wrong in the
vendor pilot, is named in the brief, or was found by running this pipeline
against the real export:

  C1 citation      every factual claim cites a passage
  C2 money         no monetary amount that is not in a cited passage
  C3 timing        no response/resolution promise the SLA does not support
  C4 leak          no personal identifier in the draft
  C5 injection     no sign the draft is obeying the ticket instead of the KB
  C6 fabrication   no claim to quote a KB article body (we do not have them)
"""
from __future__ import annotations
import re
import re
from dataclasses import dataclass, field

from .pii import leaks
from .lookup import sla

# Numbers with a currency marker, in any of the three the corpus uses.
MONEY = re.compile(r"(?:INR|Rs\.?|₹|AED|USD|\$|SAR|dirham)\s?[\d,]+(?:\.\d+)?"
                   r"|[\d,]+(?:\.\d+)?\s?(?:INR|AED|USD|dirhams?|rupees?)", re.I)
DURATION = re.compile(r"\b(\d+)\s*(minute|minutes|min|hour|hours|hrs?|"
                      r"day|days|week|weeks|business day|business days)\b", re.I)
QUOTES_KB = re.compile(r"\b(?:the|that)\s+(?:KB\s*)?article\s+(?:KB-\d+\s+)?"
                       r"(?:says|states|explains|lists|describes)\b", re.I)
OBEYS = re.compile(r"\b(refund (?:has been |is )?approved|balance (?:has been )?"
                   r"set to (?:zero|0)|marked as paid|admin mode|"
                   r"here (?:is|are) the (?:list of )?(?:admin|customer) emails)\b", re.I)


@dataclass
class CheckResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


def _numbers_in(text: str) -> set[str]:
    return {re.sub(r"[,\s]", "", m) for m in re.findall(r"[\d][\d,\.]*", text)}


# Phrases that mean "the answer is somewhere else". They are honest — the
# model is refusing to invent a number it was never given — but they are not
# an answer, and a customer who gets one has been charged a round trip for
# nothing.
_POINTER_ONLY = re.compile(
    r"\b(refer to|see|consult|available in|documented in|found in|"
    r"please check|more details in)\b[^.]{0,60}"
    r"\b(confluence|kb[- ]?\d+|article|knowledge base|full (article|document))\b",
    re.I)


def _all_passages_are_stubs(draft) -> bool:
    from .chunking import STUB_MARKER
    passages = getattr(draft, "passages", None) or []
    if not passages:
        return False
    return all(STUB_MARKER in (p.text or "") for p in passages)


def check(draft, *, tier: str | None = None, priority: str = "P3",
          payroll_week: bool = False) -> CheckResult:
    fails: list[str] = []
    detail: dict = {}
    text = draft.text or ""

    # ---- C7 -------------------------------------------------------------
    # Every passage behind this answer is a metadata stub, so there was never
    # any content to answer from. The model behaved well: it declined to
    # invent an ESI rate and pointed at the article instead. But "the rate is
    # in KB-0140, please read it" satisfies the citation check while telling
    # the customer nothing, and it is strictly worse than handing the ticket
    # to someone who can open Confluence. So it goes to a person.
    if not draft.escalate and _all_passages_are_stubs(draft):
        fails.append("C7_pointer_only")
        detail["C7_pointer_only"] = (
            "every retrieved passage is a KB index entry with no body; "
            "a pointer is not an answer")
    elif not draft.escalate and _POINTER_ONLY.search(text) and len(text) < 260:
        fails.append("C7_pointer_only")
        detail["C7_pointer_only"] = "the reply only says where the answer lives"

    if draft.escalate:
        # An escalation is a valid, safe output. Only the leak check applies.
        residual = leaks(text)
        if residual:
            fails.append("C4_leak")
            detail["leak"] = [l[0] for l in residual]
        return CheckResult(not fails, fails, detail)

    # C1 — a draft with no citation is ungrounded by definition.
    if not draft.citations:
        fails.append("C1_citation")

    passage_text = "\n".join(h.text for h in draft.passages)

    # C2 — every monetary amount must appear in a retrieved passage.
    stray_money = [m for m in MONEY.findall(text)
                   if not _numbers_in(m) <= _numbers_in(passage_text)]
    if stray_money:
        fails.append("C2_money")
        detail["money"] = stray_money[:5]

    # C3 — a promised duration must be one the contract actually gives this
    # tier, or must appear verbatim in a passage. A Standard customer has no
    # target at all, so any promise to them is unsupported.
    promised = DURATION.findall(text)
    if promised:
        allowed = set()
        s = sla(tier, priority)
        if s:
            allowed |= {str(s[0]), str(s[1])}
        if payroll_week and priority == "P1":
            allowed.add("30")
        passage_durations = {n for n, _ in DURATION.findall(passage_text)}
        bad = [f"{n} {u}" for n, u in promised
               if n not in allowed and n not in passage_durations]
        if bad:
            fails.append("C3_timing")
            detail["timing"] = bad[:5]
            detail["tier"] = tier

    # C4 — nothing personal may reach a draft.
    residual = leaks(text)
    if residual:
        fails.append("C4_leak")
        detail["leak"] = [l[0] for l in residual]

    # C5 — the draft must not be acting on the ticket's instructions.
    if OBEYS.search(text):
        fails.append("C5_injection")
        detail["obeys"] = OBEYS.search(text).group(0)

    # C6 — KB bodies are not in this system, so nothing may be quoted from one.
    if QUOTES_KB.search(text):
        fails.append("C6_fabrication")
        detail["quote"] = QUOTES_KB.search(text).group(0)

    return CheckResult(not fails, fails, detail)
