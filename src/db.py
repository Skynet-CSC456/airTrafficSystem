"""SQLite persistence for ATC messaging and airport data.

The database path is configured with ``SQLITE_DATABASE`` and defaults to
``data/atc_system.db``.

Call :func:`create_database` once at application startup. It creates the
database file (when needed), creates the tables, and seeds the supported
teams. A database instance can then manage messages, airports, and runways.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

class Team(str, Enum):
    """The subteams that may send and receive messages."""

    RADAR = "radar"
    TOWER = "tower"
    COMMAND = "command"
    AIRPORT = "airport"


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


@dataclass(frozen=True)
class Runway:
    id: str
    available: bool
    status_time: datetime


@dataclass(frozen=True)
class Airport:
    id: int
    perimeter: float
    longitude: float
    latitude: float
    runways: tuple[Runway, ...]

    @property
    def number_of_runways(self) -> int:
        return len(self.runways)


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
    """
    CREATE TABLE IF NOT EXISTS airports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        perimeter REAL NOT NULL CHECK (perimeter > 0),
        longitude REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180),
        latitude REAL NOT NULL CHECK (latitude BETWEEN -90 AND 90)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runways (
        airport_id INTEGER NOT NULL,
        id TEXT NOT NULL,
        available INTEGER NOT NULL CHECK (available IN (0, 1)),
        status_time TEXT NOT NULL,
        PRIMARY KEY (airport_id, id),
        FOREIGN KEY (airport_id) REFERENCES airports(id) ON DELETE CASCADE
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


def _dimensions(perimeter: float, longitude: float, latitude: float) -> tuple[float, float, float]:
    values = (perimeter, longitude, latitude)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
           for value in values):
        raise ValueError("Airport dimensions must be finite numbers")
    if perimeter <= 0 or not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise ValueError("Perimeter must be positive and coordinates must be valid")
    return float(perimeter), float(longitude), float(latitude)


def _status_timestamp(value: datetime | str | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    elif isinstance(value, str):
        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?",
            value,
        ):
            raise ValueError("Status time must be an ISO 8601 timestamp")
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Status time must be an ISO 8601 timestamp") from exc
    if not isinstance(value, datetime):
        raise ValueError("Status time must be an ISO 8601 timestamp")
    return value.isoformat(timespec="seconds")


def _runway_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Runway ID cannot be empty")
    return value.strip()


def _availability(value: bool) -> int:
    if not isinstance(value, bool):
        raise ValueError("Runway availability must be a boolean")
    return int(value)


class MessageDatabase:
    """Connection and operations for ATC messages and airports."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.execute("PRAGMA foreign_keys = ON")

    def initialize_schema(self) -> None:
        """Create the schema and register all supported teams."""
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

    def create_airport(self, perimeter: float, longitude: float, latitude: float) -> Airport:
        """REQ-APT-001: Create an airport. Perimeter is measured in meters."""
        dimensions = _dimensions(perimeter, longitude, latitude)
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO airports (perimeter, longitude, latitude) VALUES (?, ?, ?)",
                dimensions,
            )
        return Airport(int(cursor.lastrowid), *dimensions, ())

    def get_airport(self, airport_id: int) -> Airport | None:
        """Return an airport and its runways, or None if it does not exist."""
        row = self.connection.execute(
            "SELECT id, perimeter, longitude, latitude FROM airports WHERE id = ?",
            (airport_id,),
        ).fetchone()
        if row is None:
            return None
        runway_rows = self.connection.execute(
            "SELECT id, available, status_time FROM runways WHERE airport_id = ? ORDER BY id",
            (airport_id,),
        ).fetchall()
        return Airport(
            id=row["id"],
            perimeter=row["perimeter"],
            longitude=row["longitude"],
            latitude=row["latitude"],
            runways=tuple(
                Runway(r["id"], bool(r["available"]), datetime.fromisoformat(r["status_time"]))
                for r in runway_rows
            ),
        )

    def list_airports(self) -> list[Airport]:
        """List airports in ID order, including their runways."""
        ids = self.connection.execute("SELECT id FROM airports ORDER BY id").fetchall()
        return [airport for row in ids if (airport := self.get_airport(row["id"])) is not None]

    def update_airport(
        self, airport_id: int, perimeter: float, longitude: float, latitude: float
    ) -> bool:
        """Replace an airport's dimensions; return False if it does not exist."""
        dimensions = _dimensions(perimeter, longitude, latitude)
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE airports SET perimeter = ?, longitude = ?, latitude = ? WHERE id = ?",
                (*dimensions, airport_id),
            )
        return cursor.rowcount > 0

    def delete_airport(self, airport_id: int) -> bool:
        """Delete an airport and its runways."""
        with self.connection:
            cursor = self.connection.execute("DELETE FROM airports WHERE id = ?", (airport_id,))
        return cursor.rowcount > 0

    def create_runway(
        self,
        airport_id: int,
        runway_id: str,
        available: bool,
        status_time: datetime | str | None = None,
    ) -> Runway:
        """Add a runway and its initial availability observation."""
        runway_id = _runway_id(runway_id)
        available_value = _availability(available)
        timestamp = _status_timestamp(status_time)
        with self.connection:
            self.connection.execute(
                "INSERT INTO runways (airport_id, id, available, status_time) VALUES (?, ?, ?, ?)",
                (airport_id, runway_id, available_value, timestamp),
            )
        return Runway(runway_id, available, datetime.fromisoformat(timestamp))

    def update_runway_status(
        self,
        airport_id: int,
        runway_id: str,
        available: bool,
        status_time: datetime | str | None = None,
    ) -> bool:
        """Update runway availability and observation time."""
        runway_id = _runway_id(runway_id)
        available_value = _availability(available)
        timestamp = _status_timestamp(status_time)
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE runways SET available = ?, status_time = ? WHERE airport_id = ? AND id = ?",
                (available_value, timestamp, airport_id, runway_id),
            )
        return cursor.rowcount > 0

    def delete_runway(self, airport_id: int, runway_id: str) -> bool:
        """Remove a runway from an airport."""
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM runways WHERE airport_id = ? AND id = ?",
                (airport_id, _runway_id(runway_id)),
            )
        return cursor.rowcount > 0

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


__all__ = ["Airport", "Message", "MessageDatabase", "Runway", "Team", "create_database"]
