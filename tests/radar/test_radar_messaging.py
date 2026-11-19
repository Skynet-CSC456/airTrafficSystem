"""REQ-RAD-006: Radar requests runways with Tower's version-1 protocol and records Tower's answers."""

import builtins
from datetime import datetime

import pytest

import messaging_demo
from src.db import Team, create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.messaging import configured_airport, get_request, process_tower_decisions, request_runway
from src.tower.messaging import assign_runway_to_aircraft
from tests.radar.conftest import AIRPORT, radar_messages

T = '2026-10-22T13:30:00'


@pytest.fixture(autouse=True)
def aircraft(db):
    store = AircraftStore(db)
    store.create(AircraftState('AAL123', 40.6450, -73.7735, 'Landing', 1500, T))
    store.create(AircraftState('DAL456', 40.6380, -73.7700, 'Parked', 0, T))
    return store


# --- requesting -----------------------------------------------------------------------------

def test_request_uses_towers_version_1_envelope(db, tower):
    message_id = request_runway(db, 'AAL123', 'landing')
    (message,) = radar_messages(db, 'runway_request')
    assert message.id == message_id
    assert (message.sender, message.recipient) == (Team.RADAR, Team.TOWER)
    metadata = message.metadata
    assert set(metadata) == {'schema_version', 'request_id', 'aircraft_id', 'airport_id', 'operation', 'timestamp'}
    assert (metadata['schema_version'], metadata['aircraft_id'], metadata['airport_id'], metadata['operation']) == (1, 'AAL123', AIRPORT, 'landing')
    assert datetime.fromisoformat(metadata['timestamp']).utcoffset().total_seconds() == 0  # timezone-aware UTC
    record = get_request(db, 'AAL123')
    assert (record['status'], record['request_id'], record['request_type']) == ('requested', metadata['request_id'], 'landing')


def test_each_request_gets_a_new_request_id(db, tower):
    request_runway(db, 'AAL123', 'landing')
    request_runway(db, 'AAL123', 'landing')
    first, second = radar_messages(db, 'runway_request')
    assert first.metadata['request_id'] != second.metadata['request_id']


@pytest.mark.parametrize('aircraft_id, expected', [('AAL123', 'landing'), ('DAL456', 'departure')])
def test_request_type_follows_aircraft_status_when_omitted(db, tower, aircraft_id, expected):
    request_runway(db, aircraft_id)
    assert radar_messages(db, 'runway_request')[0].metadata['operation'] == expected


def test_airborne_aircraft_requests_landing_when_type_omitted(db, tower, aircraft):
    aircraft.create(AircraftState('JBU789', 40.6454, -73.7748, 'Airborne', 2000, T))
    request_runway(db, 'JBU789')
    assert radar_messages(db, 'runway_request')[0].metadata['operation'] == 'landing'


def test_request_type_is_case_insensitive(db, tower):
    request_runway(db, 'AAL123', 'LANDING')
    assert radar_messages(db, 'runway_request')[0].metadata['operation'] == 'landing'


@pytest.mark.parametrize('aircraft_id, request_type, error', [
    ('', 'landing', 'cannot be empty'),
    ('NOPE', 'landing', 'Unknown aircraft'),
    ('AAL123', 'hover', 'landing or departure'),
])
def test_invalid_requests_send_and_store_nothing(db, tower, aircraft_id, request_type, error):
    with pytest.raises(ValueError, match=error):
        request_runway(db, aircraft_id, request_type)
    assert radar_messages(db, 'runway_request') == []
    assert db.list_shared_state(category='request') == []


def test_airport_must_be_configured_by_tower(db):
    with pytest.raises(ValueError, match='not configured an airport'):
        request_runway(db, 'AAL123', 'landing')
    assert db.receive_messages(Team.TOWER) == []


def test_several_airports_need_an_explicit_choice(db, tower):
    tower.service.airports.create_airport('LGA', 15000, 40.7769, -73.8740, ['RWY 4'])
    with pytest.raises(ValueError, match='Several airports'):
        configured_airport(db)
    request_runway(db, 'AAL123', 'landing', airport_id='LGA')
    assert radar_messages(db, 'runway_request')[0].metadata['airport_id'] == 'LGA'


def test_request_is_published_for_the_dashboard(db, tower):
    from src.command.dashboard import build_dashboard_snapshot
    request_runway(db, 'AAL123', 'landing')
    assert any(item.get('aircraft_id') == 'AAL123' and item.get('status') == 'requested'
               for item in build_dashboard_snapshot(db)['decisions'])


# --- Tower's answers --------------------------------------------------------------------------

def test_cleared_request_records_the_runway(db, tower, aircraft):
    request_runway(db, 'AAL123', 'landing')
    tower.cycle()
    assert process_tower_decisions(db) == [('AAL123', 'cleared')]
    assert aircraft.get('AAL123').runway_id == 'RWY 1'
    record = get_request(db, 'AAL123')
    assert (record['status'], record['runway_id'], record['finished']) == ('cleared', 'RWY 1', False)
    assert db.get_shared_state('aircraft:AAL123')['payload']['runway_id'] == 'RWY 1'


def test_queued_request_records_its_position_then_promotion(db, tower, aircraft):
    aircraft.create(AircraftState('UAL789', 40.66, -73.80, 'Landing', 900, T))
    request_runway(db, 'AAL123', 'landing')
    request_runway(db, 'UAL789', 'landing')
    tower.cycle()
    process_tower_decisions(db)
    record = get_request(db, 'UAL789')
    assert (record['status'], record['queue_position']) == ('queued', 1)
    assert aircraft.get('UAL789').runway_id is None


def test_rejected_request_records_the_reason(db, tower, aircraft):
    request_runway(db, 'DAL456', 'landing')  # a parked aircraft cannot land
    tower.cycle()
    process_tower_decisions(db)
    record = get_request(db, 'DAL456')
    assert record['status'] == 'rejected' and record['reason']
    assert aircraft.get('DAL456').runway_id is None


def test_departure_is_cleared_for_a_parked_aircraft(db, tower, aircraft):
    request_runway(db, 'DAL456', 'departure')
    tower.cycle()
    process_tower_decisions(db)
    assert (get_request(db, 'DAL456')['status'], aircraft.get('DAL456').runway_id) == ('cleared', 'RWY 1')


def test_towers_menu_assignment_is_recorded_without_a_radar_request(db, tower, aircraft):
    # REQ-TWR-006: the legacy menu still sends an assign_runway function message.
    assign_runway_to_aircraft(db, 'AAL123', 'RWY 1')
    assert process_tower_decisions(db) == [('AAL123', 'cleared')]
    record = get_request(db, 'AAL123')
    assert (record['status'], record['runway_id'], record['request_type']) == ('cleared', 'RWY 1', 'landing')


def test_decisions_are_applied_once_and_inbox_stays_unread(db, tower):
    request_runway(db, 'AAL123', 'landing')
    tower.cycle()
    process_tower_decisions(db)
    assert process_tower_decisions(db) == []
    assert db.receive_messages(Team.RADAR, unread_only=True)  # people still see them as new


def test_answers_to_an_older_request_are_ignored(db, aircraft):
    request_runway_id = 'radar-AAL123-landing-old'
    db.upsert_shared_state('airport:JFK', {'airport_id': 'JFK'}, category='airport')
    request_runway(db, 'AAL123', 'landing')
    db.send_message(Team.TOWER, Team.RADAR, 'landing rejected', message_type='runway_response', metadata={
        'request_id': request_runway_id, 'aircraft_id': 'AAL123', 'operation': 'landing', 'outcome': 'rejected', 'reason': 'x'})
    assert process_tower_decisions(db) == []
    assert get_request(db, 'AAL123')['status'] == 'requested'


@pytest.mark.parametrize('message_type, metadata', [
    ('runway_response', {'request_id': 'r1', 'aircraft_id': 'GHOST', 'operation': 'landing', 'outcome': 'cleared', 'runway_id': 'RWY 1'}),
    ('runway_response', {'request_id': 'r1', 'aircraft_id': 'AAL123', 'operation': 'landing', 'outcome': 'cleared'}),  # no runway
    ('runway_response', {'request_id': 'r1', 'aircraft_id': 'AAL123', 'operation': 'landing', 'outcome': 'teleported'}),
    ('runway_response', {'aircraft_id': 'AAL123', 'operation': 'landing', 'outcome': 'cleared', 'runway_id': 'RWY 1'}),
    ('function', {'function': 'hold', 'request_id': 'r1', 'aircraft_id': 'AAL123', 'operation': 'landing', 'runway_id': 'RWY 1'}),
    ('text', {}),
])
def test_unusable_messages_are_skipped_and_not_retried(db, aircraft, message_type, metadata):
    db.send_message(Team.TOWER, Team.RADAR, 'x', message_type=message_type, metadata=metadata or None)
    assert process_tower_decisions(db) == []
    assert aircraft.get('AAL123').runway_id is None


# --- demo -------------------------------------------------------------------------------------

def _feed_inputs(monkeypatch, answers):
    answers = iter(answers)
    monkeypatch.setattr(builtins, 'input', lambda prompt='': next(answers))


def test_demo_request_runway_option_sends_a_request(db, tower, monkeypatch, capsys):
    # Radar menu option 5 -> airplane 2 (DAL456) -> request 2 (departure)
    _feed_inputs(monkeypatch, ['2', '2'])
    messaging_demo.request_runway_function(db)
    assert 'Runway request sent to tower' in capsys.readouterr().out
    (message,) = radar_messages(db, 'runway_request')
    assert (message.metadata['aircraft_id'], message.metadata['operation']) == ('DAL456', 'departure')


def test_demo_explains_when_tower_has_no_airport(db, monkeypatch, capsys):
    _feed_inputs(monkeypatch, ['1', '1'])
    messaging_demo.request_runway_function(db)
    assert 'Could not request a runway: Tower has not configured an airport yet' in capsys.readouterr().out


def test_demo_reports_when_radar_tracks_no_aircraft(monkeypatch, capsys):
    empty = create_database(':memory:')
    try:
        messaging_demo.request_runway_function(empty)
        assert 'Radar is not tracking any airplanes' in capsys.readouterr().out
        assert empty.receive_messages(Team.TOWER) == []
    finally:
        empty.close()


def test_demo_radar_send_menu_still_sends_text(db, monkeypatch):
    _feed_inputs(monkeypatch, ['tower', 'hello tower'])
    messaging_demo.send_messages(db, Team.RADAR)
    (message,) = db.receive_messages(Team.TOWER)
    assert (message.message_type, message.content) == ('text', 'hello tower')
