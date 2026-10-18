
from src.db import Team, create_database

# REQ-CMD-001: Verify that Command receives messages from Tower.
def test_tower_message_reaches_command(tmp_path, monkeypatch):
    """Check that Command can receive a message from Tower."""

    monkeypatch.chdir(tmp_path)
    db = create_database()

    try:
        message = "AC001 assigned to RWY 1"

        db.send_message(Team.TOWER, Team.COMMAND, message)

        received = db.receive_messages(Team.COMMAND, limit=100)

        assert any(message in str(item) for item in received)
    finally:
        db.close()
