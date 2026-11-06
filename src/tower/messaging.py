"""Tower messaging through the shared ATC message database.

REQ-TWR-006: Tower assigns a runway to an airborne aircraft and sends the
assignment to Radar and Command as a ``function`` message.
"""

from __future__ import annotations

from src.db import Message, MessageDatabase, Team
from src.radar.aircraft import AircraftState, AircraftStore
from src.tower.airport import AirportDatabase, current_timestamp
from src.tower.service import TowerService
from datetime import datetime, timezone
from uuid import uuid4

FUNCTION_MESSAGE_TYPE = "function"
ASSIGN_RUNWAY_FUNCTION = "assign_runway"
DEFAULT_RUNWAYS = (
    {"id": "RWY 1", "available": True, "mode": "mixed"},
    {"id": "RWY 2", "available": True, "mode": "mixed"},
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
    """REQ-TWR-006: read airborne aircraft without initializing or committing state."""
    # AircraftStore's constructor owns a raw connection context that can commit
    # the caller's transaction. Read existing rows and reuse Radar's state model.
    if db.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='aircraft'"
    ).fetchone() is None:
        return []
    rows = db.connection.execute("SELECT * FROM aircraft WHERE altitude > 0 ORDER BY id")
    return [AircraftState(**dict(row)) for row in rows]


def _legacy_airport(airports):
    """Single-airport adapter for the historical menu; no parallel runway store."""
    items = airports.list_airports()
    if not items:
        airports.create_airport("LEGACY", 22000, 40.6413, -73.7781, [r["id"] for r in DEFAULT_RUNWAYS])
        for runway in DEFAULT_RUNWAYS:
            airports.update_runway("LEGACY", runway["id"], available=True, mode=runway["mode"])
        return "LEGACY"
    if len(items) != 1:
        raise ValueError("Multiple airports configured: use TowerService with an explicit airport_id")
    return items[0]["airport_id"]


def load_runways(db: MessageDatabase) -> list[dict]:
    """REQ-TWR-006: compatibility view of the sole authoritative airport."""
    airports = AirportDatabase(db)
    with db.transaction():
        return airports.list_runways(_legacy_airport(airports))


def assign_runway_to_aircraft(db: MessageDatabase, aircraft_id: str, runway_id: str) -> dict:
    """REQ-TWR-006 legacy menu: atomic manual landing assignment/function messages.

    Busy or invalid selection raises ValueError without decision/message writes.
    Unlike the old timer, reservations now require explicit completion.
    """
    service = TowerService(db)
    store = AircraftStore(db)
    with db.transaction():
        aircraft = store.get(aircraft_id)
        if aircraft is None:
            raise ValueError(f"Unknown aircraft {aircraft_id!r}")
        if aircraft.altitude <= 0:
            raise ValueError(f"Aircraft {aircraft_id!r} is not in the air")
        airport_id = _legacy_airport(service.airports)
        if runway_id not in {r["id"] for r in service.airports.list_runways(airport_id)}:
            raise ValueError(f"Runway {runway_id!r} not found")
        result = service._submit(str(uuid4()), aircraft_id, airport_id, "landing",
                                 runway_id=runway_id, legacy=True, reject_if_busy=True)
        if result["outcome"] != "cleared":
            raise ValueError(result["reason"])
        return result


def process_requests(db: MessageDatabase, *, limit: int = 100) -> list[dict]:
    """REQ-TWR-009: process only Radar runway_request messages, one atomic unit each.

    Text/other senders are untouched. Malformed envelopes receive a correlated
    rejection; database failures propagate and leave processing unacknowledged for retry.
    Replay of an identical request ID returns the current decision without resending.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    service = TowerService(db)
    results = []
    for _ in range(limit):
        with db.transaction():
            candidate = db.connection.execute("""SELECT id FROM messages
                WHERE sender_team='radar' AND recipient_team='tower' AND message_type='runway_request'
                AND NOT EXISTS (SELECT 1 FROM tower_processed_messages WHERE message_id=messages.id)
                ORDER BY id LIMIT 1""").fetchone()
            if candidate is None:
                break
            message = db.receive_messages(Team.TOWER, message_id=candidate[0], limit=1)[0]
            metadata = message.metadata
            try:
                if not isinstance(metadata, dict) or type(metadata.get("schema_version")) is not int or metadata["schema_version"] != 1:
                    raise ValueError("Expected schema_version 1 metadata object")
                moment = metadata.get("timestamp")
                if not isinstance(moment, str):
                    raise ValueError("Request timestamp must be timezone-aware UTC")
                moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
                if moment.tzinfo is None or moment.utcoffset() != timezone.utc.utcoffset(moment):
                    raise ValueError("Request timestamp must be timezone-aware UTC")
                result = service.request_runway(metadata.get("request_id"),metadata.get("aircraft_id"),
                    metadata.get("airport_id"),metadata.get("operation"),runway_id=metadata.get("runway_id"))
            except (ValueError, TypeError) as exc:
                # Invalid envelope cannot be a durable request, but its message ID
                # still provides deterministic correlation and once-only handling.
                fields = metadata if isinstance(metadata, dict) else {}
                result = {"schema_version":1,"request_id":fields.get("request_id"),
                          "aircraft_id":fields.get("aircraft_id"),"airport_id":fields.get("airport_id"),
                          "operation":fields.get("operation"),"outcome":"rejected","status":"rejected",
                          "runway_id":None,"queue_position":None,"reason":str(exc),
                          "timestamp":current_timestamp(),"source_message_id":message.id}
                for recipient in (Team.RADAR, Team.COMMAND):
                    db.send_message(Team.TOWER,recipient,"Rejected malformed runway request",
                                    message_type="runway_response",metadata=result)
            db.connection.execute("INSERT INTO tower_processed_messages VALUES (?,?)",
                                  (message.id,current_timestamp()))
            db.connection.execute("UPDATE messages SET read_at=CURRENT_TIMESTAMP WHERE id=?", (message.id,))
            results.append(result)
    return results


if __name__ == "__main__":
    import argparse
    import time
    from src.db import create_database
    parser = argparse.ArgumentParser(description="Tower Radar-request worker")
    parser.add_argument("--database", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    db = create_database(args.database)
    try:
        while True:
            process_requests(db)
            if args.once:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        db.close()
