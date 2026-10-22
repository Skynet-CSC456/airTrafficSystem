"""REQ-RAD-003 and REQ-RAD-004: ingestion, CRUD, and aircraft messaging.

REQ-RAD-001 is the filename alias for this branch's aircraft state requirement.
"""

from dataclasses import asdict, replace
import sqlite3

import pytest

from src.db import create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.position_update import update_aircraft_position


@pytest.fixture
def store():
    db = create_database(':memory:')
    yield AircraftStore(db)
    db.close()


@pytest.fixture
def state():
    return AircraftState('AC001', 40.6413, -73.7781, 'Parked', 0, '2026-10-08T14:30:00')


def test_crud_and_latest_state(store, state):
    assert store.create(state) == state
    with pytest.raises(sqlite3.IntegrityError):
        store.create(state)
    newer = replace(state, latitude=41, longitude=-74, altitude=500,
                    status='Departing', timestamp='2026-10-08T14:30:05')
    assert update_aircraft_position(store, asdict(newer))
    assert store.get(state.id) == newer
    assert store.list() == [newer]
    assert not update_aircraft_position(store, asdict(state))
    assert not update_aircraft_position(store, asdict(newer))
    assert store.get(state.id) == newer
    assert store.delete(state.id)
    assert store.get(state.id) is None
    assert not store.delete(state.id)
    with pytest.raises(KeyError):
        update_aircraft_position(store, asdict(newer))


@pytest.mark.parametrize('changes', [
    {'latitude': 91}, {'longitude': -181}, {'altitude': -1},
    {'latitude': float('nan')}, {'altitude': float('inf')},
    {'longitude': True}, {'id': ' '}, {'status': 'Flying'},
    {'timestamp': '2026-02-30T12:00:00'}, {'timestamp': '2026-10-08 14:30:00'},
])
def test_invalid_state_does_not_change_database(store, state, changes):
    store.create(state)
    with pytest.raises(ValueError):
        update_aircraft_position(store, {**asdict(state), **changes})
    assert store.get(state.id) == state


def test_tower_messages_are_routed_to_individual_aircraft(store, state):
    store.create(state)
    store.create(replace(state, id='AC002'))
    store.send_message('AC001', 'Request departure', to_tower=True)
    assert store.receive_messages(for_tower=False, aircraft_id='AC001') == []
    assert store.receive_messages(for_tower=True)[0]['content'] == 'Request departure'
    assert store.receive_messages(for_tower=True) == []
    store.send_message('AC001', 'Hold position', to_tower=False)
    store.send_message('AC002', 'Departure approved', to_tower=False)
    assert store.receive_messages(for_tower=False, aircraft_id='AC002')[0]['content'] == 'Departure approved'
    assert store.receive_messages(for_tower=False, aircraft_id='AC001')[0]['content'] == 'Hold position'
    assert store.receive_messages(for_tower=False, aircraft_id='AC001') == []
    with pytest.raises(ValueError):
        store.receive_messages(for_tower=False)
    with pytest.raises(sqlite3.IntegrityError):
        store.send_message('UNKNOWN', 'Hello', to_tower=True)
    with pytest.raises(ValueError):
        store.send_message('AC001', ' ', to_tower=True)


def test_state_survives_reopening(tmp_path, state):
    path = str(tmp_path / 'aircraft.db')
    db = create_database(path)
    AircraftStore(db).create(state)
    db.close()
    db = create_database(path)
    try:
        assert AircraftStore(db).get(state.id) == state
    finally:
        db.close()


@pytest.mark.parametrize('field', ['id', 'latitude', 'longitude', 'status', 'altitude', 'timestamp'])
def test_incomplete_payload_does_not_change_state(store, state, field):
    store.create(state)
    payload = asdict(state)
    del payload[field]
    with pytest.raises(TypeError):
        update_aircraft_position(store, payload)
    assert store.get(state.id) == state


@pytest.mark.parametrize('status', ['Parked', 'Departing', 'Landing'])
@pytest.mark.parametrize('latitude,longitude', [(-90, -180), (90, 180)])
def test_valid_statuses_and_coordinate_boundaries(store, state, status, latitude, longitude):
    valid = replace(state, status=status, latitude=latitude, longitude=longitude)
    store.create(valid)
    assert store.get(valid.id) == valid


def test_updates_do_not_change_other_aircraft(store, state):
    store.create(state)
    other = replace(state, id='AC002')
    store.create(other)
    newer = replace(state, altitude=200, timestamp='2026-10-08T14:30:05')
    assert update_aircraft_position(store, asdict(newer))
    assert store.list() == [newer, other]


def test_tower_filter_preserves_other_aircraft_messages(store, state):
    store.create(state)
    store.create(replace(state, id='AC002'))
    first = store.send_message('AC001', 'First', to_tower=True)
    second = store.send_message('AC001', 'Second', to_tower=True)
    other = store.send_message('AC002', 'Other', to_tower=True)
    messages = store.receive_messages(for_tower=True, aircraft_id='AC001')
    assert [message['id'] for message in messages] == [first, second]
    assert [message['content'] for message in messages] == ['First', 'Second']
    assert [message['id'] for message in store.receive_messages(for_tower=True)] == [other]
    assert store.receive_messages(for_tower=True) == []


def test_deleting_aircraft_removes_only_its_messages(store, state):
    store.create(state)
    other = replace(state, id='AC002')
    store.create(other)
    store.send_message(state.id, 'Request', to_tower=True)
    store.send_message(state.id, 'Reply', to_tower=False)
    remaining = store.send_message(other.id, 'Other request', to_tower=True)
    assert store.delete(state.id)
    assert store.get(other.id) == other
    rows = store.connection.execute('SELECT * FROM aircraft_messages').fetchall()
    assert [row['id'] for row in rows] == [remaining]


@pytest.mark.parametrize('content', ['', '  ', None, 123])
@pytest.mark.parametrize('to_tower', [True, False])
def test_invalid_message_is_not_saved(store, state, content, to_tower):
    store.create(state)
    with pytest.raises(ValueError):
        store.send_message(state.id, content, to_tower=to_tower)
    assert store.receive_messages(for_tower=True) == []
    assert store.receive_messages(for_tower=False, aircraft_id=state.id) == []


def test_messages_survive_reopening_and_are_consumed_once(tmp_path, state):
    path = str(tmp_path / 'messages.db')
    db = create_database(path)
    try:
        store = AircraftStore(db)
        store.create(state)
        store.send_message(state.id, 'Request', to_tower=True)
        store.send_message(state.id, 'Reply', to_tower=False)
    finally:
        db.close()
    db = create_database(path)
    try:
        store = AircraftStore(db)
        assert [m['content'] for m in store.receive_messages(for_tower=True)] == ['Request']
        assert [m['content'] for m in store.receive_messages(for_tower=False, aircraft_id=state.id)] == ['Reply']
    finally:
        db.close()
    db = create_database(path)
    try:
        store = AircraftStore(db)
        assert store.receive_messages(for_tower=True) == []
        assert store.receive_messages(for_tower=False, aircraft_id=state.id) == []
    finally:
        db.close()
