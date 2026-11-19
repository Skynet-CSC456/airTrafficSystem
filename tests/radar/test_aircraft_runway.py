"""REQ-RAD-006: each aircraft records the runway Tower assigned to it."""

from dataclasses import asdict, replace
import sqlite3

import pytest

from src.db import create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.position_update import update_aircraft_position


@pytest.fixture
def db():
    database = create_database(':memory:')
    yield database
    database.close()


@pytest.fixture
def store(db):
    return AircraftStore(db)


@pytest.fixture
def state():
    return AircraftState('AC001', 40.6413, -73.7781, 'Landing', 1500, '2026-10-22T13:30:00')


def test_new_aircraft_has_no_runway(db, store, state):
    store.create(state)
    assert store.get('AC001').runway_id is None
    assert db.get_shared_state('aircraft:AC001')['payload']['runway_id'] is None


def test_assign_runway_is_stored_and_shared(db, store, state):
    store.create(state)
    assigned = store.assign_runway('AC001', 'RWY 1')
    assert assigned == replace(state, runway_id='RWY 1')
    assert store.get('AC001').runway_id == 'RWY 1'
    assert db.get_shared_state('aircraft:AC001')['payload']['runway_id'] == 'RWY 1'


def test_clearing_runway_with_none(db, store, state):
    store.create(state)
    store.assign_runway('AC001', 'RWY 1')
    assert store.assign_runway('AC001', None).runway_id is None
    assert store.get('AC001').runway_id is None
    assert db.get_shared_state('aircraft:AC001')['payload']['runway_id'] is None


def test_position_updates_keep_the_assigned_runway(db, store, state):
    store.create(state)
    store.assign_runway('AC001', 'RWY 2')
    newer = replace(state, altitude=900, timestamp='2026-10-22T13:30:05')
    assert update_aircraft_position(store, asdict(newer))  # payload has runway_id=None
    assert store.get('AC001') == replace(newer, runway_id='RWY 2')
    assert db.get_shared_state('aircraft:AC001')['payload']['runway_id'] == 'RWY 2'
    # Stale updates are still rejected.
    assert not update_aircraft_position(store, asdict(state))


def test_assigning_runway_does_not_change_position_or_timestamp(store, state):
    store.create(state)
    store.assign_runway('AC001', 'RWY 1')
    stored = store.get('AC001')
    assert (stored.latitude, stored.longitude, stored.altitude, stored.timestamp) == (
        state.latitude, state.longitude, state.altitude, state.timestamp)


def test_assign_runway_to_unknown_aircraft_raises(store):
    with pytest.raises(KeyError):
        store.assign_runway('UNKNOWN', 'RWY 1')


@pytest.mark.parametrize('runway_id', ['', '   ', 5, True])
def test_invalid_runway_id_is_rejected_without_change(store, state, runway_id):
    store.create(state)
    store.assign_runway('AC001', 'RWY 1')
    with pytest.raises(ValueError):
        store.assign_runway('AC001', runway_id)
    assert store.get('AC001').runway_id == 'RWY 1'


def test_aircraft_state_rejects_invalid_runway_id(state):
    with pytest.raises(ValueError):
        replace(state, runway_id='')


def test_existing_database_without_runway_column_is_upgraded(tmp_path):
    path = str(tmp_path / 'old.db')
    old = sqlite3.connect(path)
    old.execute("""
        CREATE TABLE aircraft (
            id TEXT PRIMARY KEY NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL,
            status TEXT NOT NULL, altitude REAL NOT NULL, timestamp TEXT NOT NULL
        )
    """)
    old.execute("INSERT INTO aircraft VALUES ('AC009', 40.64, -73.77, 'Landing', 800, '2026-10-22T13:00:00')")
    old.commit()
    old.close()

    db = create_database(path)
    try:
        store = AircraftStore(db)
        assert store.get('AC009').runway_id is None
        assert store.assign_runway('AC009', 'RWY 2').runway_id == 'RWY 2'
    finally:
        db.close()
