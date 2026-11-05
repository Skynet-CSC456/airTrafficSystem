#!/usr/bin/env python3
import argparse

from src.db import Team, create_database


def request_clearance(aircraft_id: str, request_type: str, altitude: float, database_path: str = "data/atc_system.db"):
    if request_type not in {"departure", "landing"}:
        raise ValueError("request_type must be 'departure' or 'landing'")

    db = create_database(database_path)
    try:
        if request_type == "departure":
            message = (
                f"{aircraft_id} is on the ground at altitude {altitude}m and requests departure clearance."
            )
        else:
            message = (
                f"{aircraft_id} is airborne at altitude {altitude}m and requests landing clearance."
            )

        db.send_message(Team.RADAR, Team.TOWER, message)
        print(f"Radar sent to Tower: {message}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Radar requests departure or landing clearance from Tower.")
    parser.add_argument("--id", required=True, help="Aircraft ID, e.g. AC101")
    parser.add_argument("--type", choices=["departure", "landing"], required=True, help="Type of clearance request")
    parser.add_argument("--altitude", type=float, default=0.0, help="Aircraft altitude in meters above ground")
    parser.add_argument("--db", default="data/atc_system.db", help="SQLite database file")
    args = parser.parse_args()

    request_clearance(
        aircraft_id=args.id,
        request_type=args.type,
        altitude=args.altitude,
        database_path=args.db,
    )
