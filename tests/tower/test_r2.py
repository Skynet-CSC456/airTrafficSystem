"""REQ-TWR-010, REQ-TWR-011 and REQ-TWR-012 without a UI or another team worker."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.db import Team, create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.tower._r2_policy import advance_in_queue, unsafe_separation
from src.tower.r2 import TowerR2Service
from src.tower.r2_worker import run_cycle

START = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


@pytest.fixture
def system(tmp_path):
    clock = SimpleNamespace(now=START.timestamp())
    path = str(tmp_path / 'r2.db')
    with closing(create_database(path)) as db:
        tower = TowerR2Service(db, clock=lambda: clock.now, max_track_age_seconds=60)
        tower.airports.create_airport('JFK', 22000, 40, -73, ['R1'])
        yield db, tower, AircraftStore(db), clock, path


def plane(store, name, *, parked=False, latitude=40, longitude=-73):
    return store.create(AircraftState(name, latitude, longitude, 'Parked' if parked else 'Landing',
                                      0 if parked else 500, START.strftime('%Y-%m-%dT%H:%M:%S')))


def update(store, name, **changes):
    assert store.update(replace(store.get(name), timestamp='2026-11-16T12:00:01', **changes))


def send(db, message_type, event_id, **fields):
    return db.send_message(Team.RADAR, Team.TOWER, message_type, message_type=message_type,
                           metadata=dict(schema_version=1, event_id=event_id,
                                         timestamp=START.isoformat(), **fields))


def conflict(db, name, reason, event_id, *, emergency=False):
    return send(db, 'conflict', event_id, request_id=name, aircraft_id=name, reason=reason,
                emergency=emergency)


def queue(tower):
    decisions = tower.list_queue('JFK')
    assert [item['queue_position'] for item in decisions] == list(range(1, len(decisions) + 1))
    return [item['aircraft_id'] for item in decisions]


def messages(db, recipient, *types):
    return [message for message in db.receive_messages(recipient, sender=Team.TOWER)
            if message.message_type in types]


def test_pure_queue_move_is_stable_and_clamps_at_front():
    assert advance_in_queue('ABCDE', 'D', 2) == list('ADBCE')
    assert advance_in_queue('ABC', 'B', 3) == list('BAC')


def test_priorities_apply_to_actual_fifo_and_do_not_stack(system):
    db, tower, store, clock, path = system
    plane(store, 'P', parked=True)
    tower.park_aircraft('P', 'JFK')
    for name in 'ABCDE':
        plane(store, name, latitude=40, longitude=-73 + 15 * ('ABCDE'.index(name)))
        tower.request_runway(name, name, 'JFK', 'landing')
    assert queue(tower) == list('ABCDE')
    conflict(db, 'D', 'delay_2h', 'D2')
    run_cycle(db, tower)
    assert queue(tower) == list('ABDCE')
    conflict(db, 'D', 'delay_4h', 'D4')
    run_cycle(db, tower)
    assert queue(tower) == list('ADBCE')
    conflict(db, 'E', 'low_fuel', 'EF')
    run_cycle(db, tower)
    assert queue(tower) == list('AEDBC')
    conflict(db, 'E', 'delay_4h', 'E-lower')
    conflict(db, 'D', 'delay_4h', 'D4')
    conflict(db, 'D', 'delay_4h', 'D4-repeat')
    with closing(create_database(path)) as restarted:
        service = TowerR2Service(restarted, clock=lambda: clock.now)
        run_cycle(restarted, service)
        run_cycle(restarted, service)
        assert queue(service) == list('AEDBC')
    data = messages(db, Team.COMMAND, 'conflict', 'conflict_resolved')
    assert [message.metadata['event_id'] for message in data] == ['D2', 'D2', 'D4', 'D4', 'EF', 'EF']
    assert all(message.metadata['severity'] == 2 for message in data)
    assert [message.metadata['event_id'] for message in messages(db, Team.RADAR, 'conflict_resolved')].count('D4') == 1


def test_assignment_deadline_survives_restart_and_gates_actual_promotion(system):
    db, tower, store, clock, path = system
    plane(store, 'P', parked=True)
    tower.park_aircraft('P', 'JFK')
    for name in 'ABCD':
        plane(store, name)
        tower.request_runway(name, name, 'JFK', 'landing')
    conflict(db, 'D', 'low_fuel', 'fuel')
    run_cycle(db, tower)
    assert queue(tower) == list('DABC')
    db.send_message(Team.RADAR, Team.TOWER, 'Depart', message_type='runway_request',
                    metadata=dict(schema_version=1, request_id='depart', aircraft_id='P',
                                  airport_id='JFK', operation='departure', timestamp=START.isoformat()))
    run_cycle(db, tower)
    deadline = tower.release_eligible_at('depart')
    assert deadline == clock.now + 30
    assert tower.occupancy(aircraft_id='P')[0]['phase'] == 'departing'
    assert not tower.airports.list_runways('JFK')[0]['available']
    clock.now += 29
    update(store, 'P', status='Departing', altitude=500)
    send(db, 'operation_finished', 'finish', request_id='depart', aircraft_id='P', operation='departure')
    assert run_cycle(db, tower) == []
    assert tower.get_decision('depart')['outcome'] == 'cleared'
    assert not messages(db, Team.COMMAND, 'runway_completed')
    with pytest.raises(ValueError, match='release deadline'):
        tower.cancel_request('depart')
    with closing(create_database(path)) as restarted:
        service = TowerR2Service(restarted, clock=lambda: clock.now)
        assert service.release_eligible_at('depart') == deadline
        clock.now += 1
        completed, = run_cycle(restarted, service)
        assert completed['outcome'] == 'completed'
        assert service.occupancy(aircraft_id='P') == []
        assert service.get_decision('D')['runway_id'] == 'R1'
        assert service.release_eligible_at('D') == clock.now + 30
        assert queue(service) == list('ABC')
        send(db, 'operation_finished', 'finish', request_id='depart', aircraft_id='P', operation='departure')
        assert run_cycle(restarted, service) == []
    assert len(messages(db, Team.COMMAND, 'runway_completed')) == 1


def test_landing_keeps_stationary_ownership_after_deadline(system):
    _, tower, store, clock, _ = system
    plane(store, 'A')
    tower.request_runway('A', 'A', 'JFK', 'landing')
    plane(store, 'B')
    tower.request_runway('B', 'B', 'JFK', 'landing')
    clock.now += 30
    assert tower.release_due() == []
    assert tower.get_decision('A')['outcome'] == 'cleared'
    with pytest.raises(ValueError, match='Radar state'):
        tower.complete_request('A')
    update(store, 'A', status='Parked', altitude=0)
    assert tower.complete_request('A')['outcome'] == 'completed'
    assert tower.occupancy(aircraft_id='A')[0]['phase'] == 'parked'
    assert not tower.airports.list_runways('JFK')[0]['available']
    assert queue(tower) == ['B']


@pytest.mark.parametrize(('coordinates', 'expected'), [
    ((9, 9), True), ((10, 1), False), ((1, 10), False), ((0, 179), False),
])
def test_separation_is_safe_at_ten_degrees_on_either_axis(coordinates, expected):
    left = SimpleNamespace(latitude=0, longitude=0)
    right = SimpleNamespace(latitude=coordinates[0], longitude=coordinates[1])
    assert unsafe_separation(left, right) is expected
    assert unsafe_separation(SimpleNamespace(latitude=0, longitude=-179),
                             SimpleNamespace(latitude=0, longitude=179))


def test_separation_alert_is_correlated_and_resolves_only_with_radar_data(system):
    db, tower, store, clock, _ = system
    plane(store, 'A', latitude=40, longitude=-73)
    plane(store, 'B', latitude=41, longitude=-74)
    run_cycle(db, tower)
    alert, = messages(db, Team.COMMAND, 'unsafe_separation')
    assert alert.metadata['aircraft_ids'] == ['A', 'B']
    assert alert.metadata['severity'] == 1
    send(db, 'unsafe_separation_resolved', 'early', conflict_id=alert.metadata['conflict_id'])
    run_cycle(db, tower)
    assert len(messages(db, Team.COMMAND, 'unsafe_separation')) == 1
    assert not messages(db, Team.COMMAND, 'unsafe_separation_resolved')
    clock.now += 1
    update(store, 'B', latitude=50)
    send(db, 'unsafe_separation_resolved', 'resolved', conflict_id=alert.metadata['conflict_id'])
    run_cycle(db, tower)
    resolved, = messages(db, Team.COMMAND, 'unsafe_separation_resolved')
    assert resolved.metadata['conflict_id'] == alert.metadata['conflict_id']
    assert not resolved.metadata['active']


def test_stale_data_neither_opens_nor_clears_alert(system):
    db, tower, store, clock, _ = system
    plane(store, 'A')
    plane(store, 'B')
    clock.now += 61
    tower.evaluate_separation()
    assert not messages(db, Team.COMMAND, 'unsafe_separation')
    clock.now -= 61
    tower.evaluate_separation()
    clock.now += 61
    store.delete('B')
    tower.evaluate_separation()
    assert len(messages(db, Team.COMMAND, 'unsafe_separation')) == 1
    assert not messages(db, Team.COMMAND, 'unsafe_separation_resolved')


def test_malformed_emergency_and_wrong_aircraft_leave_order_alone(system):
    db, tower, store, _, _ = system
    tower.airports.update_runway_status('JFK', 'R1', False)
    plane(store, 'A')
    tower.request_runway('A', 'A', 'JFK', 'landing')
    bad = conflict(db, 'A', 'low_fuel', 'bad', emergency=True)
    missing = send(db, 'conflict', 'missing', request_id='A', reason='low_fuel')
    wrong = send(db, 'operation_finished', 'wrong', request_id='A', aircraft_id='B', operation='landing')
    run_cycle(db, tower)
    run_cycle(db, tower)
    assert queue(tower) == ['A']
    assert [msg.metadata['source_message_id'] for msg in messages(db, Team.RADAR, 'tower_r2_rejected')] == [
        bad, missing, wrong,
    ]
    assert not messages(db, Team.COMMAND, 'conflict')


def test_concurrent_workers_dedupe_one_event_and_roll_back_failed_delivery(system, monkeypatch):
    db, tower, store, clock, path = system
    tower.airports.update_runway_status('JFK', 'R1', False)
    for name in 'ABC':
        plane(store, name)
        tower.request_runway(name, name, 'JFK', 'landing')
    conflict(db, 'C', 'delay_2h', 'retry')
    conflict(db, 'C', 'delay_2h', 'retry')
    original = db.send_message

    def fail(sender, recipient, content, **kwargs):
        if kwargs.get('message_type') == 'conflict_resolved':
            raise RuntimeError('Simulated write failure')
        return original(sender, recipient, content, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(db, 'send_message', fail)
        with pytest.raises(RuntimeError, match='Simulated write failure'):
            run_cycle(db, tower)
    assert queue(tower) == list('ABC')
    assert not messages(db, Team.COMMAND, 'conflict')

    def worker():
        with closing(create_database(path)) as connection:
            run_cycle(connection, TowerR2Service(connection, clock=lambda: clock.now))

    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(lambda _: worker(), range(2)))
    assert queue(tower) == list('ACB')
    assert len(messages(db, Team.COMMAND, 'conflict')) == 1


def test_repeated_one_second_signals_do_not_stack_across_restart(system):
    db, tower, store, clock, path = system
    for name in 'ABCDE':
        plane(store, name)
        tower.request_runway(name, name, 'JFK', 'landing')
    for second in range(4):
        db.send_message(Team.RADAR, Team.TOWER, 'Conflict', message_type='conflict', metadata={
            'schema_version': 1, 'event_id': 'fuel-E' if second < 2 else f'retry-{second}',
            'timestamp': datetime.fromtimestamp(clock.now + second, timezone.utc).isoformat(),
            'request_id': 'E', 'aircraft_id': 'E', 'reason': 'low_fuel', 'emergency': False,
        })
        with closing(create_database(path)) as restarted:
            run_cycle(restarted, TowerR2Service(restarted, clock=lambda: clock.now))
    assert queue(tower) == list('EBCD')
    assert len(messages(db, Team.COMMAND, 'conflict')) == 1
    assert not messages(db, Team.RADAR, 'tower_r2_rejected')


def test_corrective_target_checks_other_tracks_and_wraps_at_pole(system):
    from src.tower._r2_policy import corrective_target
    db, tower, store, clock, _ = system
    plane(store, 'A', latitude=89, longitude=179)
    plane(store, 'B', latitude=89, longitude=-179)
    plane(store, 'C', latitude=79, longitude=-169)
    target = corrective_target(store.get('B'), store.list())
    assert target == {'latitude': 79, 'longitude': 171, 'latitude_step': -10, 'longitude_step': -10}
    run_cycle(db, tower)
    order, = messages(db, Team.RADAR, 'change_position')
    assert order.metadata['aircraft_id'] == 'B'
    assert order.metadata['observed_at'] == store.get('B').timestamp
    assert store.get('B').latitude == 89
    run_cycle(db, tower)
    assert len(messages(db, Team.RADAR, 'change_position')) == 1
    assert not store.update(replace(store.get('B'), latitude=79, longitude=171))
    run_cycle(db, tower)
    assert not messages(db, Team.COMMAND, 'unsafe_separation_resolved')
    clock.now += 1
    update(store, 'B', latitude=79, longitude=171)
    run_cycle(db, tower)
    resolved, = messages(db, Team.COMMAND, 'unsafe_separation_resolved')
    assert resolved.metadata['conflict_id'] == order.metadata['conflict_id']


def test_manual_assignment_uses_r2_deadline_and_authoritative_fifo(system):
    from src.tower.messaging import assign_runway_to_aircraft
    db, tower, store, clock, _ = system
    plane(store, 'A')
    plane(store, 'B')
    assigned = assign_runway_to_aircraft(db, 'A', 'R1', service_factory=lambda _: tower)
    assert tower.release_eligible_at(assigned['request_id']) == clock.now + 30
    with pytest.raises(ValueError, match='No safe runway'):
        assign_runway_to_aircraft(db, 'B', 'R1', service_factory=lambda _: tower)
    update(store, 'A', status='Parked', altitude=0)
    clock.now += 29
    assert tower.complete_request(assigned['request_id'])['outcome'] == 'cleared'
    clock.now += 1
    completed, = tower.release_due()
    assert completed['request_id'] == assigned['request_id']
    assert tower.occupancy(aircraft_id='A')[0]['phase'] == 'parked'
