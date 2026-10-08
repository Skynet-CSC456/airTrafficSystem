"""Small aircraft simulation: python airplane_demo.py --updates 3 --interval 1."""

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import math
import time

from src.db import create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.position_update import UPDATE_INTERVAL_SECONDS, update_aircraft_position


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default='data/airplane_demo.db')
    parser.add_argument('--id', default='AC001')
    parser.add_argument('--updates', type=int, default=3)
    parser.add_argument('--interval', type=float, default=UPDATE_INTERVAL_SECONDS)
    args = parser.parse_args()
    if args.updates < 1 or not math.isfinite(args.interval) or args.interval < 1:
        parser.error('updates must be positive and interval must be at least one second')
    db = create_database(args.database)
    try:
        store = AircraftStore(db)
        now = lambda: datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        state = store.get(args.id)
        if state is None:
            state = store.create(AircraftState(args.id, 40.6413, -73.7781, 'Parked', 0, now()))
        print('Initial state:', json.dumps(asdict(state)))
        store.send_message(args.id, 'Requesting departure clearance', to_tower=True)
        for message in store.receive_messages(for_tower=True, aircraft_id=args.id):
            print('Tower received:', message['content'])
        store.send_message(args.id, 'Departure approved (simulation)', to_tower=False)
        for message in store.receive_messages(for_tower=False, aircraft_id=args.id):
            print('Aircraft received:', message['content'])
        for _ in range(args.updates):
            time.sleep(args.interval)
            # Simple demo motion; no navigation or flight physics are modeled.
            state = replace(state, latitude=min(90, state.latitude + 0.001),
                            longitude=min(180, state.longitude + 0.001),
                            altitude=state.altitude + 50, status='Departing', timestamp=now())
            accepted = update_aircraft_position(store, asdict(state))
            print('Radar accepted:', accepted, json.dumps(asdict(store.get(args.id))))
        print('Aircraft records:', len(store.list()), '(latest state only)')
    finally:
        db.close()


if __name__ == '__main__':
    main()
