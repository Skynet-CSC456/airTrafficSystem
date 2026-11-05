"""Radar messaging with Tower through the shared ATC message database.

REQ-RAD-006: Radar asks Tower for a runway to land or take off, and records the
runway when Tower assigns one.
"""

from __future__ import annotations

from src.db import Message, MessageDatabase, Team
from src.radar.aircraft import AircraftState, AircraftStore

RUNWAY_REQUEST_TYPE = "runway_request"
VALID_REQUEST_TYPES = ("landing", "departure")
# Tower's runway assignment (REQ-TWR-006): a "function" message naming "assign_runway".
FUNCTION_MESSAGE_TYPE = "function"
ASSIGN_RUNWAY_FUNCTION = "assign_runway"
# Shared-state record of the last Tower assignment message Radar has applied.
PROCESSED_KEY = "radar:runway_assignments_processed_through"
MAX_MESSAGES = 10_000


def request_runway(db: MessageDatabase, aircraft_id: str, request_type: str | None = None) -> int:
    """REQ-RAD-006: ask Tower for a runway for a known aircraft and return the message ID.

    ``request_type`` is "landing" or "departure"; when omitted it follows the
    aircraft's status (Landing -> landing, otherwise departure). The request is
    sent to Tower as a ``runway_request`` message and published to shared state
    so the Command dashboard lists it as pending. Nothing is sent or stored when
    the request is invalid.
    """
    if not isinstance(aircraft_id, str) or not aircraft_id.strip():
        raise ValueError("Aircraft ID cannot be empty")
    aircraft_id = aircraft_id.strip()
    aircraft = AircraftStore(db).get(aircraft_id)
    if aircraft is None:
        raise ValueError(f"Unknown aircraft {aircraft_id!r}")

    if request_type is None:
        request_type = "landing" if aircraft.status == "Landing" else "departure"
    request_type = str(request_type).strip().lower()
    if request_type not in VALID_REQUEST_TYPES:
        raise ValueError("Runway request must be landing or departure")

    message_id = db.send_message(
        Team.RADAR,
        Team.TOWER,
        f"Aircraft {aircraft_id} requests a runway for {request_type}",
        message_type=RUNWAY_REQUEST_TYPE,
        metadata={"aircraft_id": aircraft_id, "request_type": request_type},
    )
    db.upsert_shared_state(
        f"request:{aircraft_id}",
        {
            "aircraft_id": aircraft_id,
            "status": "requested",
            "request_type": request_type,
            "message_id": message_id,
        },
        category="request",
    )
    return message_id


def is_runway_assignment(message: Message) -> bool:
    """True for Tower's assign-runway function messages addressed to Radar."""
    return (
        message.sender == Team.TOWER
        and message.recipient == Team.RADAR
        and message.message_type == FUNCTION_MESSAGE_TYPE
        and bool(message.metadata)
        and message.metadata.get("function") == ASSIGN_RUNWAY_FUNCTION
    )


def apply_runway_assignment(db: MessageDatabase, message: Message) -> AircraftState:
    """REQ-RAD-006: record the runway from one Tower assignment message on the aircraft.

    Raises ValueError for a message that is not a runway assignment or lacks the
    aircraft or runway, and KeyError for an aircraft Radar does not know.
    """
    if not is_runway_assignment(message):
        raise ValueError("Message is not a Tower runway assignment for Radar")
    aircraft_id = message.metadata.get("aircraft_id")
    runway_id = message.metadata.get("runway_id")
    if not aircraft_id:
        raise ValueError("Runway assignment is missing the aircraft ID")
    if not runway_id:
        raise ValueError("Runway assignment is missing the runway ID")

    state = AircraftStore(db).assign_runway(aircraft_id, runway_id)
    request = db.get_shared_state(f"request:{aircraft_id}")
    if request is not None:  # the pending request has now been answered
        db.upsert_shared_state(
            f"request:{aircraft_id}",
            {**request["payload"], "status": "answered", "runway_id": runway_id,
             "message": f"{aircraft_id} assigned {runway_id}"},
            category="request",
        )
    return state


def process_runway_assignments(db: MessageDatabase) -> list[AircraftState]:
    """REQ-RAD-006: apply every Tower runway assignment Radar has not applied yet.

    Messages are applied oldest first, so the latest assignment wins. Read/unread
    status in Radar's inbox is left alone; progress is tracked in shared state.
    Assignments for unknown aircraft or with missing fields are skipped.
    Returns the aircraft whose runway was updated.
    """
    record = db.get_shared_state(PROCESSED_KEY)
    processed_through = record["payload"]["message_id"] if record else 0
    updated = []
    for message in db.receive_messages(Team.RADAR, limit=MAX_MESSAGES):
        if message.id <= processed_through or not is_runway_assignment(message):
            continue
        try:
            updated.append(apply_runway_assignment(db, message))
        except (ValueError, KeyError):
            pass  # nothing Radar can apply; do not retry it forever
        processed_through = message.id
        db.upsert_shared_state(PROCESSED_KEY, {"message_id": processed_through}, category="radar")
    return updated
