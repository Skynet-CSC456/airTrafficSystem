"""Tower-owned stationary occupancy and native message-processor contracts.

Radar is used only through its existing public store to supply flight state.
No Command, React or shared integration application is imported here.
"""
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from src.db import Team, create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.tower.occupancy import OccupancyTowerService, process_requests_with_occupancy


@pytest.fixture
def system(tmp_path):
    db = create_database(str(tmp_path / 'occupancy.db'))
    tower = OccupancyTowerService(db)
    tower.airports.create_airport('JFK', 22000, 40.6413, -73.7781, ['R1'])
    store = AircraftStore(db)
    try:
        yield db, tower, store
    finally:
        db.close()


def create(store, aircraft_id, *, parked=False):
    return store.create(AircraftState(
        aircraft_id, 40.6413, -73.7781,
        'Parked' if parked else 'Landing', 0 if parked else 500,
        '2026-11-05T15:00:00',
    ))


def update(store, aircraft_id, status, altitude):
    current = store.get(aircraft_id)
    moment = datetime.strptime(current.timestamp, '%Y-%m-%dT%H:%M:%S')
    state = replace(current, status=status, altitude=altitude,
                    timestamp=(moment + timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%S'))
    assert store.update(state)


def park(tower, store, aircraft_id='P'):
    create(store, aircraft_id, parked=True)
    return tower.park_aircraft(aircraft_id, 'JFK')


def request(tower, aircraft_id, request_id, operation='landing', **kwargs):
    return tower.request_runway(request_id, aircraft_id, 'JFK', operation, **kwargs)


def send(db, aircraft_id, request_id, **overrides):
    metadata = dict(schema_version=1, aircraft_id=aircraft_id, request_id=request_id,
                    airport_id='JFK', operation='departure', timestamp='2026-11-05T15:00:00Z')
    metadata.update(overrides)
    return db.send_message(Team.RADAR, Team.TOWER, 'Runway request',
                           message_type='runway_request', metadata=metadata)


def test_parked_ownership_is_idempotent_and_naturally_ordered(system):
    _, tower, store = system
    tower.airports.add_runway('JFK', 'R10')
    tower.airports.add_runway('JFK', 'R2')
    owners = [park(tower, store, name) for name in ('P1', 'P2', 'P3')]
    assert [owner['runway_id'] for owner in owners] == ['R1', 'R2', 'R10']
    assert tower.park_aircraft('P1', 'JFK') == owners[0]
    assert len(tower.occupancy()) == 3
    assert not any(r['available'] for r in tower.airports.list_runways('JFK'))


def test_full_airport_rejects_an_extra_parked_owner_without_changing_tower(system):
    _, tower, store = system
    first = park(tower, store)
    create(store, 'EXTRA', parked=True)
    before = tower.snapshot()
    with pytest.raises(ValueError, match='full'):
        tower.park_aircraft('EXTRA', 'JFK')
    assert tower.occupancy() == [first]
    assert tower.snapshot() == before


def test_airborne_aircraft_cannot_claim_stationary_ownership(system):
    _, tower, store = system
    create(store, 'AIR')
    with pytest.raises(ValueError, match='grounded'):
        tower.park_aircraft('AIR', 'JFK')
    assert tower.occupancy() == []
    assert tower.airports.list_runways('JFK')[0]['available']


def test_landing_completion_waits_for_real_radar_ground_state(system):
    _, tower, store = system
    create(store, 'LAND')
    assert request(tower, 'LAND', 'land')['outcome'] == 'cleared'
    with pytest.raises(ValueError, match='Radar must record'):
        tower.complete_request('land')
    assert tower.get_decision('land')['outcome'] == 'cleared'
    assert tower.occupancy() == []
    assert not tower.airports.list_runways('JFK')[0]['available']


def test_landing_completion_retains_runway_and_replay_is_idempotent(system):
    db, tower, store = system
    create(store, 'LAND')
    request(tower, 'LAND', 'land')
    update(store, 'LAND', 'Parked', 0)
    assert tower.complete_request('land')['outcome'] == 'completed'
    messages_before = db.connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
    assert tower.complete_request('land')['outcome'] == 'completed'
    assert db.connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == messages_before
    assert tower.occupancy(aircraft_id='LAND')[0]['phase'] == 'parked'
    assert not tower.airports.list_runways('JFK')[0]['available']


def test_owned_departure_fifo_landing_retention_and_r11(system):
    _, tower, store = system
    park(tower, store)
    create(store, 'FIRST')
    create(store, 'SECOND')
    assert request(tower, 'FIRST', 'first')['queue_position'] == 1
    assert request(tower, 'SECOND', 'second')['queue_position'] == 2
    departure = request(tower, 'P', 'depart', 'departure')
    assert departure['outcome'] == 'cleared' and departure['runway_id'] == 'R1'
    assert [r['aircraft_id'] for r in tower.list_queue('JFK')] == ['FIRST', 'SECOND']
    assert tower.occupancy(aircraft_id='P')[0]['phase'] == 'departing'
    update(store, 'P', 'Departing', 500)
    tower.complete_request('depart')
    assert tower.occupancy(aircraft_id='P') == []
    assert tower.get_decision('first')['outcome'] == 'cleared'
    assert tower.get_decision('second')['outcome'] == 'queued'
    update(store, 'FIRST', 'Parked', 0)
    tower.complete_request('first')
    assert tower.get_decision('second')['outcome'] == 'queued'
    assert tower.occupancy(aircraft_id='FIRST')[0]['runway_id'] == 'R1'
    tower.airports.add_runway('JFK', 'R11')
    tower.promote_queue('JFK')
    assert tower.get_decision('second')['outcome'] == 'cleared'
    assert tower.get_decision('second')['runway_id'] == 'R11'


def test_departure_cannot_use_another_aircrafts_runway(system):
    _, tower, store = system
    park(tower, store)
    tower.airports.add_runway('JFK', 'R2')
    decision = request(tower, 'P', 'wrong', 'departure', runway_id='R2')
    assert decision['outcome'] == 'rejected'
    assert 'owned runway' in decision['reason']
    assert tower.occupancy(aircraft_id='P')[0]['phase'] == 'parked'
    assert tower.airports.list_runways('JFK')[1]['available']


def test_cancelled_owned_departure_retains_parked_occupancy(system):
    _, tower, store = system
    park(tower, store)
    create(store, 'WAIT')
    request(tower, 'WAIT', 'wait')
    request(tower, 'P', 'depart', 'departure')
    assert tower.cancel_request('depart')['outcome'] == 'rejected'
    assert tower.occupancy(aircraft_id='P')[0]['phase'] == 'parked'
    assert tower.get_decision('wait')['outcome'] == 'queued'
    assert not tower.airports.list_runways('JFK')[0]['available']


def test_native_radar_message_clears_owned_departure_and_notifies_both_clients(system):
    db, tower, store = system
    park(tower, store)
    message_id = send(db, 'P', 'native')
    process_requests_with_occupancy(db)
    assert tower.get_decision('native')['outcome'] == 'cleared'
    assert tower.occupancy(aircraft_id='P')[0]['phase'] == 'departing'
    assert db.connection.execute('SELECT 1 FROM tower_processed_messages WHERE message_id=?',
                                 (message_id,)).fetchone()
    for team in (Team.RADAR, Team.COMMAND):
        replies = [m for m in db.receive_messages(team)
                   if m.message_type == 'runway_response' and m.metadata['request_id'] == 'native']
        assert len(replies) == 1
        assert replies[0].metadata['schema_version'] == 1
        assert replies[0].metadata['outcome'] == 'cleared'
    before = db.connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
    assert process_requests_with_occupancy(db) == []
    assert db.connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == before


def test_malformed_native_message_has_no_occupancy_side_effects(system):
    db, tower, store = system
    original = park(tower, store)
    message_id = send(db, 'P', 'bad', schema_version=9)
    process_requests_with_occupancy(db)
    assert tower.occupancy() == [original]
    assert tower.get_decision('bad') is None
    assert not tower.airports.list_runways('JFK')[0]['available']
    assert db.connection.execute('SELECT 1 FROM tower_processed_messages WHERE message_id=?',
                                 (message_id,)).fetchone()


def test_processor_prepares_only_its_bounded_input_snapshot(system):
    db, tower, store = system
    tower.airports.add_runway('JFK', 'R2')
    park(tower, store, 'P1')
    park(tower, store, 'P2')
    send(db, 'P1', 'one')
    send(db, 'P2', 'two')
    process_requests_with_occupancy(db, limit=1)
    assert tower.get_decision('one')['outcome'] == 'cleared'
    assert tower.get_decision('two') is None
    assert db.connection.execute('SELECT COUNT(*) FROM tower_processed_messages').fetchone()[0] == 1
    process_requests_with_occupancy(db, limit=1)
    assert tower.get_decision('two')['outcome'] == 'cleared'


@pytest.mark.parametrize('limit', [0, -1, True, 1.5])
def test_processor_preserves_original_invalid_limit_errors(system, limit):
    db, tower, _ = system
    with pytest.raises(ValueError):
        process_requests_with_occupancy(db, limit=limit)
    assert tower.occupancy() == []


def test_failed_parking_rolls_back_closure_and_owner_write(system, monkeypatch):
    _, tower, store = system
    create(store, 'P', parked=True)
    original = tower._record
    def fail_after_record(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('occupancy persistence failed')
    monkeypatch.setattr(tower, '_record', fail_after_record)
    with pytest.raises(RuntimeError, match='persistence'):
        tower.park_aircraft('P', 'JFK')
    assert tower.occupancy() == []
    assert tower.airports.list_runways('JFK')[0]['available']


def test_failed_landing_completion_retains_original_reservation(system, monkeypatch):
    _, tower, store = system
    create(store, 'LAND')
    request(tower, 'LAND', 'land')
    update(store, 'LAND', 'Parked', 0)
    def fail(*args, **kwargs):
        raise RuntimeError('owner write failed')
    monkeypatch.setattr(tower, '_record', fail)
    with pytest.raises(RuntimeError, match='owner write'):
        tower.complete_request('land')
    assert tower.get_decision('land')['outcome'] == 'cleared'
    assert tower.occupancy() == []
    assert not tower.airports.list_runways('JFK')[0]['available']


def test_stationary_owner_survives_independent_connection_reopen(system):
    db, tower, store = system
    owner = park(tower, store)
    reopened = create_database(str(db.connection.execute('PRAGMA database_list').fetchone()[2]))
    try:
        again = OccupancyTowerService(reopened)
        assert again.occupancy() == [owner]
        assert not again.airports.list_runways('JFK')[0]['available']
    finally:
        reopened.close()
