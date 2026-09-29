"""Tower messaging through the shared ATC message database."""

from src.db import Message, MessageDatabase, Team


def send_message(db: MessageDatabase, recipient: Team | str, content: str) -> int:
    """REQ-TWR-001: Send a message from Tower to another subsystem."""
    return db.send_message(Team.TOWER, recipient, content)


def receive_messages(
    db: MessageDatabase,
    *,
    unread_only: bool = False,
    limit: int = 100,
    mark_as_read: bool = False,
) -> list[Message]:
    """REQ-TWR-002: Receive messages addressed to Tower."""
    return db.receive_messages(
        Team.TOWER,
        unread_only=unread_only,
        limit=limit,
        mark_as_read=mark_as_read,
    )
