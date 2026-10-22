"""REQ-RAD-003: Validate and ingest the latest aircraft position."""

from src.radar.aircraft import AircraftState, AircraftStore

UPDATE_INTERVAL_SECONDS = 5


def update_aircraft_position(store: AircraftStore, payload: dict) -> bool:
    """Ingest a complete state for a registered aircraft, rejecting stale updates."""
    state = AircraftState(**payload)
    return store.update(state)
