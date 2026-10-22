"""REQ-APT-001 and REQ-APT-002 acceptance tests."""

import sqlite3
from datetime import datetime

import pytest

from src.airport.messaging import receive_messages, send_message
from src.db import Team, create_database


@pytest.fixture
def db():
    database = create_database(":memory:")
    yield database
    database.close()


def test_airport_and_runway_crud(db):
    airport = db.create_airport(12000, -73.7781, 40.6413)
    assert airport.number_of_runways == 0
    assert db.get_airport(airport.id) == airport

    db.create_runway(airport.id, "04L", True, "2026-10-08T09:30:00")
    db.create_runway(airport.id, "22R", False, "2026-10-08T09:31:00")
    saved = db.get_airport(airport.id)
    assert saved.number_of_runways == 2
    assert [(r.id, r.available) for r in saved.runways] == [("04L", True), ("22R", False)]
    assert saved.runways[0].status_time == datetime(2026, 10, 8, 9, 30)

    assert db.update_airport(airport.id, 13000, -73.7, 40.6)
    assert db.update_runway_status(airport.id, "04L", False, "2026-10-08T10:00:00")
    saved = db.get_airport(airport.id)
    assert saved.perimeter == 13000
    assert saved.runways[0].available is False
    assert saved.runways[0].status_time == datetime(2026, 10, 8, 10)

    assert db.delete_runway(airport.id, "22R")
    assert db.get_airport(airport.id).number_of_runways == 1
    assert db.delete_airport(airport.id)
    assert db.get_airport(airport.id) is None
    assert db.connection.execute("SELECT COUNT(*) FROM runways").fetchone()[0] == 0


def test_airport_persists_on_disk(tmp_path):
    path = tmp_path / "airport.db"
    db = create_database(str(path))
    airport = db.create_airport(1000, 10, 20)
    db.create_runway(airport.id, "A", True)
    db.close()

    reopened = create_database(str(path))
    assert reopened.get_airport(airport.id).number_of_runways == 1
    reopened.close()


def test_invalid_airport_and_runway_data(db):
    with pytest.raises(ValueError):
        db.create_airport(0, 0, 0)
    with pytest.raises(ValueError):
        db.create_airport(100, 181, 0)
    airport = db.create_airport(100, 0, 0)
    with pytest.raises(ValueError):
        db.create_runway(airport.id, " ", True)
    with pytest.raises(ValueError):
        db.create_runway(airport.id, "A", "yes")
    with pytest.raises(ValueError):
        db.create_runway(airport.id, "A", True, "yesterday")
    with pytest.raises(ValueError):
        db.create_runway(airport.id, "A", True, "2026-10-08T09:30")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_runway(999, "A", True)
    assert db.get_airport(airport.id).number_of_runways == 0


@pytest.mark.parametrize("recipient", [Team.RADAR, Team.COMMAND])
def test_airport_can_send_and_receive(db, recipient):
    message_id = send_message(db, recipient, "Runway 04L unavailable")
    delivered = db.receive_messages(recipient)
    assert delivered[0].id == message_id
    assert delivered[0].sender == Team.AIRPORT
    assert delivered[0].content == "Runway 04L unavailable"

    db.send_message(recipient, Team.AIRPORT, "Acknowledged")
    messages = receive_messages(db, unread_only=True, mark_as_read=True)
    assert [m.content for m in messages] == ["Acknowledged"]
    assert receive_messages(db, unread_only=True) == []


def test_airport_rejects_other_message_destinations(db):
    with pytest.raises(ValueError):
        send_message(db, Team.TOWER, "Hello")
    with pytest.raises(ValueError):
        send_message(db, Team.RADAR, " ")
