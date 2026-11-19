"""REQ-RAD-008: detect potential conflicts and signal them to Tower continuously.

Radar detects non-emergency conflicts from each aircraft's reported data:

- late by 2 hours or more  -> ``delay_2h``
- late by 4 hours or more  -> ``delay_4h`` (replaces delay_2h: one lateness conflict that escalates)
- fuel below LOW_FUEL_PERCENT -> ``low_fuel``

While an aircraft's landing request is queued at Tower, Radar sends a version-1
``conflict`` message for each open conflict every second until Tower answers
with ``conflict_resolved`` (REQ-TWR-010). Every repeat of one conflict carries
the same ``event_id``, so Tower applies it once. Radar then records the
conflict as resolved and stops sending. If Tower rejects the conflict
(``tower_r2_rejected``), Radar stops sending it too. A conflict is raised again
only after its condition clears and returns, when lateness escalates, or for a
new request.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.db import MessageDatabase
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.tower_inbox import send_to_tower, unprocessed_tower_messages

CONFLICT_TYPE = "conflict"
RESOLVED_TYPE = "conflict_resolved"
REJECTED_TYPE = "tower_r2_rejected"
DELAY_2H = "delay_2h"
DELAY_4H = "delay_4h"
LOW_FUEL = "low_fuel"
LOW_FUEL_PERCENT = 25  # below this, the aircraft is low on fuel (non-emergency)
SEND_INTERVAL = timedelta(seconds=1)
# A little slack so a once-a-second background pass is never skipped for arriving a few ms early.
SEND_TOLERANCE = timedelta(milliseconds=100)
RECENT_MESSAGE_IDS = 120  # sent message IDs kept per conflict, to match Tower's rejections
REPLIES_PROCESSED_KEY = "radar:conflict_replies_processed_through"
CONFLICT_CATEGORY = "conflict"


def detect_conflicts(aircraft: AircraftState) -> list[str]:
    """Conflict reasons for one aircraft's reported delay and fuel (unreported values raise none)."""
    reasons = []
    if aircraft.delay_hours is not None:
        if aircraft.delay_hours >= 4:
            reasons.append(DELAY_4H)
        elif aircraft.delay_hours >= 2:
            reasons.append(DELAY_2H)
    if aircraft.fuel_percent is not None and aircraft.fuel_percent < LOW_FUEL_PERCENT:
        reasons.append(LOW_FUEL)
    return reasons


def conflict_key(aircraft_id: str, reason: str) -> str:
    return f"conflict:{aircraft_id}:{reason}"


def _queued_landings(db: MessageDatabase) -> dict[str, str]:
    """aircraft_id -> request_id for landing requests Tower has queued (REQ-RAD-006).

    Tower only re-orders queued landing requests, so only these get conflicts.
    """
    return {
        row["payload"]["aircraft_id"]: row["payload"]["request_id"]
        for row in db.list_shared_state(category="request")
        if row["payload"].get("status") == "queued" and row["payload"].get("request_type") == "landing"
    }


def _forget(db: MessageDatabase, key: str) -> None:
    with db.connection:
        db.connection.execute("DELETE FROM shared_state WHERE key = ?", (key,))


def send_conflict_signals(db: MessageDatabase, *, now: datetime | None = None) -> list[int]:
    """REQ-RAD-008: send each open conflict to Tower, at most once per second; return the message IDs.

    Conflicts whose condition no longer holds, whose aircraft is no longer
    queued, or that belong to an earlier request are forgotten, so they are
    raised afresh if they return. The "last sent" time is shared, so several
    Radar processes do not multiply the messages.
    """
    now = now or datetime.now(timezone.utc)
    queued = _queued_landings(db)
    open_conflicts = {
        conflict_key(aircraft.id, reason): (aircraft.id, reason, queued[aircraft.id])
        for aircraft in AircraftStore(db).list()
        if aircraft.id in queued
        for reason in detect_conflicts(aircraft)
    }
    records = {row["key"]: row["payload"] for row in db.list_shared_state(category=CONFLICT_CATEGORY)}
    for key, record in records.items():
        if key not in open_conflicts or record["request_id"] != open_conflicts[key][2]:
            _forget(db, key)
            records[key] = None

    sent = []
    for key, (aircraft_id, reason, request_id) in open_conflicts.items():
        record = records.get(key)
        if record is not None:
            if record["status"] != "active":
                continue  # resolved or rejected: stop sending
            if now - datetime.fromisoformat(record["last_sent"]) < SEND_INTERVAL - SEND_TOLERANCE:
                continue
        event_id = record["event_id"] if record else f"conflict-{aircraft_id}-{reason}-{uuid4().hex[:8]}"
        message_id = send_to_tower(db, CONFLICT_TYPE, f"Conflict: {aircraft_id} {reason.replace('_', ' ')}", {
            "event_id": event_id, "request_id": request_id, "aircraft_id": aircraft_id,
            "reason": reason, "emergency": False,
        }, now=now)
        db.upsert_shared_state(key, {
            "aircraft_id": aircraft_id,
            "request_id": request_id,
            "reason": reason,
            "event_id": event_id,
            "status": "active",
            "first_sent": record["first_sent"] if record else now.isoformat(),
            "last_sent": now.isoformat(),
            "messages_sent": (record["messages_sent"] if record else 0) + 1,
            "message_ids": ((record["message_ids"] if record else []) + [message_id])[-RECENT_MESSAGE_IDS:],
        }, category=CONFLICT_CATEGORY)
        sent.append(message_id)
    return sent


def process_conflict_resolutions(db: MessageDatabase, *, now: datetime | None = None) -> list[tuple[str, str]]:
    """REQ-RAD-008: record Tower's replies to conflicts and stop sending those conflicts.

    ``conflict_resolved`` marks the matching conflict (same event ID) resolved;
    ``tower_r2_rejected`` for one of a conflict's messages marks it rejected.
    Returns the (aircraft_id, reason) pairs newly resolved or rejected. Replies
    for conflicts Radar is not signalling are ignored.
    """
    now = now or datetime.now(timezone.utc)
    records = {row["key"]: row["payload"] for row in db.list_shared_state(category=CONFLICT_CATEGORY)}
    changed = []
    for _, message_type, metadata in unprocessed_tower_messages(db, REPLIES_PROCESSED_KEY, (RESOLVED_TYPE, REJECTED_TYPE)):
        if message_type == RESOLVED_TYPE:
            match = next((key for key, record in records.items()
                          if record["status"] == "active" and record["event_id"] == metadata.get("event_id")), None)
            update = {"status": "resolved", "resolved_at": now.isoformat()}
        else:
            source = metadata.get("source_message_id")
            match = next((key for key, record in records.items()
                          if record["status"] == "active" and source in record.get("message_ids", [])), None)
            update = {"status": "rejected", "rejected_at": now.isoformat(), "rejection": metadata.get("reason")}
        if match is None:
            continue
        records[match] = {**records[match], **update}
        db.upsert_shared_state(match, records[match], category=CONFLICT_CATEGORY)
        changed.append((records[match]["aircraft_id"], records[match]["reason"]))
    return changed
