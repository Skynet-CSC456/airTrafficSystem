# REQ-RAD-001

import pytest

from src.db import Team, create_database
from src.radar.messaging import request_runway


@pytest.fixture
def db():
    database = create_database(":memory:")
    yield database
    database.close()


def test_radar_requests_runway_for_landing(db):
    message_id = request_runway(
        db,
        "AAL123",
        "landing"
    )

    messages = db.receive_messages(Team.TOWER)

    assert len(messages) == 1

    message = messages[0]

    assert message.id == message_id
    assert message.sender == Team.RADAR
    assert message.recipient == Team.TOWER
    assert message.message_type == "runway_request"

    assert message.metadata["aircraft_id"] == "AAL123"
    assert message.metadata["request_type"] == "landing"

def test_radar_requests_runway_for_takeoff(db):
    request_runway(
        db,
        "DAL456",
        "departing"
    )

    messages = db.receive_messages(Team.TOWER)

    assert len(messages) == 1

    message = messages[0]

    assert message.metadata["aircraft_id"] == "DAL456"
    assert message.metadata["request_type"] == "departing"

def test_invalid_runway_request_type(db):
    with pytest.raises(
        ValueError,
        match="Runway request must be landing or departing"
    ):
        request_runway(
            db,
            "AAL123",
            "parking"
        )

def test_empty_aircraft_id(db):
    with pytest.raises(
        ValueError,
        match="Aircraft ID cannot be empty"
    ):
        request_runway(
            db,
            "",
            "landing"
        )
