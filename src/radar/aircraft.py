"""REQ-RAD-004: Aircraft state CRUD and aircraft/Tower messaging.

Also implements REQ-RAD-001.md, whose internal requirement ID is REQ-RAD-004.
"""

from dataclasses import asdict, dataclass
from datetime import datetime
import math
import re

from src.db import MessageDatabase


@dataclass(frozen=True)
class AircraftState:
    id: str
    latitude: float
    longitude: float
    status: str
    altitude: float  # meters above ground
    timestamp: str  # UTC, YYYY-MM-DDTHH:MM:SS

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Aircraft ID must be a nonempty string")
        for name, lower, upper in (
            ("latitude", -90, 90), ("longitude", -180, 180),
            ("altitude", 0, math.inf),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError(f"Invalid {name}: {value!r}")
        if self.status not in ("Parked", "Departing", "Landing"):
            raise ValueError("Status must be Parked, Departing, or Landing")
        if not isinstance(self.timestamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", self.timestamp):
            raise ValueError("Timestamp must be UTC YYYY-MM-DDTHH:MM:SS")
        datetime.strptime(self.timestamp, "%Y-%m-%dT%H:%M:%S")


class AircraftStore:
    """Uses the shared SQLite connection; stores only the latest aircraft state."""

    def __init__(self, db: MessageDatabase):
        self.connection = db.connection
        with self.connection:
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS aircraft (
                    id TEXT PRIMARY KEY NOT NULL,
                    latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
                    longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180),
                    status TEXT NOT NULL CHECK(status IN ('Parked','Departing','Landing')),
                    altitude REAL NOT NULL CHECK(altitude >= 0),
                    timestamp TEXT NOT NULL
                )
            """)
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS aircraft_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    aircraft_id TEXT NOT NULL REFERENCES aircraft(id) ON DELETE CASCADE,
                    direction TEXT NOT NULL CHECK(direction IN ('to_tower','to_aircraft')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
                    read_at TEXT
                )
            """)

    def create(self, state: AircraftState) -> AircraftState:
        with self.connection:
            self.connection.execute(
                "INSERT INTO aircraft (id, latitude, longitude, status, altitude, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                tuple(asdict(state).values()),
            )
        return state

    def get(self, aircraft_id: str) -> AircraftState | None:
        row = self.connection.execute("SELECT * FROM aircraft WHERE id = ?", (aircraft_id,)).fetchone()
        return AircraftState(**dict(row)) if row else None

    def list(self) -> list[AircraftState]:
        return [AircraftState(**dict(row)) for row in self.connection.execute("SELECT * FROM aircraft ORDER BY id")]

    def update(self, state: AircraftState) -> bool:
        """Return False for stale/duplicate updates; unknown IDs raise KeyError."""
        with self.connection:
            if self.get(state.id) is None:
                raise KeyError(state.id)
            cursor = self.connection.execute("""
                UPDATE aircraft SET latitude=?, longitude=?, status=?, altitude=?, timestamp=?
                WHERE id=? AND timestamp < ?
            """, (state.latitude, state.longitude, state.status, state.altitude,
                  state.timestamp, state.id, state.timestamp))
        return cursor.rowcount == 1

    def delete(self, aircraft_id: str) -> bool:
        with self.connection:
            cursor = self.connection.execute("DELETE FROM aircraft WHERE id=?", (aircraft_id,))
        return cursor.rowcount == 1

    def send_message(self, aircraft_id: str, content: str, *, to_tower: bool) -> int:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Message content cannot be empty")
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO aircraft_messages (aircraft_id, direction, content) VALUES (?, ?, ?)",
                (aircraft_id, "to_tower" if to_tower else "to_aircraft", content),
            )
        return int(cursor.lastrowid)

    def receive_messages(self, *, for_tower: bool, aircraft_id: str | None = None) -> list[dict]:
        """Consume unread messages; aircraft readers must provide their ID."""
        if not for_tower and not aircraft_id:
            raise ValueError("Aircraft recipient ID is required")
        query = "SELECT * FROM aircraft_messages WHERE direction=? AND read_at IS NULL"
        params = ["to_tower" if for_tower else "to_aircraft"]
        if aircraft_id is not None:
            query += " AND aircraft_id=?"
            params.append(aircraft_id)
        with self.connection:
            # Lock before selecting so concurrent consumers cannot claim the same rows.
            self.connection.execute("BEGIN IMMEDIATE")
            rows = self.connection.execute(query + " ORDER BY id", params).fetchall()
            self.connection.executemany(
                "UPDATE aircraft_messages SET read_at=strftime('%Y-%m-%dT%H:%M:%S','now') WHERE id=?",
                [(row["id"],) for row in rows],
            )
        return [dict(row) for row in rows]
