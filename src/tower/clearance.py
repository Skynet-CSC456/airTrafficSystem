"""Tower runway and clearance safety logic.

REQ-TWR-003: check whether a clearance decision is approved.
REQ-TWR-005: choose a safe runway and issue landing clearance only when it is valid.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone


def _coerce_datetime(value):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    raise TypeError(f"Unsupported timestamp type: {type(value)!r}")


def update_runway_availability(runways: Sequence[Mapping[str, object]], *, now=None):
    """Set runway availability based on occupied_until timestamps.

    A runway is treated as occupied for 3 seconds after assignment. This keeps the
    runway unavailable for other landing requests in that window while preserving
    safety-first assignment semantics.
    """
    current_time = _coerce_datetime(now)
    for runway in runways:
        occupied_until = runway.get("occupied_until")
        if occupied_until is not None:
            release_time = _coerce_datetime(occupied_until)
            runway["available"] = current_time >= release_time
        elif runway.get("available") is None:
            runway["available"] = True
    return list(runways)


def is_aircraft_cleared(status):
    """REQ-TWR-003: only an explicit cleared status is considered safe."""
    return str(status).lower() == "cleared"


def runway_is_safe(runway: Mapping[str, object], aircraft: Mapping[str, object] | None = None) -> bool:
    """Runway safety check: the runway must be available and compatible with the aircraft."""
    if runway.get("available") is not True:
        return False
    if aircraft is None:
        return True
    aircraft_status = str(aircraft.get("status", "")).lower()
    if aircraft_status == "landing":
        allowed = set(("landing", "mixed", "active"))
        runway_mode = str(runway.get("mode", "landing")).lower()
        return runway_mode in allowed
    if aircraft_status in {"parked", "departing"}:
        return True
    return True


def find_safe_runway(runways: Sequence[Mapping[str, object]], aircraft: Mapping[str, object] | None = None):
    """Return the first available runway that is safe for the aircraft, else None."""
    for runway in runways:
        if runway_is_safe(runway, aircraft):
            return runway
    return None


def assign_runway(runways: Sequence[Mapping[str, object]], aircraft: Mapping[str, object] | None = None, *, runway_id: str | None = None, hold_seconds: int = 3):
    """Issue a landing clearance only when the chosen runway is safe.

    The selected runway is marked unavailable for `hold_seconds` seconds after the
    clearance is granted to preserve safe separation between landings.
    """
    if aircraft is None:
        raise ValueError("Aircraft state is required for clearance assignment")
    candidates = list(runways)
    update_runway_availability(candidates)
    if runway_id is not None:
        candidates = [runway for runway in candidates if str(runway.get("id")) == str(runway_id)]
    if not candidates:
        raise ValueError(f"Runway {runway_id!r} not found")
    chosen = find_safe_runway(candidates, aircraft)
    if chosen is None:
        raise ValueError("No safe runway is available for the current aircraft state")
    chosen["available"] = False
    chosen["occupied_until"] = (datetime.now(timezone.utc) + timedelta(seconds=hold_seconds)).isoformat()
    return {
        "aircraft_id": aircraft.get("id"),
        "runway_id": chosen.get("id"),
        "status": "cleared",
        "safe": True,
        "occupied_until": chosen["occupied_until"],
    }


def issue_landing_clearance(runways, aircraft, *, runway_id=None):
    """Compatibility wrapper for the landing clearance flow."""
    return assign_runway(runways, aircraft, runway_id=runway_id)


def evaluate_runway_decision(runways: Sequence[Mapping[str, object]], aircraft: Mapping[str, object] | None = None, *, runway_id: str | None = None, queue_position: int | None = None):
    """Return either a clearance or a queue decision for landing requests."""
    if aircraft is None:
        raise ValueError("Aircraft state is required for clearance assignment")
    candidates = list(runways)
    update_runway_availability(candidates)
    if runway_id is not None:
        candidates = [runway for runway in candidates if str(runway.get("id")) == str(runway_id)]
    chosen = find_safe_runway(candidates, aircraft)
    if chosen is not None:
        chosen["available"] = False
        chosen["occupied_until"] = (datetime.now(timezone.utc) + timedelta(seconds=3)).isoformat()
        return {
            "aircraft_id": aircraft.get("id"),
            "runway_id": chosen.get("id"),
            "status": "cleared",
            "safe": True,
            "queue_position": None,
            "occupied_until": chosen["occupied_until"],
        }
    return {
        "aircraft_id": aircraft.get("id"),
        "runway_id": None,
        "status": "queued",
        "safe": False,
        "queue_position": queue_position if queue_position is not None else 1,
        "occupied_until": None,
    }


def publish_runway_decision(db, aircraft, runways, *, runway_id=None, queue_position=None):
    """Persist a safe runway decision to the shared dashboard state."""
    decision = evaluate_runway_decision(runways, aircraft, runway_id=runway_id, queue_position=queue_position)
    if getattr(db, "upsert_shared_state", None) is None:
        raise TypeError("Database must expose upsert_shared_state() for shared-state decisions")
    db.upsert_shared_state(f"decision:{aircraft.get('id')}", decision, category="decision")
    if decision.get("runway_id") is not None:
        for runway in runways:
            if str(runway.get("id")) == str(decision["runway_id"]):
                runway_payload = {
                    "id": runway.get("id"),
                    "available": bool(runway.get("available", True)),
                    "mode": runway.get("mode", "landing"),
                    "occupied_until": runway.get("occupied_until"),
                }
                db.upsert_shared_state(f"runway:{runway.get('id')}", runway_payload, category="runway")
                break
    return decision
