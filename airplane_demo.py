"""Small aircraft simulation: python airplane_demo.py --updates 3 --interval 1."""

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
import math
import time

from src.db import create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.position_update import UPDATE_INTERVAL_SECONDS, update_aircraft_position
from src.tower.messaging import load_runways
from src.tower.service import TowerService
from uuid import uuid4


def initialize_runways(db):
    """Legacy demo entry point; inventory now comes from Tower's repository."""
    return load_runways(db)


def build_aircraft_ids(base_id: str, aircraft_count: int) -> list[str]:
    if aircraft_count < 1:
        raise ValueError('aircraft_count must be at least 1')
    if aircraft_count == 1:
        return [base_id]

    prefix = ''.join(ch for ch in base_id if not ch.isdigit())
    suffix = ''.join(ch for ch in base_id if ch.isdigit())
    start_index = int(suffix) if suffix else 1
    ids = []
    for offset in range(aircraft_count):
        number = start_index + offset
        ids.append(f'{prefix}{number:03d}')
    return ids


def _next_timestamp(previous: str | None, offset_seconds: int = 1) -> str:
    current = datetime.now(timezone.utc)
    if previous:
        previous_dt = datetime.strptime(previous, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        current = max(current, previous_dt + timedelta(seconds=offset_seconds))
    return current.strftime('%Y-%m-%dT%H:%M:%S')


def record_clearance_request(db, aircraft_id: str, clearance_type: str, *, message: str | None = None):
    payload = {
        'aircraft_id': aircraft_id,
        'status': 'requested',
        'request_type': clearance_type,
        'message': message or f'{aircraft_id} requested {clearance_type} clearance',
    }
    db.upsert_shared_state(f'request:{aircraft_id}:{clearance_type}', payload, category='request')
    return payload


def simulate_departure(store: AircraftStore, db, aircraft_id: str, *, updates: int, interval: float) -> AircraftState:
    state = store.get(aircraft_id)
    if state is None:
        state = store.create(AircraftState(aircraft_id, 40.6413, -73.7781, 'Parked', 0, _next_timestamp(None)))

    print('Initial state:', json.dumps(asdict(state)))
    runways = load_runways(db)
    service = TowerService(db)
    decision = service.request_runway(str(uuid4()), aircraft_id, runways[0]['airport_id'], 'departure')
    if decision['outcome'] != 'cleared':
        raise RuntimeError(f"Departure not cleared: {decision}")
    print(f'Tower: requesting departure clearance for {aircraft_id}')
    store.send_message(aircraft_id, 'Requesting departure clearance', to_tower=True)
    for message in store.receive_messages(for_tower=True, aircraft_id=aircraft_id):
        print('Tower received:', message['content'])

    store.send_message(aircraft_id, 'Departure approved (simulation)', to_tower=False)
    for message in store.receive_messages(for_tower=False, aircraft_id=aircraft_id):
        print('Aircraft received:', message['content'])

    for _ in range(updates):
        time.sleep(interval)
        state = replace(
            state,
            latitude=min(90, state.latitude + 0.001),
            longitude=min(180, state.longitude + 0.001),
            altitude=state.altitude + 50,
            status='Departing',
            timestamp=_next_timestamp(state.timestamp),
        )
        accepted = update_aircraft_position(store, asdict(state))
        print('Radar accepted:', accepted, json.dumps(asdict(store.get(aircraft_id))))
    service.complete_request(decision['request_id'])
    return state


def _request_landing(store, db, aircraft_id):
    state = store.get(aircraft_id)
    if state is None:
        raise ValueError(f'Unknown aircraft {aircraft_id!r} for landing simulation')
    runways = load_runways(db)
    state = replace(state, latitude=max(-90, state.latitude - 0.001),
                    longitude=max(-180, state.longitude - 0.001),
                    altitude=max(1, state.altitude - 40), status='Landing',
                    timestamp=_next_timestamp(state.timestamp))
    accepted = update_aircraft_position(store, asdict(state))
    print(f'Radar accepted landing update for {aircraft_id}:', accepted)
    if not accepted:
        raise ValueError('Stale landing simulation update')
    service = TowerService(db)
    decision = service.request_runway(str(uuid4()), aircraft_id, runways[0]['airport_id'], 'landing')
    print(f'Landing decision for {aircraft_id}:', decision)
    return decision


def _complete_landing(store, db, decision, landing_hold_seconds):
    if landing_hold_seconds > 0:
        time.sleep(landing_hold_seconds)
    # Flight completion is simulated; reservation release/promotion is real Tower logic.
    state = store.get(decision['aircraft_id'])
    update_aircraft_position(store, asdict(replace(state, status='Parked', altitude=0,
                                                  timestamp=_next_timestamp(state.timestamp))))
    TowerService(db).complete_request(decision['request_id'])
    print(f"{state.id} has landed and runway is available again.")


def simulate_landing(store, db, aircraft_id, *, landing_hold_seconds, queue_position=None):
    """Legacy single landing demo; queue_position is now computed by Tower."""
    decision = _request_landing(store, db, aircraft_id)
    if decision['outcome'] != 'cleared':
        return False
    _complete_landing(store, db, decision, landing_hold_seconds)
    return True


def fabricate_landing_queue(store, db, aircraft_ids, *, landing_hold_seconds):
    """Submit the batch before completing flights; Tower owns the persistent FIFO."""
    requests = [_request_landing(store, db, aircraft_id) for aircraft_id in aircraft_ids]
    service = TowerService(db)
    completed = []
    for request in requests:
        decision = service.get_decision(request['request_id'])
        if decision['outcome'] != 'cleared':
            raise RuntimeError('Demo has no completable reservation; inspect Tower queue/configuration')
        _complete_landing(store, db, decision, landing_hold_seconds)
        completed.append(decision['aircraft_id'])
    return completed


def simulate_aircraft(store: AircraftStore, db, aircraft_id: str, *, updates: int, interval: float, landing_hold_seconds: float, full_cycle: bool = False) -> bool:
    state = simulate_departure(store, db, aircraft_id, updates=updates, interval=interval) if not full_cycle else None
    if full_cycle:
        state = simulate_departure(store, db, aircraft_id, updates=updates, interval=interval)
        return simulate_landing(store, db, aircraft_id, landing_hold_seconds=landing_hold_seconds)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default='data/airplane_demo.db')
    parser.add_argument('--id', default='AC001')
    parser.add_argument('--aircraft-count', type=int, default=1)
    parser.add_argument('--updates', type=int, default=3)
    parser.add_argument('--interval', type=float, default=UPDATE_INTERVAL_SECONDS)
    parser.add_argument('--landing-hold-seconds', type=float, default=0.0, help='If > 0, simulate a landing clearance and keep the assigned runway occupied for this many seconds.')
    parser.add_argument('--reset', action='store_true', help='Delete the existing database file before starting the simulation from a clean state.')
    args = parser.parse_args()
    if args.aircraft_count < 1:
        parser.error('aircraft-count must be at least 1')
    if args.updates < 1 or not math.isfinite(args.interval) or args.interval < 1:
        parser.error('updates must be positive and interval must be at least one second')
    if args.reset:
        try:
            import os
            os.remove(args.database)
        except FileNotFoundError:
            pass
    db = create_database(args.database)
    try:
        store = AircraftStore(db)
        load_runways(db)
        aircraft_ids = build_aircraft_ids(args.id, args.aircraft_count)
        completed_departures = []
        for aircraft_id in aircraft_ids:
            if simulate_departure(store, db, aircraft_id, updates=args.updates, interval=args.interval):
                completed_departures.append(aircraft_id)

        if args.landing_hold_seconds > 0 or args.aircraft_count > 1:
            landed = fabricate_landing_queue(
                store,
                db,
                completed_departures,
                landing_hold_seconds=args.landing_hold_seconds,
            )
            print(f'Landing complete: {landed}')
            print(f'All {len(landed)} aircraft have completed departure and landing. Simulation complete.')
        else:
            print(f'Completed aircraft: {completed_departures}')
            print(f'All {len(completed_departures)} aircraft have completed departure. Simulation complete.')
    finally:
        db.close()


if __name__ == '__main__':
    main()
