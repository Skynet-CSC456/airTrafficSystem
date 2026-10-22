"""Tests for the seed_aircraft.py and clear_aircraft.py helper scripts."""

import pytest

from clear_aircraft import clear_aircraft
from seed_aircraft import SEED_AIRCRAFT, seed_aircraft
from src.db import create_database
from src.radar.aircraft import AircraftStore


@pytest.fixture
def db():
    database = create_database(":memory:")
    yield database
    database.close()


def test_seed_creates_airplanes_and_is_repeatable(db):
    store = AircraftStore(db)
    seed_aircraft(store)
    seed_aircraft(store)

    assert [a.id for a in store.list()] == sorted(row[0] for row in SEED_AIRCRAFT)
    assert any(a.altitude > 0 for a in store.list())
    assert len(db.list_shared_state(category="aircraft")) == len(SEED_AIRCRAFT)


def test_clear_removes_airplanes_state_and_their_messages(db):
    store = AircraftStore(db)
    seed_aircraft(store)
    store.send_message("AC001", "Requesting departure", to_tower=True)

    assert clear_aircraft(store) == len(SEED_AIRCRAFT)
    assert store.list() == []
    assert db.list_shared_state(category="aircraft") == []
    assert db.connection.execute("SELECT COUNT(*) FROM aircraft_messages").fetchone()[0] == 0


def test_clear_on_empty_table(db):
    assert clear_aircraft(AircraftStore(db)) == 0
