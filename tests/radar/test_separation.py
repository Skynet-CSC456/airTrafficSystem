"""REQ-RAD-009: Radar carries out Tower's unsafe-separation instructions and reports back (REQ-TWR-011)."""

from datetime import datetime, timezone

import pytest

from src.db import Team
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.runway_operations import radar_tick
from src.radar.separation import process_unsafe_separation
from tests.radar.conftest import radar_messages

T = '2026-11-05T12:00:00'


@pytest.fixture(autouse=True)
def aircraft(db):
    store = AircraftStore(db)
    store.create(AircraftState('AC1', 40.64, -73.78, 'Landing', 1000, T))
    store.create(AircraftState('AC2', 40.65, -73.77, 'Airborne', 2000, T, runway_id='RWY 2', delay_hours=3, fuel_percent=40))
    return store


def instruction(db, aircraft_id='AC2', latitude=50.65, longitude=-63.77, observed_at=T, conflict_id='c-1', **extra):
    return db.send_message(Team.TOWER, Team.RADAR, 'Unsafe Separation', message_type='change_position', metadata={
        'schema_version': 1, 'aircraft_id': aircraft_id, 'latitude': latitude, 'longitude': longitude,
        'observed_at': observed_at, 'conflict_id': conflict_id, **extra})


def test_towers_instruction_moves_the_aircraft_and_resolves_the_alert(db, tower):
    # End to end with Tower's real separation check.
    tower.cycle()
    (alert,) = tower.messages_to(Team.COMMAND, 'unsafe_separation')
    (order,) = tower.messages_to(Team.RADAR, 'change_position')
    moving = order.metadata['aircraft_id']

    assert process_unsafe_separation(db) == [moving]
    moved = AircraftStore(db).get(moving)
    assert (moved.latitude, moved.longitude) == (order.metadata['latitude'], order.metadata['longitude'])
    (report,) = radar_messages(db, 'unsafe_separation_resolved')
    assert set(report.metadata) == {'schema_version', 'event_id', 'timestamp', 'conflict_id'}
    assert report.metadata['conflict_id'] == alert.metadata['conflict_id']

    tower.cycle()  # Tower sees safe positions and clears the alert
    (resolved,) = tower.messages_to(Team.COMMAND, 'unsafe_separation_resolved')
    assert resolved.metadata['conflict_id'] == alert.metadata['conflict_id']
    assert tower.messages_to(Team.RADAR, 'tower_r2_rejected') == []


def test_move_keeps_everything_but_the_position(db):
    instruction(db)
    process_unsafe_separation(db)
    moved = AircraftStore(db).get('AC2')
    assert (moved.latitude, moved.longitude) == (50.65, -63.77)
    assert (moved.status, moved.altitude, moved.runway_id, moved.delay_hours, moved.fuel_percent) == ('Airborne', 2000, 'RWY 2', 3, 40)
    assert moved.timestamp > T
    assert db.get_shared_state('aircraft:AC2')['payload']['latitude'] == 50.65


def test_instruction_based_on_an_old_observation_is_discarded(db):
    instruction(db, observed_at='2026-11-05T11:59:00')    # Radar has reported AC2 since
    assert process_unsafe_separation(db) == []
    assert AircraftStore(db).get('AC2').latitude == 40.65
    assert radar_messages(db, 'unsafe_separation_resolved') == []


def test_each_instruction_is_applied_once(db):
    instruction(db)
    assert process_unsafe_separation(db) == ['AC2']
    assert process_unsafe_separation(db) == []
    assert len(radar_messages(db, 'unsafe_separation_resolved')) == 1


def test_move_is_newer_than_a_future_timestamp(db):
    AircraftStore(db).create(AircraftState('LATE', 10.0, 10.0, 'Landing', 900, '2099-01-01T00:00:00'))
    instruction(db, 'LATE', 20.0, 20.0, observed_at='2099-01-01T00:00:00')
    process_unsafe_separation(db, now=datetime(2026, 11, 5, tzinfo=timezone.utc))
    assert AircraftStore(db).get('LATE').timestamp == '2099-01-01T00:00:01'


@pytest.mark.parametrize('fields', [
    {'aircraft_id': 'NOPE'},          # unknown aircraft
    {'latitude': 95.0},               # out of range
    {'latitude': '10'},               # not a number
    {'latitude': True},
    {'longitude': None},              # missing
    {'conflict_id': ''},
    {'aircraft_id': ['AC2']},
])
def test_invalid_instructions_are_skipped_and_not_retried(db, fields):
    instruction(db, **{**dict(aircraft_id='AC2', latitude=10.0, longitude=10.0, conflict_id='c-1'), **fields})
    assert process_unsafe_separation(db) == []
    assert (AircraftStore(db).get('AC2').latitude, AircraftStore(db).get('AC2').longitude) == (40.65, -73.77)
    assert radar_messages(db, 'unsafe_separation_resolved') == []
    instruction(db, conflict_id='c-2')  # a later valid instruction still works
    assert process_unsafe_separation(db) == ['AC2']


def test_other_messages_are_ignored(db):
    db.send_message(Team.COMMAND, Team.RADAR, 'Change position', message_type='change_position',
                    metadata={'aircraft_id': 'AC2', 'latitude': 10.0, 'longitude': 10.0, 'observed_at': T, 'conflict_id': 'c'})
    db.send_message(Team.TOWER, Team.RADAR, 'hello')
    assert process_unsafe_separation(db) == []


def test_radar_tick_carries_out_instructions(db, tower):
    tower.cycle()
    radar_tick(db)
    tower.cycle()
    assert len(tower.messages_to(Team.COMMAND, 'unsafe_separation_resolved')) == 1
