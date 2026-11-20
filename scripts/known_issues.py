"""Turn the escalation tracker into corpus rows, with names removed.

Kept separate from chunking.py because this is the only source that needs
redacting before it is indexed, and that step should be visible rather than
buried in a loop. Prints what it removed.
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("PILOT_RATE_LIMIT", "1")

from pilot.config import DATA_ROOM          # noqa: E402

RESOLVED = {"Done", "Closed"}


def load(redact=True) -> list[dict]:
    raw = json.loads((DATA_ROOM / "data" / "jira_escalations.json").read_text())
    out, hidden = [], 0
    names = ()
    if redact:
        from pilot.lookup import customer_names
        names = customer_names()

    for j in raw:
        if j.get("status") not in RESOLVED:
            continue
        if not (j.get("resolution_note") or "").strip():
            continue
        rec = {k: j.get(k) for k in
               ("key", "summary", "resolution_note", "component",
                "issue_type", "priority", "status", "created")}
        if redact:
            for field in ("summary", "resolution_note"):
                before = rec[field] or ""
                after = _strip_names(before, names)
                if after != before:
                    hidden += 1
                rec[field] = after
        out.append(rec)
    return out, hidden


def _strip_names(text: str, names) -> str:
    """Replace a customer's name, and its first word, with a placeholder.

    Matching the first word too is deliberate: the tracker writes "Kestrel:"
    where the roster says "Kestrel Textiles Pvt Ltd", so a full-name match
    alone would miss every occurrence that matters.
    """
    import re
    out = text
    for n in sorted(names, key=len, reverse=True):
        for form in (n, n.split()[0]):
            if len(form) < 4:
                continue
            out = re.sub(rf"\b{re.escape(form)}\b", "[customer]", out,
                         flags=re.I)
    return out


if __name__ == "__main__":
    rows, hidden = load()
    print(f"  resolved issues with a written root cause: {len(rows)}")
    print(f"  fields where a customer name was removed:  {hidden}")
    for r in rows[:4]:
        print(f"    {r['key']}  {r['summary'][:72]}")
    Path(sys.argv[1] if len(sys.argv) > 1 else "/dev/stdout").write_text(
        json.dumps(rows, indent=1))
