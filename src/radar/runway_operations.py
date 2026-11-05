"""REQ-RAD-007: finish an aircraft's runway operation once Tower's runway hold ends.

A landing aircraft is parked on the ground; a departing aircraft has left the
airspace and is no longer tracked.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from src.db import MessageDatabase
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.messaging import process_runway_assignments

TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


def _hold_has_ended(db: MessageDatabase, aircraft: AircraftState, now: datetime) -> bool:
    """True when Tower cleared this aircraft for its current runway and that hold is over."""
    record = db.get_shared_state(f"decision:{aircraft.id}")
    if record is None:
        return False
    decision = record["payload"]
    occupied_until = decision.get("occupied_until")
    return (
        decision.get("status") == "cleared"
        and decision.get("runway_id") == aircraft.runway_id
        and occupied_until is not None
        and datetime.fromisoformat(occupied_until).astimezone(timezone.utc) <= now
    )


def _is_departing(db: MessageDatabase, aircraft: AircraftState) -> bool:
    """Use Radar's runway request when there is one, otherwise the aircraft's status."""
    request = db.get_shared_state(f"request:{aircraft.id}")
    if request is not None and request["payload"].get("request_type"):
        return request["payload"]["request_type"] == "departure"
    return aircraft.status != "Landing"


def complete_runway_operations(db: MessageDatabase, *, now: datetime | None = None) -> list[tuple[str, str]]:
    """REQ-RAD-007: park landed aircraft and stop tracking departed ones.

    Only aircraft whose recorded runway Tower cleared, with the runway hold
    ended, are changed. Returns (aircraft_id, "landed" | "departed") pairs.
    """
    now = now or datetime.now(timezone.utc)
    store = AircraftStore(db)
    completed = []
    for aircraft in store.list():
        if aircraft.runway_id is None or not _hold_has_ended(db, aircraft, now):
            continue
        if _is_departing(db, aircraft):
            store.delete(aircraft.id)
            completed.append((aircraft.id, "departed"))
            continue
        # The landing update must be newer than the aircraft's last update.
        last = datetime.strptime(aircraft.timestamp, TIME_FORMAT).replace(tzinfo=timezone.utc)
        timestamp = max(now, last + timedelta(seconds=1)).strftime(TIME_FORMAT)
        store.update(replace(aircraft, status="Parked", altitude=0, timestamp=timestamp))
        store.assign_runway(aircraft.id, None)  # done with the runway; also stops a repeat
        completed.append((aircraft.id, "landed"))
    return completed


def radar_tick(db: MessageDatabase) -> list[tuple[str, str]]:
    """One pass of Radar's background work: record new Tower assignments, then finish runway operations."""
    process_runway_assignments(db)
    return complete_runway_operations(db)
