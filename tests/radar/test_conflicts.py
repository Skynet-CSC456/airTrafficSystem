"""REQ-RAD-008: Radar detects conflicts from delay and fuel and signals Tower every second until resolved."""

import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.db import Team, create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.conflicts import (
    LOW_FUEL_PERCENT,
    conflict_key,
    detect_conflicts,
    process_conflict_resolutions,
    send_conflict_signals,
)
from src.radar.messaging import get_request, process_tower_decisions, request_runway
from src.radar.runway_operations import radar_tick
from tests.radar.conftest import Tower, radar_messages

T = '2026-11-05T12:00:00'
NOW = datetime.now(timezone.utc)


def plane(aircraft_id, latitude=40.0, delay_hours=None, fuel_percent=None, **changes):
    return AircraftState(aircraft_id, latitude, -73.78, changes.pop('status', 'Landing'), changes.pop('altitude', 1000), T,
                         delay_hours=delay_hours, fuel_percent=fuel_percent, **changes)


def queue_up(db, tower, *planes):
    """AC0 takes the only runway; the given aircraft queue behind it in order (10+ degrees apart, no separation alerts)."""
    store = AircraftStore(db)
    store.create(plane('AC0', latitude=-60))
    request_runway(db, 'AC0', 'landing')
    for index, state in enumerate(planes, start=1):
        store.create(replace(state, latitude=-60 + index * 20))
        request_runway(db, state.id, 'landing')
    tower.cycle()
    process_tower_decisions(db)


def conflicts_sent(db):
    return radar_messages(db, 'conflict')


# --- detection --------------------------------------------------------------------------------

@pytest.mark.parametrize('delay, fuel, expected', [
    (None, None, []),
    (0, 100, []),
    (1.9, None, []),
    (2, None, ['delay_2h']),
    (3.99, None, ['delay_2h']),
    (4, None, ['delay_4h']),           # one lateness conflict that escalates, not both
    (None, LOW_FUEL_PERCENT, []),
    (None, LOW_FUEL_PERCENT - 0.1, ['low_fuel']),
    (5, 10, ['delay_4h', 'low_fuel']),
])
def test_detect_conflicts(delay, fuel, expected):
    assert detect_conflicts(plane('A', delay_hours=delay, fuel_percent=fuel)) == expected


# --- aircraft data ------------------------------------------------------------------------------

def test_delay_and_fuel_are_stored_and_published(db):
    store = AircraftStore(db)
    store.create(plane('AC1', delay_hours=2.5, fuel_percent=40))
    assert (store.get('AC1').delay_hours, store.get('AC1').fuel_percent) == (2.5, 40)
    payload = db.get_shared_state('aircraft:AC1')['payload']
    assert (payload['delay_hours'], payload['fuel_percent']) == (2.5, 40)


def test_updates_without_delay_or_fuel_keep_the_stored_values(db):
    store = AircraftStore(db)
    store.create(plane('AC1', delay_hours=3, fuel_percent=20))
    store.update(replace(store.get('AC1'), altitude=500, delay_hours=None, fuel_percent=None, timestamp='2026-11-05T12:00:05'))
    assert (store.get('AC1').altitude, store.get('AC1').delay_hours, store.get('AC1').fuel_percent) == (500, 3, 20)
    store.update(replace(store.get('AC1'), delay_hours=0, timestamp='2026-11-05T12:00:06'))
    assert store.get('AC1').delay_hours == 0


@pytest.mark.parametrize('changes', [{'delay_hours': -1}, {'fuel_percent': 101}, {'fuel_percent': -5}, {'delay_hours': True}, {'fuel_percent': float('nan')}])
def test_invalid_delay_or_fuel_is_rejected(changes):
    with pytest.raises(ValueError):
        plane('AC1', **changes)


def test_older_database_gains_the_new_columns():
    database = create_database(':memory:')
    try:
        database.connection.execute("""CREATE TABLE aircraft (id TEXT PRIMARY KEY NOT NULL, latitude REAL NOT NULL,
            longitude REAL NOT NULL, status TEXT NOT NULL, altitude REAL NOT NULL, timestamp TEXT NOT NULL)""")
        database.connection.execute("INSERT INTO aircraft VALUES ('OLD1', 40.6, -73.7, 'Landing', 500, ?)", (T,))
        store = AircraftStore(database)
        assert (store.get('OLD1').runway_id, store.get('OLD1').delay_hours, store.get('OLD1').fuel_percent) == (None, None, None)
    finally:
        database.close()


# --- signalling ---------------------------------------------------------------------------------

def test_conflicts_are_only_signalled_while_the_landing_is_queued(db, tower):
    store = AircraftStore(db)
    store.create(plane('AC1', fuel_percent=10))
    assert send_conflict_signals(db, now=NOW) == []           # no request yet
    request_runway(db, 'AC1', 'landing')
    assert send_conflict_signals(db, now=NOW) == []           # requested, but Tower has not queued it
    tower.cycle()
    process_tower_decisions(db)
    assert get_request(db, 'AC1')['status'] == 'cleared'      # free runway: cleared, never queued
    assert send_conflict_signals(db, now=NOW) == []


def test_conflict_message_matches_towers_contract(db, tower):
    queue_up(db, tower, plane('AC1', fuel_percent=10))
    (message_id,) = send_conflict_signals(db, now=NOW)
    (message,) = conflicts_sent(db)
    assert message.id == message_id
    metadata = message.metadata
    assert set(metadata) == {'schema_version', 'event_id', 'timestamp', 'request_id', 'aircraft_id', 'reason', 'emergency'}
    assert (metadata['aircraft_id'], metadata['reason'], metadata['emergency']) == ('AC1', 'low_fuel', False)
    assert metadata['request_id'] == get_request(db, 'AC1')['request_id']


def test_conflict_is_resent_every_second_with_the_same_event_id_until_resolved(db, tower):
    queue_up(db, tower, plane('AC1'), plane('AC2', delay_hours=2))
    assert len(send_conflict_signals(db, now=NOW)) == 1
    assert send_conflict_signals(db, now=NOW + timedelta(milliseconds=500)) == []   # not yet a second
    assert len(send_conflict_signals(db, now=NOW + timedelta(seconds=1))) == 1
    assert len(send_conflict_signals(db, now=NOW + timedelta(seconds=2))) == 1
    assert len({m.metadata['event_id'] for m in conflicts_sent(db)}) == 1

    tower.cycle()  # Tower applies it once despite the repeats
    assert tower.queue() == ['AC2', 'AC1']                                          # 2 hours late: +1
    assert process_conflict_resolutions(db) == [('AC2', 'delay_2h')]
    assert send_conflict_signals(db, now=NOW + timedelta(seconds=3)) == []          # stopped
    record = db.get_shared_state(conflict_key('AC2', 'delay_2h'))['payload']
    assert (record['status'], record['messages_sent']) == ('resolved', 3)
    assert tower.messages_to(Team.RADAR, 'tower_r2_rejected') == []


def test_low_fuel_moves_three_places_and_command_is_told(db, tower):
    queue_up(db, tower, plane('AC1'), plane('AC2'), plane('AC3'), plane('AC4', fuel_percent=10))
    assert tower.queue() == ['AC1', 'AC2', 'AC3', 'AC4']
    send_conflict_signals(db, now=NOW)
    tower.cycle()
    assert tower.queue() == ['AC4', 'AC1', 'AC2', 'AC3']
    assert [m.message_type for m in tower.messages_to(Team.COMMAND, 'conflict')] == ['conflict']
    assert len(tower.messages_to(Team.COMMAND, 'conflict_resolved')) == 1


def test_lateness_escalates_from_2_to_4_hours(db, tower):
    store = AircraftStore(db)
    queue_up(db, tower, plane('AC1'), plane('AC2'), plane('AC3', delay_hours=2))
    send_conflict_signals(db, now=NOW)
    tower.cycle()
    process_conflict_resolutions(db)
    assert tower.queue() == ['AC1', 'AC3', 'AC2']                                   # +1
    store.update(replace(store.get('AC3'), delay_hours=4.5, timestamp='2026-11-05T12:00:05'))
    send_conflict_signals(db, now=NOW + timedelta(seconds=5))
    tower.cycle()
    assert tower.queue() == ['AC3', 'AC1', 'AC2']                                   # +1 more: +2 in total
    assert [m.metadata['reason'] for m in conflicts_sent(db)] == ['delay_2h', 'delay_4h']
    assert len({m.metadata['event_id'] for m in conflicts_sent(db)}) == 2           # a stronger reason is a new event
    assert db.get_shared_state(conflict_key('AC3', 'delay_2h')) is None


def test_resolved_conflict_is_raised_again_only_after_it_clears_and_returns(db, tower):
    store = AircraftStore(db)
    queue_up(db, tower, plane('AC1', fuel_percent=10))
    send_conflict_signals(db, now=NOW)
    tower.cycle()
    process_conflict_resolutions(db)
    assert send_conflict_signals(db, now=NOW + timedelta(seconds=5)) == []          # still low, already resolved
    store.update(replace(store.get('AC1'), fuel_percent=50, timestamp='2026-11-05T12:00:06'))
    send_conflict_signals(db, now=NOW + timedelta(seconds=6))
    assert db.get_shared_state(conflict_key('AC1', 'low_fuel')) is None            # cleared: forgotten
    store.update(replace(store.get('AC1'), fuel_percent=5, timestamp='2026-11-05T12:00:07'))
    assert len(send_conflict_signals(db, now=NOW + timedelta(seconds=7))) == 1


def test_rejected_conflict_stops_being_sent(db, tower):
    queue_up(db, tower, plane('AC1', fuel_percent=10))
    send_conflict_signals(db, now=NOW)
    # Tower rejects one of the conflict's messages (e.g. the request was cleared in the meantime).
    source = conflicts_sent(db)[0].id
    db.send_message(Team.TOWER, Team.RADAR, 'Rejected Tower R2 event', message_type='tower_r2_rejected',
                    metadata={'schema_version': 1, 'source_message_id': source, 'reason': 'Conflict priority requires a currently queued request'})
    assert process_conflict_resolutions(db) == [('AC1', 'low_fuel')]
    record = db.get_shared_state(conflict_key('AC1', 'low_fuel'))['payload']
    assert record['status'] == 'rejected' and 'queued' in record['rejection']
    assert send_conflict_signals(db, now=NOW + timedelta(seconds=5)) == []


def test_signalling_stops_once_the_landing_is_cleared(db, tower):
    store = AircraftStore(db)
    queue_up(db, tower, plane('AC1', delay_hours=5))
    send_conflict_signals(db, now=NOW)
    db.upsert_shared_state('request:AC1', {**get_request(db, 'AC1'), 'status': 'cleared'}, category='request')
    assert send_conflict_signals(db, now=NOW + timedelta(seconds=1)) == []
    assert db.list_shared_state(category='conflict') == []


def test_several_radar_processes_do_not_multiply_messages(tmp_path):
    path = str(tmp_path / 'atc.db')
    first, second = create_database(path), create_database(path)
    try:
        tower = Tower(first, ['RWY 1'])
        queue_up(first, tower, plane('AC1', fuel_percent=10))
        assert len(send_conflict_signals(first, now=NOW)) == 1
        assert send_conflict_signals(second, now=NOW + timedelta(milliseconds=300)) == []
        assert len(send_conflict_signals(second, now=NOW + timedelta(seconds=1))) == 1
    finally:
        first.close()
        second.close()


def test_unrelated_or_malformed_replies_are_ignored(db, tower):
    queue_up(db, tower, plane('AC1', fuel_percent=10))
    send_conflict_signals(db, now=NOW)
    for message_type, metadata in (
        ('conflict_resolved', {'event_id': 'someone-else', 'reason': 'low_fuel'}),
        ('conflict_resolved', {'reason': 'low_fuel'}),
        ('tower_r2_rejected', {'source_message_id': 999_999, 'reason': 'x'}),
        ('tower_r2_rejected', {'reason': 'x'}),
    ):
        db.send_message(Team.TOWER, Team.RADAR, 'x', message_type=message_type, metadata=metadata)
    db.send_message(Team.COMMAND, Team.RADAR, 'x', message_type='conflict_resolved',
                    metadata={'event_id': conflicts_sent(db)[0].metadata['event_id']})
    assert process_conflict_resolutions(db) == []
    assert db.get_shared_state(conflict_key('AC1', 'low_fuel'))['payload']['status'] == 'active'


def test_radar_tick_signals_and_records_resolutions(db, tower):
    queue_up(db, tower, plane('AC1', delay_hours=4))
    radar_tick(db)
    assert len(conflicts_sent(db)) == 1
    tower.cycle()
    radar_tick(db)
    assert db.get_shared_state(conflict_key('AC1', 'delay_4h'))['payload']['status'] == 'resolved'


def test_many_concurrent_aircraft_are_signalled_without_degradation(db):
    # "Handles concurrent aircraft without degradation": one pass covers every aircraft well within its 1-second cycle.
    store = AircraftStore(db)
    for index in range(300):
        aircraft_id = f'AC{index:03d}'
        store.create(plane(aircraft_id, latitude=-80 + index * 0.5, delay_hours=index % 6, fuel_percent=10 + index % 40))
        db.upsert_shared_state(f'request:{aircraft_id}', {'aircraft_id': aircraft_id, 'request_id': f'r-{aircraft_id}',
                                                          'status': 'queued', 'request_type': 'landing'}, category='request')
    expected = sum(len(detect_conflicts(aircraft)) for aircraft in store.list())
    started = time.perf_counter()
    sent = send_conflict_signals(db, now=NOW)
    elapsed = time.perf_counter() - started
    assert len(sent) == expected > 300
    assert elapsed < 1.0
    started = time.perf_counter()
    assert len(send_conflict_signals(db, now=NOW + timedelta(seconds=1))) == expected  # the next second, all again
    assert time.perf_counter() - started < 1.0
