"""Box 1 — the PII gate.

This runs before every other box. No later box, and no model, ever sees
personal data. The gate returns redacted text plus a token map; the map stays
in the caller's process and is never passed to a model, logged, or written to
the draft. Gate G1: zero identifiers survive.

Design notes
------------
* Order matters. Aadhaar, Emirates ID and long account numbers all match
  digit runs, so the most specific pattern must win. The patterns are applied
  longest-first and each match is replaced by a placeholder that no later
  pattern can match.
* The phone pattern is deliberately greedy; on this corpus it also matches
  12-digit Aadhaar numbers. That is fine for redaction (both are removed) but
  it means per-pattern counts overlap and must not be summed.
* Names are not regex-detectable in general. Customer and end-customer names
  come from the structured fields, so they are redacted by exact match from
  the roster rather than guessed.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Iterable

# Ordered most-specific first. The first pattern to claim a span keeps it.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMIRATES_ID", re.compile(r"\b784[-\s]?\d{4}[-\s]?\d{7}[-\s]?\d\b")),
    ("IBAN",        re.compile(r"\b[A-Z]{2}\d{2}[ ]?(?:[A-Z0-9]{4}[ ]?){2,7}[A-Z0-9]{1,4}\b")),
    ("UAN",         re.compile(r"\bUAN[:\s]*\d{12}\b", re.I)),
    ("AADHAAR",     re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")),
    ("PAN",         re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("IFSC",        re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("ACCOUNT",     re.compile(r"\b\d{9,18}\b")),
    ("EMAIL",       re.compile(r"[\w.\-+]+@[\w\-]+\.[\w.\-]+")),
    # Deliberately greedy, but it must not eat an ISO date: "2026-08-10" is
    # nine digits with two hyphens and matched the old pattern, which turned
    # every timestamp in the export into a false leak. A phone number here
    # never has exactly the 4-2-2 digit shape of a date.
    ("PHONE",       re.compile(r"(?!\d{4}-\d{2}-\d{2}(?!\d))"
                               r"\+?\d[\d\s\-]{8,13}\d")),
]

# Anything shaped like a placeholder we already inserted. Used by the leak
# check so a redacted string is never re-scanned into a false positive.
PLACEHOLDER = re.compile(r"\[REDACTED:[A-Z_]+:\d+\]")


@dataclass
class Redaction:
    """What the gate produced. `tokens` never leaves the process."""
    text: str
    tokens: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.tokens)


def redact(text: str, extra_literals: Iterable[str] = ()) -> Redaction:
    """Replace every identifier with a stable placeholder.

    `extra_literals` carries values we know are personal because of where they
    came from — a customer name, an end-customer name — rather than because of
    their shape.
    """
    if not text:
        return Redaction(text="", tokens={}, counts={})

    tokens: dict[str, str] = {}
    counts: dict[str, int] = {}
    out = text

    # Known literals first: they may contain characters the patterns would split.
    for literal in sorted({s for s in extra_literals if s and len(s) > 3},
                          key=len, reverse=True):
        if literal in out:
            n = counts.get("NAME", 0) + 1
            key = f"[REDACTED:NAME:{n}]"
            tokens[key] = literal
            counts["NAME"] = n
            out = out.replace(literal, key)

    for label, pattern in PATTERNS:
        def _sub(m: re.Match[str]) -> str:
            value = m.group(0)
            n = counts.get(label, 0) + 1
            key = f"[REDACTED:{label}:{n}]"
            tokens[key] = value
            counts[label] = n
            return key
        out = pattern.sub(_sub, out)

    return Redaction(text=out, tokens=tokens, counts=counts)


def leaks(text: str) -> list[tuple[str, str]]:
    """Identifiers still present in `text`. G1 asserts this is empty.

    Placeholders are blanked before scanning so the digits inside
    `[REDACTED:AADHAAR:1]` cannot themselves be read as an identifier.
    """
    scrubbed = PLACEHOLDER.sub(" ", text)
    found: list[tuple[str, str]] = []
    for label, pattern in PATTERNS:
        for m in pattern.finditer(scrubbed):
            found.append((label, m.group(0)))
    return found


# The fields that carry free text, and therefore the only ones that can carry
# personal data. Everything else in a ticket is structured: pseudonymous ids,
# queue names, timestamps. The gate is scoped to these, and so is the leak
# check, because "no model ever sees personal data" is a statement about the
# text handed downstream — not about an epoch-millisecond timestamp that
# happens to be twelve digits long.
TEXT_FIELDS = ("subject", "body", "title", "message", "summary", "details",
               "end_customer", "from_email", "user_ref")


def downstream_text(ticket: dict) -> str:
    """Exactly what the pipeline hands to a model, concatenated. The G1 test
    asserts on this, so the test and the pipeline cannot drift apart."""
    parts = [str(ticket.get(f, "")) for f in TEXT_FIELDS if ticket.get(f)]
    for event in (ticket.get("events") or ticket.get("history")
                  or ticket.get("activity_log") or []):
        meta = event.get("meta") or event.get("data") or event.get("info") or {}
        if meta.get("note"):
            parts.append(str(meta["note"]))
    return "\n".join(parts)


def redact_ticket(raw: dict, known_names: Iterable[str] = ()) -> tuple[dict, Redaction]:
    """Redact every free-text field of a raw ticket, whatever schema it is in.

    Returns a copy of the ticket with the text fields replaced, and the single
    Redaction covering all of them. Structured identifiers (customer_id,
    agent id) are pseudonyms already and are kept: the pipeline needs them to
    look up tier and region, and they carry no personal detail on their own.
    """
    out = dict(raw)
    joined = "\n".join(str(raw.get(f, "")) for f in TEXT_FIELDS if raw.get(f))
    red = redact(joined, extra_literals=known_names)

    # Re-redact field by field so the shape of the ticket is preserved. Using
    # the same function keeps the two paths honest; the joined pass above is
    # only used for the counts.
    for f in TEXT_FIELDS:
        if raw.get(f):
            out[f] = redact(str(raw[f]), extra_literals=known_names).text

    # Agent notes ride along inside the event list and are free text too.
    for key in ("events", "history", "activity_log"):
        if not raw.get(key):
            continue
        events = []
        for event in raw[key]:
            event = dict(event)
            for mk in ("meta", "data", "info"):
                if isinstance(event.get(mk), dict) and event[mk].get("note"):
                    meta = dict(event[mk])
                    meta["note"] = redact(str(meta["note"]),
                                          extra_literals=known_names).text
                    event[mk] = meta
            events.append(event)
        out[key] = events
    return out, red
