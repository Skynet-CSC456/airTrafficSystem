"""Acceptance tests for REQ-TWR-006: Tower assigns a runway via a function message."""

import builtins

import pytest

import messaging_demo
from src.db import Team, create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.tower.messaging import (
    assign_runway_to_aircraft,
    list_airborne_aircraft,
    load_runways,
)


@pytest.fixture
def db():
    database = create_database(":memory:")
    store = AircraftStore(database)
    store.create(AircraftState("AC001", 40.0, -73.0, "Departing", 500, "2030-01-01T00:00:00"))
    store.create(AircraftState("AC002", 40.0, -73.0, "Landing", 120, "2030-01-01T00:00:00"))
    store.create(AircraftState("AC003", 40.0, -73.0, "Parked", 0, "2030-01-01T00:00:00"))
    yield database
    database.close()


def test_only_airborne_aircraft_are_listed(db):
    # REQ-TWR-006
    assert [a.id for a in list_airborne_aircraft(db)] == ["AC001", "AC002"]


def test_assignment_sends_function_message_to_radar_and_command(db):
    # REQ-TWR-006
    decision = assign_runway_to_aircraft(db, "AC001", "RWY 2")

    assert decision["status"] == "cleared"
    for team in (Team.RADAR, Team.COMMAND):
        (message,) = db.receive_messages(team)
        assert message.sender == Team.TOWER
        assert message.message_type == "function"
        assert message.id == decision["message_ids"][team.value]
        assert message.metadata["function"] == "assign_runway"
        assert message.metadata["aircraft_id"] == "AC001"
        assert message.metadata["runway_id"] == "RWY 2"
        assert message.metadata["occupied_until"] == decision["occupied_until"]
    assert db.receive_messages(Team.TOWER) == []


def test_assignment_updates_shared_state(db):
    # REQ-TWR-006
    assign_runway_to_aircraft(db, "AC001", "RWY 1")

    stored = {r["id"]: r for r in load_runways(db)}
    assert stored["RWY 1"]["available"] is False
    assert stored["RWY 2"]["available"] is True
    assert db.get_shared_state("decision:AC001")["payload"]["runway_id"] == "RWY 1"


def test_occupied_runway_is_rejected_without_side_effects(db):
    # REQ-TWR-006 / REQ-TWR-005
    assign_runway_to_aircraft(db, "AC001", "RWY 1")
    sent_before = len(db.receive_messages(Team.RADAR))

    with pytest.raises(ValueError, match="No safe runway"):
        assign_runway_to_aircraft(db, "AC002", "RWY 1")

    assert len(db.receive_messages(Team.RADAR)) == sent_before
    assert db.get_shared_state("decision:AC002") is None


@pytest.mark.parametrize(
    "aircraft_id, runway_id, error",
    [
        ("AC003", "RWY 1", "not in the air"),
        ("NOPE", "RWY 1", "Unknown aircraft"),
        ("AC001", "RWY 9", "not found"),
    ],
)
def test_invalid_assignments_are_rejected(db, aircraft_id, runway_id, error):
    # REQ-TWR-006
    with pytest.raises(ValueError, match=error):
        assign_runway_to_aircraft(db, aircraft_id, runway_id)
    assert db.receive_messages(Team.RADAR) == []
    assert db.receive_messages(Team.COMMAND) == []


def _feed_inputs(monkeypatch, answers):
    answers = iter(answers)
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))


def test_demo_tower_send_menu_assigns_runway(db, monkeypatch, capsys):
    # REQ-TWR-006: Tower -> send -> function -> airplane 2 (AC002) -> runway 2
    _feed_inputs(monkeypatch, ["2", "2", "2"])
    messaging_demo.send_messages(db, Team.TOWER)

    assert "Function message sent to radar and command" in capsys.readouterr().out
    (message,) = db.receive_messages(Team.COMMAND)
    assert message.metadata["aircraft_id"] == "AC002"
    assert message.metadata["runway_id"] == "RWY 2"


def test_demo_tower_send_menu_still_sends_text(db, monkeypatch):
    # REQ-TWR-001 remains available from the same menu.
    _feed_inputs(monkeypatch, ["1", "radar", "hello radar"])
    messaging_demo.send_messages(db, Team.TOWER)

    (message,) = db.receive_messages(Team.RADAR)
    assert message.message_type == "text"
    assert message.content == "hello radar"


def test_demo_other_teams_skip_the_function_menu(db, monkeypatch):
    _feed_inputs(monkeypatch, ["tower", "hi tower"])
    messaging_demo.send_messages(db, Team.RADAR)

    assert [m.content for m in db.receive_messages(Team.TOWER)] == ["hi tower"]


def test_demo_reports_when_no_aircraft_in_the_air(monkeypatch, capsys):
    empty = create_database(":memory:")
    try:
        _feed_inputs(monkeypatch, ["2"])
        messaging_demo.send_messages(empty, Team.TOWER)
        assert "No airplanes are in the air" in capsys.readouterr().out
        assert empty.receive_messages(Team.RADAR) == []
    finally:
        empty.close()
