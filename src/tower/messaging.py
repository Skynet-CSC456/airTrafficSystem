"""Tower messaging through the shared ATC message database.

REQ-TWR-006: Tower assigns a runway to an airborne aircraft and sends the
assignment to Radar and Command as a ``function`` message.
"""

from __future__ import annotations

from dataclasses import asdict

from src.db import Message, MessageDatabase, Team
from src.radar.aircraft import AircraftState, AircraftStore
from src.tower.clearance import assign_runway, update_runway_availability

FUNCTION_MESSAGE_TYPE = "function"
ASSIGN_RUNWAY_FUNCTION = "assign_runway"
DEFAULT_RUNWAYS = (
    {"id": "RWY 1", "available": True, "mode": "landing"},
    {"id": "RWY 2", "available": True, "mode": "landing"},
)


def send_message(db: MessageDatabase, recipient: Team | str, content: str) -> int:
    """REQ-TWR-001: Send a message from Tower to another subsystem."""
    return db.send_message(Team.TOWER, recipient, content)


def receive_messages(
    db: MessageDatabase,
    *,
    unread_only: bool = False,
    limit: int = 100,
    mark_as_read: bool = False,
) -> list[Message]:
    """REQ-TWR-002: Receive messages addressed to Tower."""
    return db.receive_messages(
        Team.TOWER,
        unread_only=unread_only,
        limit=limit,
        mark_as_read=mark_as_read,
    )


def list_airborne_aircraft(db: MessageDatabase) -> list[AircraftState]:
    """REQ-TWR-006: aircraft currently in the air (altitude above ground)."""
    return [state for state in AircraftStore(db).list() if state.altitude > 0]


def load_runways(db: MessageDatabase) -> list[dict]:
    """REQ-TWR-006: runways from shared state, seeding the defaults on first use."""
    runways = [row["payload"] for row in db.list_shared_state(category="runway")]
    if not runways:
        runways = [dict(runway) for runway in DEFAULT_RUNWAYS]
        for runway in runways:
            db.upsert_shared_state(f"runway:{runway['id']}", runway, category="runway")
    return update_runway_availability(runways)


def assign_runway_to_aircraft(
    db: MessageDatabase, aircraft_id: str, runway_id: str
) -> dict:
    """REQ-TWR-006: assign ``runway_id`` to an airborne aircraft.

    The runway must be available and safe for the aircraft. On success the
    decision and runway are written to shared state and a ``function`` message
    is sent to both Radar and Command. Nothing is written when the assignment
    is rejected.
    """
    aircraft = AircraftStore(db).get(aircraft_id)
    if aircraft is None:
        raise ValueError(f"Unknown aircraft {aircraft_id!r}")
    if aircraft.altitude <= 0:
        raise ValueError(f"Aircraft {aircraft_id!r} is not in the air")

    runways = load_runways(db)
    decision = assign_runway(runways, asdict(aircraft), runway_id=runway_id)
    decision["queue_position"] = None

    runway = next(r for r in runways if str(r.get("id")) == str(decision["runway_id"]))
    db.upsert_shared_state(f"runway:{runway['id']}", runway, category="runway")
    db.upsert_shared_state(f"decision:{aircraft_id}", decision, category="decision")

    content = f"Assign {decision['runway_id']} to {aircraft_id}"
    metadata = {
        "function": ASSIGN_RUNWAY_FUNCTION,
        "aircraft_id": aircraft_id,
        "runway_id": decision["runway_id"],
        "occupied_until": decision["occupied_until"],
    }
    decision["message_ids"] = {
        recipient.value: db.send_message(
            Team.TOWER,
            recipient,
            content,
            message_type=FUNCTION_MESSAGE_TYPE,
            metadata=metadata,
        )
        for recipient in (Team.RADAR, Team.COMMAND)
    }
    return decision
