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
  C7 pointer-only  no reply whose only content is "the answer is in KB-xxxx"
  C8 internal      no internal tracker id, and no promise about an unshipped fix
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
# C8. Indexing the resolved escalations gave the pilot real answers and, with
# them, two new ways to say something to a customer that it should not.
#
# The first is the tracker key. "This is a duplicate of PNJ-4412" is true,
# cites correctly and passes every other check -- and PNJ-4412 means nothing
# to a customer while telling them how our issue tracker is numbered. The key
# belongs in the citation, which is what the agent sees, not in the prose.
#
# The second is worse. PNJ-4412's note ends "Fix in 2.7.3, rollout pending
# customer UAT", and the first draft written from it told the customer
# exactly that. The issue is closed, so the rule of only indexing resolved
# issues did not catch it: a resolution note can still be forward-looking.
# A support reply that names an unreleased version, or says a fix is coming,
# is a commitment the support desk has not agreed to make -- which is the
# same class of mistake as promising a resolution time, and C3 already exists
# because of that one.
TRACKER_ID = re.compile(r"\b(?:PNJ|JIRA|ENG)-\d{2,}\b")
# "A fix is in progress" is the phrasing the drafting prompt asks for, and it
# promises nothing -- no version, no date. The first version of this pattern
# banned it anyway, because "fix is in" matched, and the check then rejected
# the exact sentence the system had been told to write. What makes a promise
# is the specifics, so the version-and-date branch requires a digit.
# C9 -- an engineer's verdict, repeated to the customer who raised the ticket.
# These are all true statements about our tracker and all useless, or worse,
# in a reply: "could not reproduce" tells a customer we did not find their
# bug, "support content is out of date" tells them our documentation is
# wrong. The index step withholds such notes, and this is the backstop for
# when the model paraphrases one out of a passage that was kept.
INTERNAL_VOICE = re.compile(
    r"(?:could\s+not|cannot|can'?t|unable\s+to|couldn'?t|were\s+not\s+able\s+to)"
    r"\s+(?:be\s+)?reprod"
    r"|not\s+reproducible"
    r"|work(?:s|ing|ed)?\s+as\s+(?:designed|intended)"
    r"|by\s+design\b"
    r"|won'?t\s+fix"
    r"|support\s+(?:content|documentation|article)s?\s+(?:is\s+|are\s+|may\s+be\s+)?"
    r"(?:out\s+of\s+date|stale|outdated)"
    r"|config(?:uration)?\s+issue\s+on\s+(?:the\s+)?tenant"
    r"|duplicate\s+of\s+(?:this\s+)?(?:issue|ticket|report)"
    # An unevidenced promise of progress. The prompt no longer asks for this
    # phrasing -- it used to, which is how it reached customers -- and a
    # tracker entry is not a commitment that anyone is working on it today.
    r"|(?:a\s+|the\s+)?fix\s+(?:is|will\s+be)\s+(?:currently\s+)?"
    r"(?:in\s+progress|under\s*way|on\s+the\s+way|coming)"
    r"|(?:is|are)\s+(?:currently\s+)?being\s+(?:worked\s+on|addressed|investigated)"
    r"|(?:we|the\s+team|engineering)\s+(?:are|is)\s+(?:currently\s+)?"
    r"(?:working\s+on|investigating|looking\s+into)\s+(?:a\s+fix|this|it)"
    r"|under\s+investigation\b"
    r"|(?:attempts?|efforts?)\s+to\s+reproduce\b[^.]{0,40}?(?:have|has|were|was)\s+(?:been\s+)?(?:unsuccessful|without\s+success)",
    re.I)


UNSHIPPED = re.compile(
    r"\b(?:"
    r"fix(?:ed)?\s+(?:is\s+|will\s+be\s+)?(?:in|coming\s+in|planned\s+for|"
    r"scheduled\s+for|expected\s+(?:in|by))\s+(?:version\s+|release\s+|v)?\d"
    r"|version\s+v?\d[\d.]*\s+(?:is\s+)?(?:pending|due|scheduled|expected)"
    r"|rollout\s+(?:is\s+)?pending"
    r"|pending\s+(?:customer\s+)?UAT"
    r"|will\s+be\s+(?:fixed|released|deployed|available)"
    r"|in\s+(?:a\s+)?(?:future|upcoming|next)\s+release"
    r")", re.I)

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
# C10 -- a claim about what the product cannot do.
#
# The drafting prompt already forbids this in as many words, and names the
# exact sentence as an example of a guess: "The system does not support
# exporting these together". The model wrote that sentence anyway, cited a
# P1 incident about payslip PDFs rendering net pay as zero, and passed every
# check -- because saying a feature does not exist uses no money, no
# duration, no tracker id and no internal voice.
#
# A rule the model is asked to follow is a hope. The same rule in code is a
# gate. Whether a feature exists is a claim about the product like any
# other, and this corpus holds rules, runbooks and contracts, not a feature
# list, so the passages almost never support one. When the draft denies a
# capability and no passage denies it too, the ticket goes to a person who
# can actually check the product.
_CAPABILITY_DENIAL = re.compile(
    r"\b(?:does\s+not|doesn'?t|do\s+not|don'?t)\s+(?:currently\s+)?"
    r"(?:support|allow|permit|offer|provide|have)\b"
    r"|\bis\s+not\s+(?:supported|available|possible|offered|provided)\b"
    r"|\bare\s+not\s+(?:supported|available|possible)\b"
    r"|\b(?:cannot|can'?t|can\s+not)\s+be\s+\w+ed\b"
    r"|\bthere\s+is\s+no\s+(?:option|way|feature|setting|support)\b"
    r"|\bnot\s+possible\s+to\b"
    r"|\bno\s+such\s+(?:option|feature|setting)\b",
    re.I)

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

    # C8 — internal identifiers and unshipped-fix promises.
    ids = TRACKER_ID.findall(text)
    if ids:
        fails.append("C8_internal_id")
        detail["internal_id"] = sorted(set(ids))[:5]
    promise = UNSHIPPED.findall(text)
    if promise:
        fails.append("C8_unshipped_fix")
        detail["unshipped_fix"] = promise[:3]

    # C9 -- internal register. A draft that reads like a tracker comment is
    # not a reply, however well grounded it is.
    voice = INTERNAL_VOICE.findall(text)
    if voice:
        fails.append("C9_internal_voice")
        detail["internal_voice"] = voice[:3]

    # C10 — a denial of a product capability needs a passage that denies it.
    # Checked against the passages before the citation check so that the
    # reason a person gets the ticket is the interesting one.
    passages_so_far = "\n".join(h.text for h in draft.passages)
    denial = _CAPABILITY_DENIAL.findall(text)
    if denial and not _CAPABILITY_DENIAL.search(passages_so_far):
        fails.append("C10_capability_claim")
        detail["C10_capability_claim"] = (
            "the reply states what the product cannot do, and no retrieved "
            "passage says so; this corpus holds rules, runbooks and "
            "contracts, not a feature list")

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
