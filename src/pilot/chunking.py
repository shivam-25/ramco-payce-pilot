"""Four chunking strategies, built so the bake-off can compare them on the
same queries. The winner is chosen on measured context precision and recall,
not on which one sounds most modern.

The corpus is small and oddly shaped, which is the whole reason this is worth
measuring rather than assuming:

  * 4 process documents, ~1,220 words total, heavily headed markdown
  * 4 statutory rules, each a structured record with an effective date
  * 24 KB articles that are METADATA ONLY — `excerpt` reads "See full article
    in Confluence space SUP-KB." There are no bodies. Chunking cannot invent
    them, and an answer step that pretends otherwise is how the last vendor
    pilot produced a confident gratuity figure that was in no article.

At this size the usual advice ("512 tokens with overlap") may well be worse
than not splitting at all. That is strategy A's hypothesis and it gets tested
rather than dismissed.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import date

from .config import DATA_ROOM

PROCESS_DOCS = ("escalation_policy.md", "payroll_week_runbook.md",
                "sla_contract_excerpt.md", "ticket_triage_sop.md")

# A KB row in this export is an index entry: title, topic, owner, review
# date — and no body, because the body lives in Confluence. The marker is a
# named constant so the output check can recognise a passage that cannot
# support an answer, rather than matching on prose that might be reworded.
STUB_MARKER = "[KB index entry — body lives in Confluence]"

STRATEGIES = ("A_whole_doc", "B_fixed_512", "C_heading", "D_heading_context")

# Sentinel start for a "period before the first recorded change". The corpus
# has no earlier rule to bound it, and leaving it NULL would make the row
# match every date including ones after the change.
from datetime import date as _date_cls
_PAST = _date_cls(2000, 1, 1)


@dataclass
class Chunk:
    strategy: str
    source_type: str          # process | statutory | kb | known_issue
    source_id: str
    heading: str | None
    text: str
    region: str = "ALL"
    effective_from: date | None = None
    effective_to: date | None = None

    @property
    def tokens(self) -> int:
        # ~4 characters a token. Good enough to size a prompt; the real cost
        # comes from the usage field on the actual call.
        return max(1, len(self.text) // 4)


def _split_markdown(text: str) -> list[tuple[str, str]]:
    """Split on ## / ### headings, keeping each heading with its body."""
    parts: list[tuple[str, str]] = []
    current_head, buf = None, []
    for line in text.splitlines():
        if re.match(r"^#{1,4}\s+", line):
            if buf and "".join(buf).strip():
                parts.append((current_head, "\n".join(buf).strip()))
            current_head = line.lstrip("#").strip()
            buf = []
        else:
            buf.append(line)
    if buf and "".join(buf).strip():
        parts.append((current_head, "\n".join(buf).strip()))
    return parts


def _fixed(text: str, size: int = 512, overlap: int = 64) -> list[str]:
    """Fixed windows measured in approximate tokens, overlapping so a fact
    split across a boundary still appears whole in one window."""
    words = text.split()
    per = int(size * 0.75)                 # ~0.75 words a token
    step = per - int(overlap * 0.75)
    out = []
    for i in range(0, max(1, len(words)), max(1, step)):
        window = words[i:i + per]
        if window:
            out.append(" ".join(window))
        if i + per >= len(words):
            break
    return out


def build(strategy: str, rules: list[dict], kb: list[dict],
          issues: list[dict] | None = None) -> list[Chunk]:
    if strategy not in STRATEGIES:
        raise ValueError(strategy)
    chunks: list[Chunk] = []

    # ---- process documents --------------------------------------------
    for name in PROCESS_DOCS:
        path = DATA_ROOM / "process" / name
        raw = path.read_text()
        doc_title = raw.splitlines()[0].lstrip("# ").strip()

        if strategy == "A_whole_doc":
            chunks.append(Chunk(strategy, "process", name, doc_title, raw.strip()))

        elif strategy == "B_fixed_512":
            for i, piece in enumerate(_fixed(raw)):
                chunks.append(Chunk(strategy, "process", f"{name}#{i}", None, piece))

        else:
            for head, body in _split_markdown(raw):
                if strategy == "D_heading_context":
                    # One line of provenance in front of every chunk. The
                    # failure it fixes is a retrieved section that is correct
                    # but unattributable — the reader cannot tell whether
                    # "within 4 hours" came from the SLA contract or the
                    # escalation policy, and those disagree.
                    text = (f"[From {doc_title} — section: {head or 'opening'}]\n"
                            f"{body}")
                else:
                    text = f"{head}\n{body}" if head else body
                chunks.append(Chunk(strategy, "process",
                                    f"{name}#{head or 'opening'}", head, text))

    # ---- statutory rules ----------------------------------------------
    # Never split: a rule is one fact, and its effective dates are columns
    # that travel with it. Splitting one would separate a rate from its date,
    # which is the exact error the last pilot shipped.
    #
    # One row PER VALUE PERIOD, not per rule record. The source file stores a
    # change as a single object carrying both the new value and the value it
    # replaced, so a rule that takes effect on 1 August is simply absent from
    # a June query once the date filter is on — and a June payslip question
    # then has nothing to answer from at all. The bake-off found this: date
    # correctness sat at 0.60 and the three failures were every question
    # asked BEFORE its change. An effective-dated store has to hold the old
    # period as its own row, with its own window, so "what applied in June"
    # is a lookup rather than an inference.
    # The superseded value is deliberately NOT carried here, for the same
    # reason it is not carried on the prior-period row below. A chunk holding
    # both "in force: 25,000" and "this replaced: 15,000" is a trap: ask with
    # a June-flavoured subject and the model reads the superseded figure off
    # a passage that is genuinely in force, and cites it correctly. Every
    # gate passes -- the date filter, the citation check, the grounding
    # check -- and the reply is still wrong, which is the exact incident this
    # pilot exists to prevent. The old value is not lost; it is the prior
    # period's own row, with its own window.
    for r in rules:
        current = (f"Statutory rule {r['rule_id']} ({r['region']}, topic "
                   f"{r['topic']}): {r['title']}. "
                   f"The value in force is: {r.get('new_value')}. "
                   f"Effective from {r['effective_from']}"
                   + (f" until {r['effective_to']}." if r.get("effective_to")
                      else " (currently in force).")
                   + f" Source: {r.get('source')}.")
        if strategy == "D_heading_context":
            current = (f"[Statutory rule for {r['region']}, in force from "
                       f"{r['effective_from']}]\n{current}")
        chunks.append(Chunk(strategy, "statutory", r["rule_id"], r["topic"],
                            current, region=r["region"],
                            effective_from=_d(r["effective_from"]),
                            effective_to=_d(r.get("effective_to"))))

        # The period BEFORE this change, if the record names what it replaced
        # and the replacement was a real change rather than a clarification.
        old_value = (r.get("old_value") or "").strip()
        if old_value and "unchanged" not in old_value.lower():
            # The replacement value is deliberately NOT repeated here. The
            # first version said "it was replaced by X with effect from
            # <date>", and the model drafting a June answer read X off this
            # very chunk and quoted it — the exact error the whole
            # effective-dating exercise exists to prevent. A chunk that
            # describes one period states one value.
            prior = (f"Statutory rule {r['rule_id']}-PRIOR ({r['region']}, "
                     f"topic {r['topic']}): the position in force up to "
                     f"{r['effective_from']}. "
                     f"For any date before {r['effective_from']}, the correct "
                     f"and only applicable value is: {old_value}. "
                     f"This value was superseded on {r['effective_from']}; "
                     f"the replacement does not apply to earlier dates and is "
                     f"recorded separately. Source: {r.get('source')}.")
            if strategy == "D_heading_context":
                prior = (f"[Statutory rule for {r['region']}, the period "
                         f"ending {r['effective_from']}]\n{prior}")
            chunks.append(Chunk(strategy, "statutory", r["rule_id"], r["topic"],
                                prior, region=r["region"],
                                effective_from=_PAST,
                                effective_to=_d(r["effective_from"])))

    # ---- KB articles ---------------------------------------------------
    # Metadata only. Indexed so the system can say an article exists, who owns
    # it and how stale it is — never so it can pretend to quote the body.
    for a in kb:
        stale = ""
        if a.get("last_reviewed"):
            stale = f" Last reviewed {a['last_reviewed']}."
        text = (f"Knowledge base article {a['id']}: {a['title']}. "
                f"Topic {a.get('topic')}, region {a.get('region')}, "
                f"status {a.get('status')}.{stale} "
                f"Body not available in this system: {a.get('excerpt')}")
        if strategy == "D_heading_context":
            text = f"{STUB_MARKER}\n{text}"
        chunks.append(Chunk(strategy, "kb", a["id"], a.get("topic"), text,
                            region=a.get("region", "ALL")))

    # ---- known issues, from resolved engineering escalations ------------
    # The 24 KB articles are index entries with no body, so the pilot can say
    # an article exists and nothing more. The escalation tracker is the one
    # place in this export that holds real written answers: 42 resolved
    # issues with a human-written root cause. Indexing them is what lets the
    # pilot answer "is this a known problem" instead of handing it to a
    # person who will look up the same ticket.
    #
    # Three rules, all of which exist because this source is internal and the
    # others are not:
    #
    # 1. Customer names are redacted. Three summaries name the customer who
    #    hit the bug ("Kestrel: overtime computed twice"). Serving that to a
    #    different customer would disclose that a named company had a payroll
    #    defect. The redaction uses the same identity list as the PII gate,
    #    so a name added there is covered here without a second edit.
    # 2. Customer ids and linked ticket ids are not indexed at all. With the
    #    names gone, what remains is a product defect description, which is
    #    the same for everyone and belongs to no region -- hence region ALL.
    # 3. Only resolved issues. An open issue's note is a plan, not an answer,
    #    and "fix in 2.7.3, rollout pending" becomes a promise the moment it
    #    reaches a customer. The drafting prompt already forbids promising a
    #    date, but the cheaper control is not to put one in the corpus.
    #
    # effective_from is the day the issue was raised, so a question dated
    # before it does not retrieve a defect that had not happened yet.
    chunks.extend(known_issue_chunks(strategy, issues))

    return chunks


def _d(value):
    from datetime import date as _date
    if not value:
        return None
    if isinstance(value, _date):
        return value
    y, m, d = str(value).split("-")
    return _date(int(y), int(m), int(d))


def known_issue_chunks(strategy: str, issues: list[dict] | None) -> list[Chunk]:
    """Known-issue chunks on their own, with no disk access.

    Separate from `build` because `build` reads the process documents from
    the data room, and the deployed runtime does not have one -- the
    packaging step leaves case data out of the artifact on purpose. The
    ingest path runs inside the runtime, so it needs a builder that touches
    nothing but its argument.
    """
    out: list[Chunk] = []
    for j in issues or []:
        note = (j.get("resolution_note") or "").strip()
        text = (f"Known issue {j['key']} ({j.get('component')}, "
                f"{j.get('issue_type')}, priority {j.get('priority')}, "
                f"status {j.get('status')}): {j.get('summary')}. "
                f"Resolution: {note}")
        if strategy == "D_heading_context":
            text = (f"[Resolved engineering issue {j['key']} — "
                    f"component {j.get('component')}]\n{text}")
        out.append(Chunk(strategy, "known_issue", j["key"],
                            j.get("component"), text, region="ALL",
                            effective_from=_d(str(j.get("created", ""))[:10])))

    return out
