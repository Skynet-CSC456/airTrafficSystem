"""REQ-APT-002: Messaging between Airport, Radar, and Command."""

from src.db import Message, MessageDatabase, Team


def send_message(db: MessageDatabase, recipient: Team | str, content: str) -> int:
    """Send an Airport message to Radar or Command."""
    try:
        recipient = Team(recipient.lower() if isinstance(recipient, str) else recipient)
    except ValueError as exc:
        raise ValueError("Airport messages may only be sent to Radar or Command") from exc
    if recipient not in (Team.RADAR, Team.COMMAND):
        raise ValueError("Airport messages may only be sent to Radar or Command")
    return db.send_message(Team.AIRPORT, recipient, content)


def receive_messages(
    db: MessageDatabase,
    *,
    unread_only: bool = False,
    limit: int = 100,
    mark_as_read: bool = False,
) -> list[Message]:
    """Receive messages addressed to Airport."""
    return db.receive_messages(
        Team.AIRPORT,
        unread_only=unread_only,
        limit=limit,
        mark_as_read=mark_as_read,
    )
