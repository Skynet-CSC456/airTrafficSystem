"""Acceptance tests for REQ-TWR-001 and REQ-TWR-002."""

import pytest

from src.db import Team, create_database
from src.tower.messaging import receive_messages, send_message


@pytest.fixture
def db():
    database = create_database(":memory:")
    yield database
    database.close()


# REQ-TWR-001: Tower can send messages to other subsystems.
@pytest.mark.parametrize("recipient", [Team.RADAR, Team.COMMAND])
def test_send_message_reaches_recipient(db, recipient):
    message_id = send_message(db, recipient, "Runway 4 is available")

    messages = db.receive_messages(recipient)
    assert len(messages) == 1
    assert messages[0].id == message_id
    assert messages[0].sender == Team.TOWER
    assert messages[0].recipient == recipient
    assert messages[0].content == "Runway 4 is available"


@pytest.mark.parametrize(
    "recipient, content, error",
    [
        ("unknown", "Ready", "Unknown team"),
        (Team.TOWER, "Ready", "must be different teams"),
        (Team.RADAR, "", "Message content cannot be empty"),
        (Team.COMMAND, "   ", "Message content cannot be empty"),
    ],
)
def test_send_message_preserves_database_validation(db, recipient, content, error):
    # REQ-TWR-001
    with pytest.raises(ValueError, match=error):
        send_message(db, recipient, content)
    for team in Team:
        assert db.receive_messages(team) == []


def test_receive_messages_returns_only_tower_inbox(db):
    # REQ-TWR-002
    radar_id = db.send_message(Team.RADAR, Team.TOWER, "Aircraft approaching")
    command_id = db.send_message(Team.COMMAND, Team.TOWER, "Confirm runway status")
    db.send_message(Team.RADAR, Team.COMMAND, "Other inbox")
    db.send_message(Team.TOWER, Team.RADAR, "Outbound message")

    messages = receive_messages(db)
    assert [message.id for message in messages] == [radar_id, command_id]
    assert [message.sender for message in messages] == [Team.RADAR, Team.COMMAND]
    assert all(message.recipient == Team.TOWER for message in messages)
    assert [message.content for message in messages] == [
        "Aircraft approaching", "Confirm runway status"
    ]
    assert [message.id for message in receive_messages(db, unread_only=True)] == [
        radar_id, command_id
    ]


def test_receive_messages_empty_inbox(db):
    # REQ-TWR-002
    assert receive_messages(db) == []


def test_receive_messages_unread_marking_and_limit(db):
    # REQ-TWR-002
    first_id = db.send_message(Team.RADAR, Team.TOWER, "First")
    second_id = db.send_message(Team.COMMAND, Team.TOWER, "Second")
    other_id = db.send_message(Team.RADAR, Team.COMMAND, "Other inbox")

    messages = receive_messages(db, unread_only=True, limit=1, mark_as_read=True)
    assert [message.id for message in messages] == [first_id]
    assert [message.id for message in receive_messages(db, unread_only=True)] == [second_id]
    history = receive_messages(db)
    assert [message.id for message in history] == [first_id, second_id]
    assert history[0].read_at is not None
    assert history[1].read_at is None
    assert [message.id for message in db.receive_messages(Team.COMMAND, unread_only=True)] == [other_id]


def test_receive_messages_preserves_limit_validation(db):
    # REQ-TWR-002
    with pytest.raises(ValueError, match="limit must be greater than zero"):
        receive_messages(db, limit=0)
