"""Radar runway requests and Tower's answers (REQ-RAD-006).

Radar asks Tower for a runway with Tower's version-1 ``runway_request``
message (REQ-TWR-009) and records each answer: a ``runway_response`` (cleared,
queued or rejected), a ``runway_completed`` event, or the legacy Tower menu's
``assign_runway`` function message.

Radar keeps one request record per aircraft in shared state
(``request:<aircraft_id>``), which the Command dashboard and Radar's conflict
signals (REQ-RAD-008) read.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from src.db import MessageDatabase
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.tower_inbox import send_to_tower, unprocessed_tower_messages, utc_timestamp

RUNWAY_REQUEST_TYPE = "runway_request"
RUNWAY_RESPONSE_TYPE = "runway_response"
RUNWAY_COMPLETED_TYPE = "runway_completed"
FUNCTION_MESSAGE_TYPE = "function"  # the legacy Tower menu's assignment (REQ-TWR-006)
ASSIGN_RUNWAY_FUNCTION = "assign_runway"
VALID_REQUEST_TYPES = ("landing", "departure")
INBOUND_STATUSES = ("Landing", "Airborne")  # in the air, so a runway request means landing
DECISIONS_PROCESSED_KEY = "radar:tower_decisions_processed_through"
REQUEST_CATEGORY = "request"


def configured_airport(db: MessageDatabase) -> str:
    """The airport Tower has configured (REQ-TWR-007); there must be exactly one."""
    airports = sorted(row["payload"]["airport_id"] for row in db.list_shared_state(category="airport")
                      if isinstance(row["payload"], dict) and row["payload"].get("airport_id"))
    if not airports:
        raise ValueError("Tower has not configured an airport yet")
    if len(airports) > 1:
        raise ValueError(f"Several airports are configured ({', '.join(airports)}): choose one")
    return airports[0]


def get_request(db: MessageDatabase, aircraft_id: str) -> dict | None:
    """Radar's latest runway request record for an aircraft, or None."""
    record = db.get_shared_state(f"request:{aircraft_id}")
    return record["payload"] if record else None


def save_request(db: MessageDatabase, aircraft_id: str, payload: dict) -> None:
    db.upsert_shared_state(f"request:{aircraft_id}", payload, category=REQUEST_CATEGORY)


def request_runway(db: MessageDatabase, aircraft_id: str, request_type: str | None = None,
                   *, airport_id: str | None = None, now: datetime | None = None) -> int:
    """REQ-RAD-006: ask Tower for a runway for a known aircraft and return the message ID.

    ``request_type`` is "landing" or "departure"; when omitted it follows the
    aircraft's status (Landing or Airborne -> landing, otherwise departure).
    ``airport_id`` defaults to the one airport Tower has configured. The request
    is sent as Tower's version-1 ``runway_request`` with a new request ID and
    recorded as pending. Nothing is sent or stored when the request is invalid.
    """
    if not isinstance(aircraft_id, str) or not aircraft_id.strip():
        raise ValueError("Aircraft ID cannot be empty")
    aircraft_id = aircraft_id.strip()
    aircraft = AircraftStore(db).get(aircraft_id)
    if aircraft is None:
        raise ValueError(f"Unknown aircraft {aircraft_id!r}")
    if request_type is None:
        request_type = "landing" if aircraft.status in INBOUND_STATUSES else "departure"
    request_type = str(request_type).strip().lower()
    if request_type not in VALID_REQUEST_TYPES:
        raise ValueError("Runway request must be landing or departure")
    airport_id = airport_id or configured_airport(db)

    request_id = f"radar-{aircraft_id}-{request_type}-{uuid4().hex[:8]}"
    message_id = send_to_tower(db, RUNWAY_REQUEST_TYPE, f"Aircraft {aircraft_id} requests a runway for {request_type}", {
        "request_id": request_id, "aircraft_id": aircraft_id, "airport_id": airport_id, "operation": request_type,
    }, now=now)
    save_request(db, aircraft_id, {
        "aircraft_id": aircraft_id, "request_id": request_id, "airport_id": airport_id,
        "request_type": request_type, "status": "requested", "message_id": message_id,
        "requested_at": utc_timestamp(now),
    })
    return message_id


def _decision_from(message_type: str, metadata: dict) -> dict | None:
    """Tower's decision fields from a runway_response, runway_completed or legacy assign_runway message."""
    if message_type == FUNCTION_MESSAGE_TYPE and metadata.get("function") != ASSIGN_RUNWAY_FUNCTION:
        return None
    outcome = "completed" if message_type == RUNWAY_COMPLETED_TYPE else metadata.get("outcome", "cleared")
    fields = (metadata.get("aircraft_id"), metadata.get("request_id"), metadata.get("operation"))
    if outcome not in ("cleared", "queued", "rejected", "completed") or not all(isinstance(value, str) and value for value in fields):
        return None
    if outcome == "cleared" and not isinstance(metadata.get("runway_id"), str):
        return None
    return {**metadata, "outcome": outcome}


def apply_tower_decision(db: MessageDatabase, decision: dict, *, now: datetime | None = None) -> AircraftState | None:
    """REQ-RAD-006: record one Tower decision for a known aircraft; return the aircraft, or None if skipped.

    Decisions for Radar's current request are always applied. A clearance for
    another request (the Tower menu assigns runways without a Radar request) is
    applied too; other answers to requests Radar no longer tracks are ignored.
    A completed departure means the aircraft has left the airspace, so Radar
    stops tracking it (REQ-RAD-007).
    """
    store = AircraftStore(db)
    aircraft_id, request_id, outcome = decision["aircraft_id"], decision["request_id"], decision["outcome"]
    aircraft = store.get(aircraft_id)
    if aircraft is None:
        return None
    request = get_request(db, aircraft_id)
    current = request is not None and request.get("request_id") == request_id
    if not current and outcome != "cleared":
        return None
    base = request if current else {
        "aircraft_id": aircraft_id, "request_id": request_id, "airport_id": decision.get("airport_id"),
        "request_type": decision["operation"],
    }
    record = {**base, "status": outcome, "queue_position": decision.get("queue_position"),
              "reason": decision.get("reason")}
    if outcome == "cleared":
        record.update(runway_id=decision["runway_id"], cleared_at=utc_timestamp(now), finished=False)
        aircraft = store.assign_runway(aircraft_id, decision["runway_id"])
    elif outcome == "rejected" and aircraft.runway_id is not None and base.get("runway_id") == aircraft.runway_id:
        aircraft = store.assign_runway(aircraft_id, None)  # a cancelled clearance frees the aircraft
    elif outcome == "completed":
        if aircraft.runway_id is not None:
            aircraft = store.assign_runway(aircraft_id, None)
        if decision["operation"] == "departure":
            store.delete(aircraft_id)  # REQ-RAD-007: departed, out of Radar's airspace
    save_request(db, aircraft_id, record)
    return aircraft


def process_tower_decisions(db: MessageDatabase, *, now: datetime | None = None) -> list[tuple[str, str]]:
    """REQ-RAD-006: apply every Tower decision Radar has not applied yet, oldest first.

    Read/unread status in Radar's inbox is left alone; progress is tracked in
    shared state. Malformed decisions and decisions for unknown aircraft are
    skipped and not retried. Returns the (aircraft_id, outcome) pairs applied.
    """
    applied = []
    message_types = (RUNWAY_RESPONSE_TYPE, RUNWAY_COMPLETED_TYPE, FUNCTION_MESSAGE_TYPE)
    for _, message_type, metadata in unprocessed_tower_messages(db, DECISIONS_PROCESSED_KEY, message_types):
        decision = _decision_from(message_type, metadata)
        if decision is None:
            continue
        try:
            if apply_tower_decision(db, decision, now=now) is not None:
                applied.append((decision["aircraft_id"], decision["outcome"]))
        except (ValueError, KeyError):
            continue  # nothing Radar can apply; do not retry it forever
    return applied
