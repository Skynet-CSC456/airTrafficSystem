"""REQ-RAD-007: finish an aircraft's runway operation once Tower's runway hold ends."""

from datetime import datetime, timedelta, timezone

import pytest

from src.db import create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.messaging import process_runway_assignments, request_runway
from src.radar.runway_operations import complete_runway_operations, radar_tick
from src.tower.clearance import RUNWAY_HOLD_SECONDS
from src.tower.messaging import assign_runway_to_aircraft

T = '2026-11-05T12:00:00'


@pytest.fixture
def db():
    database = create_database(':memory:')
    store = AircraftStore(database)
    store.create(AircraftState('LAND1', 40.645, -73.77, 'Landing', 600, T))
    store.create(AircraftState('DEP1', 40.64, -73.775, 'Parked', 0, T))
    yield database
    database.close()


def after_hold():
    return datetime.now(timezone.utc) + timedelta(seconds=RUNWAY_HOLD_SECONDS + 1)


def _clear(db, aircraft_id, runway_id, request_type):
    """Radar requests, Tower assigns, Radar records the runway."""
    request_runway(db, aircraft_id, request_type)
    assign_runway_to_aircraft(db, aircraft_id, runway_id)
    process_runway_assignments(db)


def test_landing_aircraft_is_parked_after_the_hold(db):
    _clear(db, 'LAND1', 'RWY 1', 'landing')

    assert complete_runway_operations(db, now=after_hold()) == [('LAND1', 'landed')]
    plane = AircraftStore(db).get('LAND1')
    assert (plane.status, plane.altitude, plane.runway_id) == ('Parked', 0, None)
    assert (plane.latitude, plane.longitude) == (40.645, -73.77)
    assert db.get_shared_state('aircraft:LAND1')['payload']['status'] == 'Parked'


def test_departing_aircraft_is_removed_after_the_hold(db):
    _clear(db, 'DEP1', 'RWY 2', 'departure')

    assert complete_runway_operations(db, now=after_hold()) == [('DEP1', 'departed')]
    assert AircraftStore(db).get('DEP1') is None
    assert db.get_shared_state('aircraft:DEP1') is None


def test_nothing_changes_while_the_runway_is_still_held(db):
    _clear(db, 'LAND1', 'RWY 1', 'landing')
    _clear(db, 'DEP1', 'RWY 2', 'departure')

    assert complete_runway_operations(db) == []
    assert AircraftStore(db).get('LAND1').status == 'Landing'
    assert AircraftStore(db).get('DEP1') is not None


def test_each_operation_completes_only_once(db):
    _clear(db, 'LAND1', 'RWY 1', 'landing')
    complete_runway_operations(db, now=after_hold())
    assert complete_runway_operations(db, now=after_hold() + timedelta(seconds=5)) == []


def test_aircraft_without_a_recorded_runway_is_unchanged(db):
    # Tower assigned a runway, but Radar has not recorded it yet.
    request_runway(db, 'LAND1', 'landing')
    assign_runway_to_aircraft(db, 'LAND1', 'RWY 1')
    assert complete_runway_operations(db, now=after_hold()) == []
    assert AircraftStore(db).get('LAND1').status == 'Landing'


def test_without_a_request_the_status_decides(db):
    # Tower assigns airborne aircraft without any Radar request (REQ-TWR-006).
    AircraftStore(db).create(AircraftState('CLIMB1', 40.65, -73.78, 'Departing', 300, T))
    assign_runway_to_aircraft(db, 'CLIMB1', 'RWY 1')
    assign_runway_to_aircraft(db, 'LAND1', 'RWY 2')
    process_runway_assignments(db)

    assert sorted(complete_runway_operations(db, now=after_hold())) == [('CLIMB1', 'departed'), ('LAND1', 'landed')]


def test_landing_update_is_newer_than_a_future_timestamp(db):
    AircraftStore(db).create(AircraftState('LATE1', 40.65, -73.78, 'Landing', 300, '2099-01-01T00:00:00'))
    _clear(db, 'LATE1', 'RWY 1', 'landing')
    complete_runway_operations(db, now=after_hold())
    plane = AircraftStore(db).get('LATE1')
    assert (plane.status, plane.timestamp) == ('Parked', '2099-01-01T00:00:01')


def test_radar_tick_records_assignments_first(db):
    request_runway(db, 'LAND1', 'landing')
    assign_runway_to_aircraft(db, 'LAND1', 'RWY 1')

    assert radar_tick(db) == []  # hold still active: runway recorded, nothing finished yet
    assert AircraftStore(db).get('LAND1').runway_id == 'RWY 1'
