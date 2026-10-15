# REQ-RAD-001

import pytest

from src.db import create_database
from src.radar.aircraft_state import AircraftStateStore

@pytest.fixture
def db():
    database = create_database(":memory:")
    yield database
    database.close()


@pytest.fixture
def store(db):
    return AircraftStateStore(db)

def test_create_new_aircraft_state(store):
    aircraft = store.create_aircraft_state(
        aircraft_id="AAL123",
        latitude=40.6413,
        longitude=-73.7781,
        status="departing",
        altitude=3500,
        timestamp="2026-10-15 10:30:00"
    )

    assert aircraft.aircraft_id == "AAL123"
    assert aircraft.latitude == 40.6413
    assert aircraft.longitude == -73.7781
    assert aircraft.status == "departing"
    assert aircraft.altitude == 3500
    assert aircraft.timestamp == "2026-10-15 10:30:00"

def test_update_existing_aircraft_state(store):
    store.create_aircraft_state(
        aircraft_id="AAL123",
        latitude=40.6413,
        longitude=-73.7781,
        status="departing",
        altitude=3500,
        timestamp="2026-10-15 10:30:00"
    )

    updated_aircraft = store.update_aircraft_state(
        aircraft_id="AAL123",
        latitude=40.7000,
        longitude=-73.7000,
        status="landing",
        altitude=1500,
        timestamp="2026-10-15 10:35:00"
    )

    assert updated_aircraft.aircraft_id == "AAL123"
    assert updated_aircraft.latitude == 40.7000
    assert updated_aircraft.longitude == -73.7000
    assert updated_aircraft.status == "landing"
    assert updated_aircraft.altitude == 1500
    assert updated_aircraft.timestamp == "2026-10-15 10:35:00"


def test_invalid_aircraft_status(store):
    with pytest.raises(ValueError):
        store.create_aircraft_state(
            aircraft_id="AAL123",
            latitude=40.6413,
            longitude=-73.7781,
            status="flying",
            altitude=3500,
            timestamp="2026-10-15 10:30:00"
        )


def test_invalid_timestamp(store):
    with pytest.raises(ValueError):
        store.create_aircraft_state(
            aircraft_id="AAL123",
            latitude=40.6413,
            longitude=-73.7781,
            status="departing",
            altitude=3500,
            timestamp="October 15"
        )

def test_invalid_latitude(store):
    with pytest.raises(ValueError):
        store.create_aircraft_state(
            aircraft_id="AAL123",
            latitude=100.0,
            longitude=-73.7781,
            status="departing",
            altitude=3500,
            timestamp="2026-10-15 10:30:00"
        )

def test_invalid_longitude(store):
    with pytest.raises(ValueError):
        store.create_aircraft_state(
            aircraft_id="AAL123",
            latitude=40.6413,
            longitude=-200.0,
            status="departing",
            altitude=3500,
            timestamp="2026-10-15 10:30:00"
        )

def test_invalid_altitude(store):
    with pytest.raises(ValueError):
        store.create_aircraft_state(
            aircraft_id="AAL123",
            latitude=40.6413,
            longitude=-73.7781,
            status="departing",
            altitude=-100,
            timestamp="2026-10-15 10:30:00"
        )

def test_duplicate_aircraft_id(store):
    store.create_aircraft_state(
        aircraft_id="AAL123",
        latitude=40.6413,
        longitude=-73.7781,
        status="departing",
        altitude=3500,
        timestamp="2026-10-15 10:30:00"
    )

    with pytest.raises(
        ValueError,
        match="Aircraft ID already exists"
    ):
        store.create_aircraft_state(
            aircraft_id="AAL123",
            latitude=40.7000,
            longitude=-73.7000,
            status="landing",
            altitude=1500,
            timestamp="2026-10-15 10:35:00"
        )

def test_update_does_not_create_duplicate(db, store):
    store.create_aircraft_state(
        aircraft_id="AAL123",
        latitude=40.6413,
        longitude=-73.7781,
        status="departing",
        altitude=3500,
        timestamp="2026-10-15 10:30:00"
    )

    store.update_aircraft_state(
        aircraft_id="AAL123",
        latitude=40.7000,
        longitude=-73.7000,
        status="landing",
        altitude=1500,
        timestamp="2026-10-15 10:35:00"
    )

    row = db.connection.execute(
        """
        SELECT COUNT(*)
        FROM aircraft_states
        WHERE aircraft_id = ?
        """,
        ("AAL123",)
    ).fetchone()

    assert row[0] == 1

def test_get_aircraft_state(store):
    store.create_aircraft_state(
        aircraft_id="AAL123",
        latitude=40.6413,
        longitude=-73.7781,
        status="departing",
        altitude=3500,
        timestamp="2026-10-15 10:30:00"
    )

    aircraft = store.get_aircraft_state("AAL123")

    assert aircraft.aircraft_id == "AAL123"
    assert aircraft.latitude == 40.6413
    assert aircraft.longitude == -73.7781
    assert aircraft.status == "departing"
    assert aircraft.altitude == 3500
    assert aircraft.timestamp == "2026-10-15 10:30:00"

def test_get_nonexistent_aircraft(store):
    with pytest.raises(
        ValueError,
        match="Aircraft does not exist"
    ):
        store.get_aircraft_state("UNKNOWN123")