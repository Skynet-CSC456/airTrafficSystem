"""REQ-RAD-009: carry out Tower's "Unsafe Separation" instructions.

When two aircraft are too close, Tower sends Radar a ``change_position``
message with the coordinates to move one of them to, and the time of the Radar
observation it was based on (REQ-TWR-011). Radar moves the aircraft through its
normal update path (validation and the stale-update check), then sends Tower a
version-1 ``unsafe_separation_resolved`` message. An instruction based on an
observation that has since changed is discarded, as Tower asks.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

from src.db import MessageDatabase
from src.radar.aircraft import AircraftStore
from src.radar.position_update import update_aircraft_position
from src.radar.tower_inbox import send_to_tower, unprocessed_tower_messages

CHANGE_POSITION_TYPE = "change_position"
RESOLVED_TYPE = "unsafe_separation_resolved"
PROCESSED_KEY = "radar:separation_instructions_processed_through"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


def _number(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def process_unsafe_separation(db: MessageDatabase, *, now: datetime | None = None) -> list[str]:
    """REQ-RAD-009: apply each new change_position instruction once; return the aircraft moved.

    Instructions for unknown aircraft, with a stale observation time, or with
    missing or invalid coordinates or conflict ID are skipped and not retried.
    Each move is reported to Tower.
    """
    now = now or datetime.now(timezone.utc)
    store = AircraftStore(db)
    moved = []
    for _, _, metadata in unprocessed_tower_messages(db, PROCESSED_KEY, (CHANGE_POSITION_TYPE,)):
        aircraft_id, conflict_id = metadata.get("aircraft_id"), metadata.get("conflict_id")
        latitude, longitude = metadata.get("latitude"), metadata.get("longitude")
        current = store.get(aircraft_id) if isinstance(aircraft_id, str) else None
        if (current is None or not isinstance(conflict_id, str) or not conflict_id
                or not _number(latitude) or not _number(longitude)):
            continue
        if metadata.get("observed_at") != current.timestamp:
            continue  # Radar has a newer observation than the one Tower used
        # The move must be newer than the aircraft's last update to pass the stale-update check.
        last = datetime.strptime(current.timestamp, TIME_FORMAT).replace(tzinfo=timezone.utc)
        timestamp = max(now, last + timedelta(seconds=1)).strftime(TIME_FORMAT)
        try:
            moved_state = replace(current, latitude=float(latitude), longitude=float(longitude), timestamp=timestamp)
            payload = {key: value for key, value in asdict(moved_state).items() if key != "runway_id"}
            if not update_aircraft_position(store, payload):
                continue
        except ValueError:
            continue  # e.g. coordinates out of range
        send_to_tower(db, RESOLVED_TYPE, f"Unsafe Separation resolved: {aircraft_id} moved", {
            "event_id": f"separation-{conflict_id}-{aircraft_id}", "conflict_id": conflict_id,
        }, now=now)
        moved.append(aircraft_id)
    return moved
