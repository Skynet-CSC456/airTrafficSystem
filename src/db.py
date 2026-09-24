"""SQLite persistence for communication between ATC subteams.

The database path is configured with ``SQLITE_DATABASE`` and defaults to
``data/atc_system.db``.

Call :func:`create_database` once at application startup. It creates the
database file (when needed), creates the tables, and seeds the three supported
teams. A database instance can then be used to send and receive messages.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

class Team(str, Enum):
    """The subteams that may send and receive messages."""

    RADAR = "radar"
    TOWER = "tower"
    COMMAND = "command"


@dataclass(frozen=True)
class Message:
    """A message exchanged between two ATC subteams."""

    id: int
    sender: Team
    recipient: Team
    content: str
    message_type: str
    metadata: dict[str, Any] | None
    created_at: datetime
    read_at: datetime | None


SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS teams (
        name VARCHAR(16) NOT NULL PRIMARY KEY
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_team VARCHAR(16) NOT NULL,
        recipient_team VARCHAR(16) NOT NULL,
        content TEXT NOT NULL,
        message_type VARCHAR(32) NOT NULL DEFAULT 'text',
        metadata TEXT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        read_at TEXT NULL,
        CONSTRAINT fk_messages_sender
            FOREIGN KEY (sender_team) REFERENCES teams(name),
        CONSTRAINT fk_messages_recipient
            FOREIGN KEY (recipient_team) REFERENCES teams(name),
        CONSTRAINT uq_messages_id UNIQUE (id)
    )
    """,
)

_TEAM_VALUES = tuple(team.value for team in Team)


def _team_value(team: Team | str) -> str:
    value = team.value if isinstance(team, Team) else team.lower()
    if value not in _TEAM_VALUES:
        valid = ", ".join(_TEAM_VALUES)
        raise ValueError(f"Unknown team {team!r}; expected one of: {valid}")
    return value


class MessageDatabase:
    """Connection and operations for the cross-team message store."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.execute("PRAGMA foreign_keys = ON")

    def initialize_schema(self) -> None:
        """Create the schema and register Radar, Tower, and Command."""
        cursor = self.connection.cursor()
        try:
            for statement in SCHEMA_SQL:
                cursor.execute(statement)
            cursor.executemany(
                "INSERT OR IGNORE INTO teams (name) VALUES (?)",
                [(team,) for team in _TEAM_VALUES],
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            cursor.close()

    def send_message(
        self,
        sender: Team | str,
        recipient: Team | str,
        content: str,
        *,
        message_type: str = "text",
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        """Persist a message and return its generated ID."""
        sender_value = _team_value(sender)
        recipient_value = _team_value(recipient)
        if sender_value == recipient_value:
            raise ValueError("A message sender and recipient must be different teams")
        if not content or not content.strip():
            raise ValueError("Message content cannot be empty")
        if not message_type or not message_type.strip():
            raise ValueError("Message type cannot be empty")

        cursor = self.connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO messages
                    (sender_team, recipient_team, content, message_type, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    sender_value,
                    recipient_value,
                    content,
                    message_type,
                    json.dumps(metadata) if metadata is not None else None,
                ),
            )
            self.connection.commit()
            return int(cursor.lastrowid)
        except Exception:
            self.connection.rollback()
            raise
        finally:
            cursor.close()

    def receive_messages(
        self,
        recipient: Team | str,
        *,
        unread_only: bool = False,
        limit: int = 100,
        mark_as_read: bool = False,
    ) -> list[Message]:
        """Return messages addressed to ``recipient``, oldest first.

        When ``mark_as_read`` is true, returned messages are marked read in the
        same transaction.  This makes polling consumers safe to use without
        needing a second database call.
        """
        recipient_value = _team_value(recipient)
        if limit < 1:
            raise ValueError("limit must be greater than zero")

        cursor = self.connection.cursor()
        try:
            query = """
                SELECT id, sender_team, recipient_team, content, message_type,
                       metadata, created_at, read_at
                FROM messages
                WHERE recipient_team = ?
            """
            parameters: list[Any] = [recipient_value]
            if unread_only:
                query += " AND read_at IS NULL"
            query += " ORDER BY created_at ASC, id ASC LIMIT ?"
            parameters.append(limit)
            cursor.execute(query, parameters)
            rows = cursor.fetchall()
            messages = [self._row_to_message(row) for row in rows]

            if mark_as_read and messages:
                ids = [message.id for message in messages]
                placeholders = ", ".join(["?"] * len(ids))
                cursor.execute(
                    f"UPDATE messages SET read_at = CURRENT_TIMESTAMP "
                    f"WHERE recipient_team = ? AND id IN ({placeholders}) AND read_at IS NULL",
                    [recipient_value, *ids],
                )
                self.connection.commit()
            return messages
        except Exception:
            self.connection.rollback()
            raise
        finally:
            cursor.close()

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _row_to_message(row: Mapping[str, Any]) -> Message:
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        read_at = row["read_at"]
        if isinstance(read_at, str):
            read_at = datetime.fromisoformat(read_at)
        return Message(
            id=int(row["id"]),
            sender=Team(row["sender_team"]),
            recipient=Team(row["recipient_team"]),
            content=row["content"],
            message_type=row["message_type"],
            metadata=metadata,
            created_at=created_at,
            read_at=read_at,
        )


def create_database(database: str | None = None) -> MessageDatabase:
    """Open the configured SQLite database and initialize its schema."""
    database = database or os.getenv("SQLITE_DATABASE", os.path.join("data", "atc_system.db"))
    if database != ":memory:":
        parent = os.path.dirname(os.path.abspath(database))
        os.makedirs(parent, exist_ok=True)

    connection = sqlite3.connect(database, detect_types=sqlite3.PARSE_DECLTYPES)
    connection.row_factory = sqlite3.Row
    message_database = MessageDatabase(connection)
    message_database.initialize_schema()
    return message_database


__all__ = ["Message", "MessageDatabase", "Team", "create_database"]
