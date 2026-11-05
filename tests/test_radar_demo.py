"""REQ-RAD-001 / REQ-RAD-004: Radar creates and updates airplanes from the messaging demo."""

import builtins

import pytest

import messaging_demo
from src.db import create_database
from src.radar.aircraft import AircraftState, AircraftStore


@pytest.fixture
def db():
    database = create_database(':memory:')
    yield database
    database.close()


def _feed_inputs(monkeypatch, answers):
    answers = iter(answers)
    monkeypatch.setattr(builtins, 'input', lambda prompt='': next(answers))


def test_create_airplane(db, monkeypatch, capsys):
    _feed_inputs(monkeypatch, ['AC010', '40.6450', '-73.7735', 'landing', '800'])
    messaging_demo.create_airplane(db)

    assert 'Airplane created!' in capsys.readouterr().out
    plane = AircraftStore(db).get('AC010')
    assert (plane.latitude, plane.longitude, plane.status, plane.altitude) == (40.645, -73.7735, 'Landing', 800)
    assert db.get_shared_state('aircraft:AC010') is not None  # visible to the dashboard


def test_create_airplane_accepts_status_number_and_reprompts_bad_input(db, monkeypatch, capsys):
    _feed_inputs(monkeypatch, ['AC011', 'north', '40.64', '-73.78', 'flying', '2', 'high', '450'])
    messaging_demo.create_airplane(db)

    out = capsys.readouterr().out
    assert 'Please enter a number.' in out
    assert 'Invalid status.' in out
    plane = AircraftStore(db).get('AC011')
    assert (plane.status, plane.altitude) == ('Departing', 450)


def test_create_airplane_rejects_duplicate_id(db, monkeypatch, capsys):
    AircraftStore(db).create(AircraftState('AC012', 40.64, -73.78, 'Parked', 0, '2026-11-05T10:00:00'))
    _feed_inputs(monkeypatch, ['AC012'])
    messaging_demo.create_airplane(db)

    assert 'AC012 already exists' in capsys.readouterr().out
    assert AircraftStore(db).get('AC012').status == 'Parked'


def test_create_airplane_reports_radar_validation_errors(db, monkeypatch, capsys):
    _feed_inputs(monkeypatch, ['AC013', '95', '-73.78', '3', '500'])
    messaging_demo.create_airplane(db)

    assert 'Could not create airplane: Invalid latitude' in capsys.readouterr().out
    assert AircraftStore(db).get('AC013') is None


def test_update_airplane_changes_only_what_is_typed(db, monkeypatch, capsys):
    store = AircraftStore(db)
    store.create(AircraftState('AC020', 40.64, -73.78, 'Landing', 900, '2026-01-01T00:00:00'))
    store.assign_runway('AC020', 'RWY 1')
    # airplane 1 -> keep latitude/longitude/status -> altitude 300
    _feed_inputs(monkeypatch, ['1', '', '', '', '300'])
    messaging_demo.update_airplane(db)

    assert 'Airplane updated!' in capsys.readouterr().out
    plane = store.get('AC020')
    assert (plane.latitude, plane.longitude, plane.status, plane.altitude) == (40.64, -73.78, 'Landing', 300)
    assert plane.runway_id == 'RWY 1'  # position updates keep the assigned runway
    assert db.get_shared_state('aircraft:AC020')['payload']['altitude'] == 300


def test_update_airplane_reports_stale_update(db, monkeypatch, capsys):
    AircraftStore(db).create(AircraftState('AC021', 40.64, -73.78, 'Landing', 900, '2099-01-01T00:00:00'))
    _feed_inputs(monkeypatch, ['1', '', '', '', '300'])
    messaging_demo.update_airplane(db)

    assert 'Radar ignored the update' in capsys.readouterr().out
    assert AircraftStore(db).get('AC021').altitude == 900


def test_update_airplane_with_no_airplanes(db, monkeypatch, capsys):
    messaging_demo.update_airplane(db)
    assert 'Radar is not tracking any airplanes' in capsys.readouterr().out


@pytest.mark.parametrize('team, shows_airplane_options', [('radar', True), ('tower', False), ('command', False)])
def test_only_radar_menu_offers_airplane_options(db, monkeypatch, capsys, team, shows_airplane_options):
    monkeypatch.setattr(messaging_demo, 'create_database', lambda: db)
    _feed_inputs(monkeypatch, [team, 'q'])
    messaging_demo.main()

    out = capsys.readouterr().out
    assert ('3. Create airplane' in out) is shows_airplane_options
    assert ('4. Update airplane' in out) is shows_airplane_options
