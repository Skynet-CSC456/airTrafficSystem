"""REQ-RAD-003 and REQ-RAD-004: ingestion, CRUD, and aircraft messaging."""

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
