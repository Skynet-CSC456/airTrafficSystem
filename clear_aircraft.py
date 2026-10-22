"""Remove every airplane from the database: python clear_aircraft.py [--database PATH].

Also removes each airplane's live-state entry and its aircraft<->tower messages.
Team messages, runways, and decisions are left untouched.
"""

import argparse

from src.db import create_database
from src.radar.aircraft import AircraftStore


def clear_aircraft(store: AircraftStore) -> int:
    """Delete all airplanes and return how many were removed."""
    return sum(store.delete(state.id) for state in store.list())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=None, help="SQLite file (default: $SQLITE_DATABASE or data/atc_system.db)")
    args = parser.parse_args()

    db = create_database(args.database)
    try:
        print(f"Removed {clear_aircraft(AircraftStore(db))} airplane(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
