
"""Automated tests for REQ-TWR-003: Airport CRUD."""

import pytest

from src.db import Team, create_database
from src.tower.airport import AirportDatabase


@pytest.fixture
def airport_db(tmp_path):
    """Create an isolated SQLite database for each test."""
    db = create_database(str(tmp_path / "test_atc.db"))
    airport = AirportDatabase(db)

    yield airport

    db.close()


def make_airport(airport_db):
    airport_db.create_airport(
        airport_id="JFK",
        perimeter=22000.0,
        latitude=40.6413,
        longitude=-73.7781,
        runway_ids=["04L", "04R"],
    )


def test_create_and_retrieve_airport(airport_db):
    """REQ-TWR-003: Create and read an airport."""
    make_airport(airport_db)

    airport = airport_db.get_airport("JFK")

    assert airport["airport_id"] == "JFK"
    assert airport["latitude"] == 40.6413
    assert airport["number_of_runways"] == 2
    assert len(airport["runways"]) == 2
    assert airport["runways"][0]["available"] is True


def test_update_airport(airport_db):
    """REQ-TWR-003: Update airport information."""
    make_airport(airport_db)

    airport_db.update_airport(
        "JFK", 25000.0, 40.65, -73.78
    )

    airport = airport_db.get_airport("JFK")

    assert airport["perimeter"] == 25000.0
    assert airport["latitude"] == 40.65


def test_delete_airport(airport_db):
    """REQ-TWR-003: Delete airport and associated runways."""
    make_airport(airport_db)

    airport_db.delete_airport("JFK")

    assert airport_db.get_airport("JFK") is None

    count = airport_db.connection.execute(
        "SELECT COUNT(*) FROM runways WHERE airport_id = ?",
        ("JFK",),
    ).fetchone()[0]

    assert count == 0


def test_duplicate_airport_rejected(airport_db):
    """REQ-TWR-003: Airport IDs must be unique."""
    make_airport(airport_db)

    with pytest.raises(ValueError):
        make_airport(airport_db)


def test_invalid_coordinates_rejected(airport_db):
    """REQ-TWR-003: Invalid coordinates must be rejected."""
    with pytest.raises(ValueError):
        airport_db.create_airport(
            "BAD", 1000.0, 100.0, -73.0, ["01"]
        )


def test_runway_status_update(airport_db):
    """REQ-TWR-003: Update runway availability and timestamp."""
    make_airport(airport_db)

    airport_db.update_runway_status("JFK", "04L", False)

    airport = airport_db.get_airport("JFK")
    runway = airport["runways"][0]

    assert runway["runway_id"] == "04L"
    assert runway["available"] is False
    assert "T" in runway["timestamp"]


def test_add_runway(airport_db):
    """REQ-TWR-003: Adding a runway updates the runway count."""
    make_airport(airport_db)

    airport_db.add_runway("JFK", "13L")

    assert airport_db.get_airport("JFK")["number_of_runways"] == 3


def test_runway_messaging(airport_db):
    """REQ-TWR-003: Tower can notify Radar and Command."""
    make_airport(airport_db)

    airport_db.update_runway_status("JFK", "04L", False)
    airport_db.notify_runway_status("JFK", "04L")

    radar_messages = airport_db.db.receive_messages(Team.RADAR)
    command_messages = airport_db.db.receive_messages(Team.COMMAND)

    assert len(radar_messages) == 1
    assert len(command_messages) == 1
    assert radar_messages[0].sender == Team.TOWER
    assert "unavailable" in radar_messages[0].content
    assert "04L" in command_messages[0].content
