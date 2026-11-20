"""Which language a ticket is written in, and what to do about it.

The corpus is entirely English. 75 of the 1,367 tickets in the export are
not: 38 Arabic, 37 Devanagari. Before this module they failed quietly in the
worst way available -- not with an error, but with a plausible escalation.
An Arabic question embedded against an English corpus retrieves something,
just not the right thing, and the drafter then says, correctly, that the
passages it was given do not answer the question. Every Arabic ticket became
a person's problem and nothing in the system said why.

Two decisions, and they are deliberately split:

  * Detecting the language is a RULE. A Unicode script range is decisive,
    free, and cannot hallucinate. There is no model call here and no
    confidence score to tune.

  * Bridging the gap is JUDGEMENT, and it is done by translating the
    QUESTION into English for retrieval only. The alternative -- translating
    the corpus into three languages and indexing each -- triples the index,
    triples the cost of every change to a document, and introduces a second
    place where the EPF ceiling can be wrong. Translating one short question
    costs a fraction of a cent on the cheap model and leaves exactly one
    copy of the truth.

The customer's own words are never replaced. The translation is used to find
passages; the draft is written against the original text, in the original
language, because a customer who wrote in Arabic is owed a reply in Arabic.
"""
from __future__ import annotations

import re

from .bedrock import converse, Usage
from .config import CLASSIFY_MODEL

# Script ranges, not word lists. A word list would need maintaining and would
# disagree with itself on loan words; a codepoint range does not.
_ARABIC = re.compile(r"[؀-ۿݐ-ݿ]")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

NAMES = {"en": "English", "ar": "Arabic", "hi": "Hindi"}


def detect(text: str) -> str:
    """'en', 'ar' or 'hi'. English is the default because the corpus is.

    A ticket is counted as non-English on the presence of the script rather
    than on a majority of characters: "صرف الرواتب - payroll run RUN-2026-09"
    is an Arabic ticket with an English reference in it, and the reply should
    be in Arabic.
    """
    if _ARABIC.search(text or ""):
        return "ar"
    if _DEVANAGARI.search(text or ""):
        return "hi"
    return "en"


_SYSTEM = """You translate a payroll support question into English so it can
be matched against English documentation.

Rules:
- Output ONLY the translated question. No preamble, no explanation, no quotes.
- Keep identifiers exactly as written: ticket numbers, run ids, employee
  codes, amounts, dates, and product names are not translated.
- Translate what is written. Do not answer the question, do not expand it,
  and do not add words that would change what it is asking for."""


def to_english(text: str, lang: str) -> tuple[str, Usage | None]:
    """The question in English, for retrieval only.

    Returns the original text unchanged when it is already English, so the
    common case pays nothing. On any failure the original is returned rather
    than an exception: a worse retrieval is a worse answer, but a raised
    exception here would turn a ticket in Arabic into a 500, and that is a
    strictly worse way to fail.
    """
    if lang == "en" or not (text or "").strip():
        return text, None
    try:
        out, usage = converse(CLASSIFY_MODEL, text[:2000], system=_SYSTEM,
                              max_tokens=400, temperature=0.0)
        out = (out or "").strip()
        return (out or text), usage
    except Exception:
        return text, None


def reply_instruction(lang: str) -> str:
    """Appended to the drafting prompt. Empty for English so the English path
    is byte-for-byte what it was before this module existed.

    Non-English drafts come back in BOTH languages, and that is an approval
    requirement rather than a courtesy. A person signs off every draft before
    it reaches a customer; if the draft is only in Arabic and the agent on
    shift does not read Arabic, the approval step degrades into pressing a
    button on text nobody checked. The English half is what makes the gate
    real. The customer is sent the half written in their language.
    """
    if lang == "en":
        return ""
    name = NAMES[lang]
    return (f"\n\nThe customer wrote in {name}. Write the reply TWICE, in "
            f"this order and this format exactly:\n"
            f"{name} first, then a line containing only ---, then the same "
            f"reply in English.\n"
            f"Both halves must say the same thing: the English half is what "
            f"the approving agent reads, so it must not be shorter, softer "
            f"or fuller than the {name} half.\n"
            f"The passages are in English; translate what you use from them. "
            f"Keep identifiers, amounts, dates and the [1] style citation "
            f"markers exactly as the passages write them, in both halves. "
            f"If you cannot answer, write the English word ESCALATE on its "
            f"own line and write nothing else.")


def split_bilingual(text: str, lang: str) -> tuple[str, str]:
    """The customer's half and the approver's half of a bilingual draft.

    Returns (customer_text, english_text). For English, both are the same
    string. When the model ignored the format there is nothing to split, so
    the whole draft is returned as the customer half and the English half is
    empty -- the caller can then see that the approval aid is missing rather
    than being handed a silently truncated reply.
    """
    if lang == "en":
        return text, text
    parts = [p.strip() for p in re.split(r"^\s*-{3,}\s*$", text or "",
                                         maxsplit=1, flags=re.MULTILINE)]
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    return (text or ""), ""
