"""REQ-RAD-007: Radar carries out a cleared landing or departure and reports it to Tower (REQ-TWR-012)."""

from datetime import datetime, timedelta, timezone

import pytest

from src.db import Team, create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.messaging import get_request, process_tower_decisions, request_runway
from src.radar.runway_operations import (
    PARKING_STANDS,
    RUNWAY_OPERATION_SECONDS,
    complete_runway_operations,
    next_parking_stand,
    radar_tick,
)
from tests.radar.conftest import radar_messages

T = '2026-11-05T12:00:00'


@pytest.fixture(autouse=True)
def aircraft(db):
    store = AircraftStore(db)
    store.create(AircraftState('LAND1', 40.645, -73.77, 'Landing', 600, T))
    store.create(AircraftState('DEP1', 40.64, -73.775, 'Parked', 0, T))
    return store


def after_operation():
    return datetime.now(timezone.utc) + timedelta(seconds=RUNWAY_OPERATION_SECONDS + 1)


def _clear(db, tower, aircraft_id, request_type):
    """Radar requests, Tower clears, Radar records the runway."""
    request_runway(db, aircraft_id, request_type)
    tower.cycle()
    process_tower_decisions(db)
    assert get_request(db, aircraft_id)['status'] == 'cleared'


def test_operation_takes_30_seconds(db, tower2):
    assert RUNWAY_OPERATION_SECONDS == 30
    _clear(db, tower2, 'LAND1', 'landing')
    cleared_at = datetime.fromisoformat(get_request(db, 'LAND1')['cleared_at'])
    assert complete_runway_operations(db, now=cleared_at + timedelta(seconds=29)) == []
    assert complete_runway_operations(db, now=cleared_at + timedelta(seconds=30)) == [('LAND1', 'landed')]


def test_landing_parks_at_a_stand_and_reports_operation_finished(db, tower2, aircraft):
    _clear(db, tower2, 'LAND1', 'landing')
    assert complete_runway_operations(db, now=after_operation()) == [('LAND1', 'landed')]
    plane = aircraft.get('LAND1')
    assert (plane.status, plane.altitude, plane.runway_id) == ('Parked', 0, None)
    assert (plane.latitude, plane.longitude) == PARKING_STANDS[0]
    (message,) = radar_messages(db, 'operation_finished')
    request_id = get_request(db, 'LAND1')['request_id']
    assert set(message.metadata) == {'schema_version', 'event_id', 'timestamp', 'request_id', 'aircraft_id', 'operation'}
    assert (message.metadata['request_id'], message.metadata['aircraft_id'], message.metadata['operation']) == (request_id, 'LAND1', 'landing')


def test_tower_completes_the_landing_after_its_30_second_hold(db, tower2, aircraft):
    _clear(db, tower2, 'LAND1', 'landing')
    complete_runway_operations(db, now=after_operation())
    tower2.cycle()  # Tower's clock has not moved: confirmation recorded, runway still held
    process_tower_decisions(db)
    assert get_request(db, 'LAND1')['status'] == 'cleared'
    tower2.advance(30)
    tower2.cycle()
    process_tower_decisions(db)
    assert get_request(db, 'LAND1')['status'] == 'completed'
    assert aircraft.get('LAND1').status == 'Parked'          # landed aircraft stay tracked
    assert tower2.messages_to(Team.RADAR, 'tower_r2_rejected') == []


def test_departure_takes_off_then_leaves_the_airspace(db, tower2, aircraft):
    _clear(db, tower2, 'DEP1', 'departure')
    assert complete_runway_operations(db, now=after_operation()) == [('DEP1', 'departed')]
    plane = aircraft.get('DEP1')
    assert (plane.status, plane.runway_id) == ('Departing', None) and plane.altitude > 0
    (message,) = radar_messages(db, 'operation_finished')
    assert message.metadata['operation'] == 'departure'
    tower2.advance(30)
    tower2.cycle()  # Tower confirms the aircraft is airborne and completes the departure
    process_tower_decisions(db)
    assert aircraft.get('DEP1') is None                       # out of Radar's airspace
    assert db.get_shared_state('aircraft:DEP1') is None
    assert tower2.messages_to(Team.RADAR, 'tower_r2_rejected') == []


def test_completion_frees_the_runway_for_the_next_aircraft(db, tower, aircraft):
    # One runway: DEP1 departs, then the queued landing is cleared.
    _clear(db, tower, 'DEP1', 'departure')
    request_runway(db, 'LAND1', 'landing')
    tower.cycle()
    process_tower_decisions(db)
    assert get_request(db, 'LAND1')['status'] == 'queued'
    complete_runway_operations(db, now=after_operation())
    tower.advance(30)
    tower.cycle()
    process_tower_decisions(db)
    assert (get_request(db, 'LAND1')['status'], aircraft.get('LAND1').runway_id) == ('cleared', 'RWY 1')


def test_each_operation_is_finished_and_reported_once(db, tower2):
    _clear(db, tower2, 'LAND1', 'landing')
    complete_runway_operations(db, now=after_operation())
    assert complete_runway_operations(db, now=after_operation() + timedelta(seconds=5)) == []
    assert len(radar_messages(db, 'operation_finished')) == 1


def test_two_radar_processes_finish_and_report_once(tmp_path):
    from tests.radar.conftest import Tower
    path = str(tmp_path / 'atc.db')
    first, second = create_database(path), create_database(path)
    try:
        tower = Tower(first, ['RWY 1'])
        AircraftStore(first).create(AircraftState('LAND1', 40.645, -73.77, 'Landing', 600, T))
        _clear(first, tower, 'LAND1', 'landing')
        later = after_operation()
        assert complete_runway_operations(first, now=later) == [('LAND1', 'landed')]
        assert complete_runway_operations(second, now=later + timedelta(seconds=3)) == []
        assert len(radar_messages(first, 'operation_finished')) == 1
    finally:
        first.close()
        second.close()


def test_aircraft_without_a_clearance_is_unchanged(db, tower2, aircraft):
    request_runway(db, 'LAND1', 'landing')        # Tower has not answered yet
    assert complete_runway_operations(db, now=after_operation()) == []
    aircraft.assign_runway('DEP1', 'RWY 2')       # a runway with no clearance record
    assert complete_runway_operations(db, now=after_operation()) == []
    assert aircraft.get('LAND1').status == 'Landing'


def test_landing_update_is_newer_than_a_future_timestamp(db, tower2, aircraft):
    aircraft.create(AircraftState('LATE1', 40.65, -73.78, 'Landing', 300, '2099-01-01T00:00:00'))
    _clear(db, tower2, 'LATE1', 'landing')
    complete_runway_operations(db, now=after_operation())
    assert (aircraft.get('LATE1').status, aircraft.get('LATE1').timestamp) == ('Parked', '2099-01-01T00:00:01')


def test_each_landed_aircraft_gets_its_own_stand(db, tower2, aircraft):
    aircraft.create(AircraftState('LAND2', 40.65, -73.78, 'Landing', 400, T))
    _clear(db, tower2, 'LAND1', 'landing')
    _clear(db, tower2, 'LAND2', 'landing')
    complete_runway_operations(db, now=after_operation())
    stands = {(aircraft.get(i).latitude, aircraft.get(i).longitude) for i in ('LAND1', 'LAND2')}
    assert stands == set(PARKING_STANDS[:2])


def test_next_parking_stand_skips_taken_stands_and_shares_the_last_when_full():
    def plane(aircraft_id, stand):
        return AircraftState(aircraft_id, *stand, 'Parked', 0, T)
    assert next_parking_stand([plane('A', PARKING_STANDS[0])], 'NEW') == PARKING_STANDS[1]
    assert next_parking_stand([plane('A', PARKING_STANDS[0])], 'A') == PARKING_STANDS[0]  # its own spot is free to it
    full = [plane(str(i), stand) for i, stand in enumerate(PARKING_STANDS)]
    assert next_parking_stand(full, 'NEW') == PARKING_STANDS[-1]


def test_radar_tick_records_decisions_then_finishes_due_operations(db, tower2, aircraft):
    request_runway(db, 'LAND1', 'landing')
    tower2.cycle()
    assert radar_tick(db) == []                   # clearance recorded, operation under way
    assert aircraft.get('LAND1').runway_id == 'RWY 1'
