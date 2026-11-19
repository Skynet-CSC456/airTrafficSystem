"""REQ-RAD-007: carry out a cleared landing or departure and report it to Tower.

Once Tower clears an aircraft (REQ-RAD-006), Radar simulates the runway
operation for RUNWAY_OPERATION_SECONDS, then records the result and sends Tower
an ``operation_finished`` message (REQ-TWR-012):

- landing: the aircraft taxis to a free parking stand and is Parked at 0 m;
- departure: the aircraft is airborne (Departing) above the runway.

Tower releases the runway once the operation is confirmed and its own 30-second
hold has passed, then sends ``runway_completed``. A completed departure has
left Radar's airspace, so Radar stops tracking it (see messaging.py).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from src.db import MessageDatabase
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.conflicts import process_conflict_resolutions, send_conflict_signals
from src.radar.messaging import get_request, process_tower_decisions, save_request
from src.radar.separation import process_unsafe_separation
from src.radar.tower_inbox import send_to_tower

TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"
# How long Radar's simulated landing or take-off takes; it matches Tower's
# 30-second release delay (REQ-TWR-012), so the runway is free when it ends.
RUNWAY_OPERATION_SECONDS = 30
DEPARTURE_ALTITUDE = 300  # meters, once the aircraft has taken off
OPERATION_FINISHED_TYPE = "operation_finished"

# Parking stands (latitude, longitude): a column on the east side of the airport,
# clear of the runways drawn on the Command map.
PARKING_STANDS = (
    (40.6463, -73.7711),
    (40.6438, -73.7711),
    (40.6413, -73.7711),
    (40.6388, -73.7711),
    (40.6363, -73.7711),
)


def next_parking_stand(aircraft: list[AircraftState], aircraft_id: str) -> tuple[float, float]:
    """First stand no other aircraft is on; when all are taken, the last stand is shared."""
    occupied = {(plane.latitude, plane.longitude) for plane in aircraft if plane.id != aircraft_id}
    return next((stand for stand in PARKING_STANDS if stand not in occupied), PARKING_STANDS[-1])


def _operation_due(request: dict | None, aircraft: AircraftState, now: datetime) -> bool:
    """Tower cleared this aircraft for the runway it holds, Radar has not finished, and the time is up."""
    return (
        request is not None
        and request.get("status") == "cleared"
        and not request.get("finished")
        and request.get("runway_id") == aircraft.runway_id
        and datetime.fromisoformat(request["cleared_at"]) + timedelta(seconds=RUNWAY_OPERATION_SECONDS) <= now
    )


def complete_runway_operations(db: MessageDatabase, *, now: datetime | None = None) -> list[tuple[str, str]]:
    """REQ-RAD-007: finish each due landing or departure and tell Tower; return (aircraft_id, result) pairs.

    Each operation is finished and reported once, even with several Radar
    processes running.
    """
    now = now or datetime.now(timezone.utc)
    store = AircraftStore(db)
    finished = []
    for aircraft in store.list():
        if aircraft.runway_id is None or not _operation_due(get_request(db, aircraft.id), aircraft, now):
            continue
        current = store.get(aircraft.id)  # re-read: another Radar process may have finished it meanwhile
        request = get_request(db, aircraft.id)
        if current is None or current.runway_id is None or not _operation_due(request, current, now):
            continue
        last = datetime.strptime(current.timestamp, TIME_FORMAT).replace(tzinfo=timezone.utc)
        timestamp = max(now, last + timedelta(seconds=1)).strftime(TIME_FORMAT)
        if request["request_type"] == "landing":
            latitude, longitude = next_parking_stand(store.list(), current.id)
            result = "landed"
            updated = replace(current, latitude=latitude, longitude=longitude, status="Parked", altitude=0, timestamp=timestamp)
        else:
            result = "departed"
            updated = replace(current, status="Departing", altitude=max(current.altitude, DEPARTURE_ALTITUDE), timestamp=timestamp)
        if not store.update(updated):
            continue  # another Radar process finished it first
        store.assign_runway(current.id, None)  # off the runway
        save_request(db, current.id, {**request, "finished": True, "finished_at": now.isoformat()})
        send_to_tower(db, OPERATION_FINISHED_TYPE, f"{current.id} {result} on {request['runway_id']}", {
            "event_id": f"finished-{request['request_id']}",
            "request_id": request["request_id"], "aircraft_id": current.id, "operation": request["request_type"],
        }, now=now)
        finished.append((current.id, result))
    return finished


def radar_tick(db: MessageDatabase) -> list[tuple[str, str]]:
    """One pass of Radar's background work, run every second by the demo.

    Handle Tower's messages first (runway decisions, resolved or rejected
    conflicts, unsafe-separation moves), then finish due runway operations
    (REQ-RAD-007) and finally signal any open conflicts (REQ-RAD-008).
    Returns the runway operations finished.
    """
    process_tower_decisions(db)
    process_conflict_resolutions(db)
    process_unsafe_separation(db)
    finished = complete_runway_operations(db)
    send_conflict_signals(db)
    return finished
