#!/usr/bin/env python3
import argparse
from datetime import datetime, timezone

from src.db import Team, create_database
from src.radar.aircraft import AircraftState, AircraftStore


AIRPORT_CENTER_LAT = 40.6413
AIRPORT_CENTER_LON = -73.7781
# Keep airborne aircraft just outside the airport perimeter, but close enough to remain visible on screen.
OUTSIDE_AIRBORNE_COORDINATES = (40.6454, -73.7748)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def normalize_coordinates_for_status(latitude: float, longitude: float, status: str):
    if status == "Airborne":
        lat_delta = abs(float(latitude) - AIRPORT_CENTER_LAT)
        lon_delta = abs(float(longitude) - AIRPORT_CENTER_LON)
        if lat_delta <= 0.01 and lon_delta <= 0.01:
            return OUTSIDE_AIRBORNE_COORDINATES[0], OUTSIDE_AIRBORNE_COORDINATES[1]
    return float(latitude), float(longitude)


def create_or_update_aircraft(
    aircraft_id: str,
    latitude: float,
    longitude: float,
    altitude: float,
    status: str,
    database_path: str = "data/atc_system.db",
):
    """Create/update a radar aircraft record.

    Grounded aircraft should use status="Parked". Flying aircraft should use
    status="Airborne" and then request landing clearance explicitly via the
    separate clearance-request script.
    """
    db = create_database(database_path)
    try:
        normalized_latitude, normalized_longitude = normalize_coordinates_for_status(latitude, longitude, status)
        store = AircraftStore(db)
        state = AircraftState(
            id=aircraft_id,
            latitude=float(normalized_latitude),
            longitude=float(normalized_longitude),
            status=status,
            altitude=float(altitude),
            timestamp=utc_timestamp(),
        )

        existing = store.get(aircraft_id)
        if existing:
            store.connection.execute(
                """
                UPDATE aircraft
                SET latitude=?, longitude=?, status=?, altitude=?, timestamp=?
                WHERE id=?
                """,
                (
                    state.latitude,
                    state.longitude,
                    state.status,
                    state.altitude,
                    state.timestamp,
                    state.id,
                ),
            )
            store.connection.commit()
            store._save_shared_state(state)
            print(f"Updated aircraft {aircraft_id}")
        else:
            store.create(state)
            print(f"Created aircraft {aircraft_id}")

        print(f"Aircraft state: {state}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Radar creates an aircraft in the ATC system. "
            "Use status=Parked for planes on the ground and status=Airborne "
            "for planes already flying; landing clearance must be requested manually."
        )
    )
    parser.add_argument("--id", required=True, help="Aircraft ID, e.g. AC101")
    parser.add_argument("--latitude", type=float, required=True, help="Latitude value")
    parser.add_argument("--longitude", type=float, required=True, help="Longitude value")
    parser.add_argument("--altitude", type=float, default=0.0, help="Altitude in meters above ground")
    parser.add_argument(
        "--status",
        choices=["Parked", "Airborne", "Landing"],
        required=True,
        help="Parked for ground aircraft; Airborne for aircraft in flight; Tower still assigns landing runway manually.",
    )
    parser.add_argument("--db", default="data/atc_system.db", help="SQLite database file")
    args = parser.parse_args()

    create_or_update_aircraft(
        aircraft_id=args.id,
        latitude=args.latitude,
        longitude=args.longitude,
        altitude=args.altitude,
        status=args.status,
        database_path=args.db,
    )
