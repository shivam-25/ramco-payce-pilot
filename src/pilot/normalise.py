"""Box 2 — normalise.

Three source systems export the same ticket three different ways: the email
queue (665), the in-app widget (483) and the partner portal (219). They
disagree on field names, on timestamp format and on what the priority scale
is called. One check, not one per channel.

No eval gate: normalising does not drive the misrouting problem. It is
plumbing, and the brief says to skip gates that do not trace to the problem.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
GST = timezone(timedelta(hours=4))

# The three channels, keyed by the field that only that schema has.
EMAIL, WIDGET, PARTNER = "email", "widget", "partner"

# Priority words differ per channel. Channels call urgent work P1, urgent, S1.
PRIORITY = {
    "p1": "P1", "p2": "P2", "p3": "P3",
    "s1": "P1", "s2": "P2", "s3": "P3",
    "urgent": "P1", "high": "P2", "normal": "P3", "low": "P3",
}


@dataclass
class Ticket:
    """One ticket, one shape. Text is already redacted when this is built."""
    ticket_id: str
    channel: str
    customer_id: str | None
    subject: str
    body: str
    raw_category: str
    priority: str
    status: str
    received_at: datetime | None
    first_response_at: datetime | None
    resolved_at: datetime | None
    events: list[dict] = field(default_factory=list)
    # filled in by later boxes
    region: str | None = None
    tier: str | None = None
    queue: str | None = None
    case_id: str | None = None


def _ts(value: Any, assume: timezone) -> datetime | None:
    """Parse the four timestamp shapes in this export into one aware UTC value.

    The export's own note says "timestamps as stored by each source system",
    which is the polite way of saying three formats and one epoch in
    milliseconds. A naive value is read in its channel's local zone, because
    that is what the source system meant by it.
    """
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):           # widget: epoch millis
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    s = str(value).strip()

    # The partner portal appends a zone label, and it is not always the same
    # one: 219 partner tickets carry both "GST" and "IST" suffixes. Read the
    # label when it is there rather than assuming the portal's own zone.
    zone = assume
    for label, tz in (("IST", IST), ("GST", GST), ("UTC", timezone.utc)):
        if s.endswith(label):
            zone = tz
            s = s[: -len(label)].strip()
            break

    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=zone)
        return dt.astimezone(timezone.utc)
    return None


def detect_schema(raw: dict) -> str:
    if "ticket_id" in raw:
        return EMAIL
    if "tkt" in raw:
        return WIDGET
    if "reference" in raw:
        return PARTNER
    raise ValueError(f"unknown ticket schema: {sorted(raw)[:6]}")


def normalise(raw: dict) -> Ticket:
    kind = detect_schema(raw)

    if kind == EMAIL:
        return Ticket(
            ticket_id=raw["ticket_id"], channel="email",
            customer_id=raw.get("customer_id"),
            subject=raw.get("subject", ""), body=raw.get("body", ""),
            raw_category=raw.get("category", ""),
            priority=PRIORITY.get(str(raw.get("priority", "")).lower(), "P3"),
            status=str(raw.get("status", "")).lower(),
            received_at=_ts(raw.get("received_at"), IST),
            first_response_at=_ts(raw.get("first_response_at"), IST),
            resolved_at=_ts(raw.get("resolved_at"), IST),
            events=raw.get("events", []),
        )

    if kind == WIDGET:
        return Ticket(
            ticket_id=raw["tkt"], channel="widget",
            customer_id=raw.get("org_id"),
            subject=raw.get("title", ""), body=raw.get("message", ""),
            raw_category=raw.get("cat", ""),
            priority=PRIORITY.get(str(raw.get("prio", "")).lower(), "P3"),
            status=str(raw.get("state", "")).lower(),
            received_at=_ts(raw.get("created"), IST),
            first_response_at=_ts(raw.get("first_reply_ts"), IST),
            resolved_at=_ts(raw.get("closed_ts"), IST),
            events=raw.get("history", []),
        )

    return Ticket(
        ticket_id=raw["reference"], channel="partner",
        customer_id=raw.get("tenant"),
        subject=raw.get("summary", ""), body=raw.get("details", ""),
        raw_category=raw.get("topic", ""),
        priority=PRIORITY.get(str(raw.get("severity", "")).lower(), "P3"),
        status=str(raw.get("status", "")).lower(),
        received_at=_ts(raw.get("opened_on"), GST),
        first_response_at=_ts(raw.get("fr_at"), GST),
        resolved_at=_ts(raw.get("closed_at"), GST),
        events=raw.get("activity_log", []),
    )
