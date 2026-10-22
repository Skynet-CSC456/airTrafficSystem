"""Seed the database with a few airplanes: python seed_aircraft.py [--database PATH]."""

import argparse
from datetime import datetime, timezone

from src.db import create_database
from src.radar.aircraft import AircraftState, AircraftStore

# (id, latitude, longitude, status, altitude in meters)
SEED_AIRCRAFT = [
    ("AC001", 40.6413, -73.7781, "Parked", 0),
    ("AC002", 40.6500, -73.7900, "Departing", 450),
    ("AC003", 40.6700, -73.8200, "Departing", 1200),
    ("AC004", 40.6600, -73.8000, "Landing", 300),
    ("AC005", 40.6450, -73.7850, "Landing", 90),
]


def seed_aircraft(store: AircraftStore) -> list[AircraftState]:
    """Insert the seed airplanes, replacing any that already exist with the same ID."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    states = []
    for aircraft_id, latitude, longitude, status, altitude in SEED_AIRCRAFT:
        store.delete(aircraft_id)
        states.append(
            store.create(AircraftState(aircraft_id, latitude, longitude, status, altitude, timestamp))
        )
    return states


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=None, help="SQLite file (default: $SQLITE_DATABASE or data/atc_system.db)")
    args = parser.parse_args()

    db = create_database(args.database)
    try:
        for state in seed_aircraft(AircraftStore(db)):
            print(f"Seeded {state.id}: {state.status}, {state.altitude:g} m")
    finally:
        db.close()


if __name__ == "__main__":
    main()
