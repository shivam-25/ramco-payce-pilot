"""Gate G1 — the PII gate. Pass mark: 0 leaks.

785 tickets: 725 carry at least one identifier (104 of them an identity or
bank number), and 60 clean tickets are included as negatives. A gate tested
only on positives cannot tell a careful redactor from one that blanks the
whole body, so both directions are asserted.
"""
from __future__ import annotations
import json, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import load, record                       # noqa: E402

from pilot.config import DATA_ROOM                       # noqa: E402
from pilot.lookup import customer_names                  # noqa: E402
from pilot.pii import redact_ticket, leaks, downstream_text   # noqa: E402
from pilot.normalise import normalise                    # noqa: E402

RAW = {}


def _raw():
    if not RAW:
        for r in json.loads(
                (DATA_ROOM / "data" / "tickets_export.json").read_text())["tickets"]:
            RAW[normalise(r).ticket_id] = r
    return RAW


def run() -> dict:
    rows = load("g1_pii.jsonl")
    raw = _raw()
    names = customer_names()

    leaked, checked, identity_checked = [], 0, 0
    destroyed = []          # negatives whose text came back empty
    kinds = Counter()

    for row in rows:
        src = raw.get(row["ticket_id"])
        if src is None:
            continue
        masked, red = redact_ticket(src, names)
        # The gate promises that no model ever sees personal data, so the
        # assertion is on exactly what the pipeline hands to a model.
        # Structured timestamps and pseudonymous ids are not free text and
        # are not in scope; scanning them made every ISO date look like a
        # phone number and every epoch-millisecond stamp like an account.
        text = downstream_text(masked)
        checked += 1
        if row.get("has_identity_doc"):
            identity_checked += 1

        found = leaks(text)
        if found:
            leaked.append((row["ticket_id"], found[:3]))
        for k, v in red.counts.items():
            kinds[k] += v

        if row["expect"] == "unchanged_text_preserved":
            m = normalise(masked)
            if not (m.subject or m.body).strip():
                destroyed.append(row["ticket_id"])

    return {"checked": checked, "identity": identity_checked,
            "leaked": leaked, "destroyed": destroyed, "kinds": kinds}


def test_g1_zero_leaks():
    m = run()
    print(f"\n  tickets checked        {m['checked']}")
    print(f"  carrying an ID/bank no {m['identity']}")
    print(f"  identifiers removed    {sum(m['kinds'].values())}")
    for k, v in m["kinds"].most_common():
        print(f"    {k:14} {v}")
    print(f"  leaks                  {len(m['leaked'])}")
    print(f"  clean tickets destroyed {len(m['destroyed'])}")

    record("G1 PII gate", "0 leaks", f"{len(m['leaked'])} leaks",
           not m["leaked"] and not m["destroyed"],
           tickets_checked=m["checked"],
           identity_bearing=m["identity"],
           identifiers_removed=sum(m["kinds"].values()))

    assert not m["leaked"], f"PII survived in {len(m['leaked'])}: {m['leaked'][:3]}"
    assert not m["destroyed"], \
        f"{len(m['destroyed'])} clean tickets were emptied by the redactor"


if __name__ == "__main__":
    test_g1_zero_leaks()
