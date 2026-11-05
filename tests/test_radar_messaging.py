"""REQ-RAD-006: Radar asks Tower for a runway and records the runway Tower assigns."""

import builtins

import pytest

import messaging_demo
from src.db import Team, create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.messaging import apply_runway_assignment, process_runway_assignments, request_runway
from src.tower.messaging import assign_runway_to_aircraft


@pytest.fixture
def db():
    database = create_database(':memory:')
    store = AircraftStore(database)
    store.create(AircraftState('AAL123', 40.6450, -73.7735, 'Landing', 1500, '2026-10-22T13:30:00'))
    store.create(AircraftState('DAL456', 40.6380, -73.7700, 'Departing', 0, '2026-10-22T13:30:00'))
    yield database
    database.close()


def test_radar_requests_runway_for_landing(db):
    message_id = request_runway(db, 'AAL123', 'landing')

    messages = db.receive_messages(Team.TOWER)
    assert len(messages) == 1
    message = messages[0]
    assert message.id == message_id
    assert message.sender == Team.RADAR
    assert message.recipient == Team.TOWER
    assert message.message_type == 'runway_request'
    assert message.metadata == {'aircraft_id': 'AAL123', 'request_type': 'landing'}


def test_radar_requests_runway_for_departure(db):
    request_runway(db, 'DAL456', 'departure')

    message = db.receive_messages(Team.TOWER)[0]
    assert message.metadata == {'aircraft_id': 'DAL456', 'request_type': 'departure'}


@pytest.mark.parametrize('aircraft_id, expected', [('AAL123', 'landing'), ('DAL456', 'departure')])
def test_request_type_follows_aircraft_status_when_omitted(db, aircraft_id, expected):
    request_runway(db, aircraft_id)
    assert db.receive_messages(Team.TOWER)[0].metadata['request_type'] == expected


def test_request_type_is_case_insensitive(db):
    request_runway(db, 'AAL123', ' Landing ')
    assert db.receive_messages(Team.TOWER)[0].metadata['request_type'] == 'landing'


def test_request_is_published_for_the_dashboard(db):
    message_id = request_runway(db, 'AAL123')
    record = db.get_shared_state('request:AAL123')
    assert record['category'] == 'request'
    assert record['payload'] == {
        'aircraft_id': 'AAL123', 'status': 'requested', 'request_type': 'landing', 'message_id': message_id,
    }


@pytest.mark.parametrize('aircraft_id, request_type, error', [
    ('AAL123', 'parking', 'Runway request must be landing or departure'),
    ('', 'landing', 'Aircraft ID cannot be empty'),
    ('   ', 'landing', 'Aircraft ID cannot be empty'),
    ('UNKNOWN', 'landing', 'Unknown aircraft'),
])
def test_invalid_requests_send_and_store_nothing(db, aircraft_id, request_type, error):
    with pytest.raises(ValueError, match=error):
        request_runway(db, aircraft_id, request_type)
    assert db.receive_messages(Team.TOWER) == []
    assert db.list_shared_state(category='request') == []


def _tower_assigns(db, aircraft_id, runway_id):
    """Simulate Tower's REQ-TWR-006 message without Tower's runway checks."""
    return db.send_message(
        Team.TOWER, Team.RADAR, f'Assign {runway_id} to {aircraft_id}', message_type='function',
        metadata={'function': 'assign_runway', 'aircraft_id': aircraft_id, 'runway_id': runway_id},
    )


def test_tower_assignment_is_recorded_on_the_aircraft_end_to_end(db):
    request_runway(db, 'AAL123')
    assign_runway_to_aircraft(db, 'AAL123', 'RWY 2')  # Tower's real REQ-TWR-006 function

    updated = process_runway_assignments(db)

    assert [state.id for state in updated] == ['AAL123']
    assert AircraftStore(db).get('AAL123').runway_id == 'RWY 2'
    assert db.get_shared_state('aircraft:AAL123')['payload']['runway_id'] == 'RWY 2'
    request = db.get_shared_state('request:AAL123')['payload']
    assert (request['status'], request['runway_id']) == ('answered', 'RWY 2')


def test_assignments_are_applied_once_and_latest_wins(db):
    _tower_assigns(db, 'AAL123', 'RWY 1')
    _tower_assigns(db, 'AAL123', 'RWY 2')
    assert len(process_runway_assignments(db)) == 2
    assert AircraftStore(db).get('AAL123').runway_id == 'RWY 2'

    AircraftStore(db).assign_runway('AAL123', None)
    assert process_runway_assignments(db) == []  # old messages are not re-applied
    assert AircraftStore(db).get('AAL123').runway_id is None


def test_processing_leaves_radar_inbox_unread(db):
    db.send_message(Team.COMMAND, Team.RADAR, 'Weather update')
    _tower_assigns(db, 'AAL123', 'RWY 1')
    process_runway_assignments(db)
    assert len(db.receive_messages(Team.RADAR, unread_only=True)) == 2


def test_other_messages_are_ignored(db):
    db.send_message(Team.TOWER, Team.RADAR, 'Hold position')
    db.send_message(Team.COMMAND, Team.RADAR, 'Assign RWY 1 to AAL123', message_type='function',
                    metadata={'function': 'assign_runway', 'aircraft_id': 'AAL123', 'runway_id': 'RWY 1'})
    assert process_runway_assignments(db) == []
    assert AircraftStore(db).get('AAL123').runway_id is None


def test_unusable_assignments_are_skipped_and_not_retried(db):
    _tower_assigns(db, 'GHOST', 'RWY 1')
    db.send_message(Team.TOWER, Team.RADAR, 'Assign ? to AAL123', message_type='function',
                    metadata={'function': 'assign_runway', 'aircraft_id': 'AAL123'})
    _tower_assigns(db, 'DAL456', 'RWY 2')
    assert [state.id for state in process_runway_assignments(db)] == ['DAL456']
    assert process_runway_assignments(db) == []


def test_apply_runway_assignment_rejects_bad_messages(db):
    message_id = db.send_message(Team.TOWER, Team.RADAR, 'Hold position')
    text_message = next(m for m in db.receive_messages(Team.RADAR) if m.id == message_id)
    with pytest.raises(ValueError, match='not a Tower runway assignment'):
        apply_runway_assignment(db, text_message)

    _tower_assigns(db, 'GHOST', 'RWY 1')
    ghost = db.receive_messages(Team.RADAR)[-1]
    with pytest.raises(KeyError):
        apply_runway_assignment(db, ghost)


def _feed_inputs(monkeypatch, answers):
    answers = iter(answers)
    monkeypatch.setattr(builtins, 'input', lambda prompt='': next(answers))


def test_demo_radar_send_menu_requests_runway(db, monkeypatch, capsys):
    # Radar -> send -> function -> airplane 2 (DAL456) -> request 2 (departure)
    _feed_inputs(monkeypatch, ['2', '2', '2'])
    messaging_demo.send_messages(db, Team.RADAR)

    assert 'Runway request sent to tower' in capsys.readouterr().out
    (message,) = db.receive_messages(Team.TOWER)
    assert message.message_type == 'runway_request'
    assert message.metadata == {'aircraft_id': 'DAL456', 'request_type': 'departure'}


def test_demo_radar_send_menu_still_sends_text(db, monkeypatch):
    _feed_inputs(monkeypatch, ['1', 'tower', 'hello tower'])
    messaging_demo.send_messages(db, Team.RADAR)

    (message,) = db.receive_messages(Team.TOWER)
    assert message.message_type == 'text'
    assert message.content == 'hello tower'


def test_demo_reports_when_radar_tracks_no_aircraft(monkeypatch, capsys):
    empty = create_database(':memory:')
    try:
        _feed_inputs(monkeypatch, ['2'])
        messaging_demo.send_messages(empty, Team.RADAR)
        assert 'Radar is not tracking any airplanes' in capsys.readouterr().out
        assert empty.receive_messages(Team.TOWER) == []
    finally:
        empty.close()


def test_demo_radar_inbox_records_tower_assignment(db, capsys):
    request_runway(db, 'AAL123')
    assign_runway_to_aircraft(db, 'AAL123', 'RWY 1')

    messaging_demo.read_messages(db, Team.RADAR)

    out = capsys.readouterr().out
    assert 'Assign RWY 1 to AAL123' in out
    assert 'Radar recorded RWY 1 for AAL123.' in out
    assert AircraftStore(db).get('AAL123').runway_id == 'RWY 1'
    # Reading again shows the message but does not record it a second time.
    messaging_demo.read_messages(db, Team.RADAR)
    assert 'Radar recorded' not in capsys.readouterr().out


def test_demo_other_inboxes_do_not_record_assignments(db):
    assign_runway_to_aircraft(db, 'AAL123', 'RWY 1')
    messaging_demo.read_messages(db, Team.COMMAND)
    assert AircraftStore(db).get('AAL123').runway_id is None
