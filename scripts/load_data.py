"""Load the data room into the two regional databases.

Each region's database receives ONLY that region's customers and tickets.
The 67 tickets whose customer_id is absent from customers.csv have no region,
so they go to neither: they cannot be answered from a regional store and the
router sends them to a human. That is a finding, not a dropped row — the
count is printed and carried into the submission.

Ticket text is masked by the PII gate BEFORE it is written. The raw body
never reaches the database.
"""
from __future__ import annotations
import csv, json, sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pilot.config import DATA_ROOM, REGIONS          # noqa: E402
from pilot.db import connect                          # noqa: E402
from pilot.normalise import normalise                 # noqa: E402
from pilot.lookup import customers, customer_names    # noqa: E402
from pilot.pii import redact_ticket, leaks            # noqa: E402
from pilot.taxonomy import (canonical_category, final_queue,   # noqa: E402
                            intake_queue)
from pilot.dedupe import assign_cases                 # noqa: E402

SCHEMA = (ROOT / "infra" / "schema.sql").read_text()


def apply_schema() -> None:
    for region in REGIONS:
        with connect(region, vectors=False) as conn:
            conn.execute(SCHEMA)
        print(f"  schema applied to {region}")


def load_customers() -> None:
    cust = customers()
    for region in REGIONS:
        rows = [c for c in cust.values() if c.region == region]
        with connect(region) as conn:
            conn.execute("TRUNCATE customers CASCADE")
            conn.cursor().executemany(
                "INSERT INTO customers VALUES (%s,%s,%s,%s,%s,%s)",
                [(c.customer_id, c.name, c.tier, c.region,
                  c.monthly_fee_usd, c.employees_paid) for c in rows])
        print(f"  customers {region}: {len(rows)}")


def load_tickets() -> None:
    raw_tickets = json.loads(
        (DATA_ROOM / "data" / "tickets_export.json").read_text())["tickets"]
    names = customer_names()
    cust = customers()

    prepared: dict[str, list] = {r: [] for r in REGIONS}
    orphans = 0
    leak_count = 0
    # 14 ticket_ids appear twice in the export, with identical text but under
    # two different channels — the same incident exported by two source
    # systems under one reference. The export's own note warns that "not all
    # channels included"; this is the other half of that. Keep the first and
    # count the rest: an exact-ID duplicate is a loader concern, while a
    # repeat INCIDENT (same customer, same topic, different reference) is
    # what box 5 is for. They are different problems and are counted apart.
    seen_ids: set[str] = set()
    exact_dupes = 0

    norm_all = [normalise(r) for r in raw_tickets]
    cases = assign_cases(norm_all)

    for raw in raw_tickets:
        plain = normalise(raw)
        c = cust.get(plain.customer_id or "")
        if c is None:
            orphans += 1
            continue

        if plain.ticket_id in seen_ids:
            exact_dupes += 1
            continue
        seen_ids.add(plain.ticket_id)

        masked_raw, red = redact_ticket(raw, names)
        t = normalise(masked_raw)

        # Assert the gate at the point of writing, not only in the test.
        # Nothing unmasked may enter the database, ever.
        residual = leaks(f"{t.subject}\n{t.body}")
        if residual:
            leak_count += 1
            raise SystemExit(f"PII LEAK writing {t.ticket_id}: {residual[:3]}")

        prepared[c.region].append((
            t.ticket_id, t.channel, t.customer_id, c.region, c.tier,
            t.subject, t.body, plain.raw_category,
            canonical_category(plain.raw_category), t.priority, t.status,
            t.received_at, t.first_response_at, t.resolved_at,
            intake_queue(plain.events), final_queue(plain.events),
            cases.get(t.ticket_id), json.dumps(red.counts),
        ))

    for region, rows in prepared.items():
        with connect(region) as conn:
            conn.execute("TRUNCATE tickets CASCADE")
            conn.cursor().executemany(
                "INSERT INTO tickets (ticket_id,channel,customer_id,region,tier,"
                "subject_masked,body_masked,raw_category,canonical_category,"
                "priority,status,received_at,first_response_at,resolved_at,"
                "queue_intake,queue_final,case_id,pii_counts) "
                "VALUES (" + ",".join(["%s"] * 18) + ")", rows)
        print(f"  tickets {region}: {len(rows)}")
    print(f"  tickets with no resolvable customer (sent to a human): {orphans}")
    print(f"  exact cross-channel duplicate ids collapsed at load: {exact_dupes}")


def load_kb() -> None:
    arts = json.loads((DATA_ROOM / "data" / "kb_articles.json").read_text())
    for region in REGIONS:
        rows = [a for a in arts if a.get("region") in ("ALL", region)]
        with connect(region) as conn:
            conn.execute("TRUNCATE kb_articles CASCADE")
            conn.cursor().executemany(
                "INSERT INTO kb_articles VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(a["id"], a["title"], a.get("region"), a.get("topic"),
                  a.get("last_reviewed"), a.get("owner"), a.get("status"),
                  a.get("views_90d"), a.get("helpful_pct"), a.get("excerpt"))
                 for a in rows])
        print(f"  kb_articles {region}: {len(rows)}")


def load_rules() -> None:
    """effective_to is derived: a rule stops applying when the next rule on
    the same topic and region starts. Without this, every query matches every
    version of a rule and the June question gets the August answer."""
    rules = json.loads(
        (DATA_ROOM / "integrations" / "statutory_rules.json").read_text())["rules"]
    rules.sort(key=lambda r: (r["region"], r["topic"], r["effective_from"]))
    for i, r in enumerate(rules):
        nxt = next((n for n in rules[i + 1:]
                    if n["region"] == r["region"] and n["topic"] == r["topic"]), None)
        r["effective_to"] = nxt["effective_from"] if nxt else None

    for region in REGIONS:
        rows = [r for r in rules if r["region"] == region]
        with connect(region) as conn:
            conn.execute("TRUNCATE statutory_rules CASCADE")
            conn.cursor().executemany(
                "INSERT INTO statutory_rules VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(r["rule_id"], r["region"], r["topic"], r["title"],
                  r["effective_from"], r["effective_to"], r.get("old_value"),
                  r.get("new_value"), r.get("source"), r.get("status"))
                 for r in rows])
        print(f"  statutory_rules {region}: {len(rows)}")


if __name__ == "__main__":
    print("applying schema");    apply_schema()
    print("loading customers");  load_customers()
    print("loading tickets");    load_tickets()
    print("loading kb");         load_kb()
    print("loading rules");      load_rules()
    print("done")
