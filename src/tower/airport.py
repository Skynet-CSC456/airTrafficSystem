"""REQ-TWR-007: authoritative Airport/runway storage, adapted from local CRUD.

Availability is administrative openness AND absence of an active reservation.
Shared-state rows are disposable projections, never allocation inputs.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from sqlite3 import IntegrityError

from src.db import MessageDatabase, Team


def current_timestamp() -> str:
    """Timezone-aware UTC timestamp for Tower records and contracts."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _identifier(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{label} must be a nonempty string without surrounding whitespace")


class AirportDatabase:
    """Airport CRUD; reservation policy lives in TowerService, not this repository."""

    def __init__(self, db: MessageDatabase):
        self.db = db
        self.connection = db.connection
        self.initialize_schema()

    def initialize_schema(self):
        # Fail clearly rather than silently reinterpret the incompatible PR #17 schema.
        with self.db.transaction():
            for table, column in (("airports", "airport_id"), ("runways", "runway_id")):
                columns = self.connection.execute(f"PRAGMA table_info({table})").fetchall()
                if columns and column not in {r["name"] for r in columns}:
                    raise ValueError("Incompatible Airport schema: migrate PR #17 data before use")
            self.connection.execute("""CREATE TABLE IF NOT EXISTS airports (
                airport_id TEXT PRIMARY KEY NOT NULL,
                perimeter REAL NOT NULL CHECK(perimeter > 0),
                latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
                longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180))""")
            self.connection.execute("""CREATE TABLE IF NOT EXISTS runways (
                airport_id TEXT NOT NULL REFERENCES airports(airport_id) ON DELETE CASCADE,
                runway_id TEXT NOT NULL,
                available INTEGER NOT NULL DEFAULT 1 CHECK(available IN (0,1)),
                timestamp TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'mixed' CHECK(mode IN ('landing','departure','mixed')),
                PRIMARY KEY(airport_id, runway_id))""")
            columns = {r["name"] for r in self.connection.execute("PRAGMA table_info(runways)")}
            if "mode" not in columns:
                self.connection.execute("ALTER TABLE runways ADD COLUMN mode TEXT NOT NULL DEFAULT 'mixed'")
            self.connection.execute("""CREATE TABLE IF NOT EXISTS tower_requests (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL UNIQUE,
                aircraft_id TEXT NOT NULL,
                airport_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                preferred_runway TEXT,
                outcome TEXT NOT NULL CHECK(outcome IN ('queued','cleared','rejected','completed')),
                runway_id TEXT,
                created_at TEXT NOT NULL,
                response TEXT,
                legacy INTEGER NOT NULL DEFAULT 0 CHECK(legacy IN (0,1)))""")
            self.connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS tower_one_runway
                ON tower_requests(airport_id, runway_id) WHERE outcome='cleared'""")
            self.connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS tower_one_aircraft
                ON tower_requests(aircraft_id) WHERE outcome IN ('cleared','queued')""")
            self.connection.execute("""CREATE TABLE IF NOT EXISTS tower_processed_messages (
                message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
                processed_at TEXT NOT NULL)""")
            self.connection.execute("""CREATE INDEX IF NOT EXISTS tower_fifo
                ON tower_requests(airport_id, outcome, sequence)""")

    @staticmethod
    def validate_airport(airport_id, perimeter, latitude, longitude):
        _identifier(airport_id, "Airport ID")
        for value in (perimeter, latitude, longitude):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Airport dimensions must be finite numbers")
        if perimeter <= 0 or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("Perimeter must be positive and coordinates valid")

    def create_airport(self, airport_id, perimeter, latitude, longitude, runway_ids):
        """Create airport with at least one mixed-use runway; perimeter is meters."""
        self.validate_airport(airport_id, perimeter, latitude, longitude)
        if not runway_ids:
            raise ValueError("An airport must have at least one runway")
        for runway_id in runway_ids:
            _identifier(runway_id, "Runway ID")
        if len(set(runway_ids)) != len(runway_ids):
            raise ValueError("Duplicate runway IDs are not allowed")
        try:
            with self.db.transaction():
                if not self.connection.execute("SELECT 1 FROM airports LIMIT 1").fetchone() and self.db.list_shared_state(category="runway"):
                    raise ValueError("Existing runway previews need explicit migration or a fresh database")
                self.connection.execute("INSERT INTO airports VALUES (?,?,?,?)",
                                        (airport_id, perimeter, latitude, longitude))
                for runway_id in runway_ids:
                    self.add_runway(airport_id, runway_id)
                self.db.upsert_shared_state(f"airport:{airport_id}", self.get_airport(airport_id), category="airport")
        except IntegrityError as exc:
            raise ValueError("Airport or runway already exists") from exc

    def list_airports(self):
        """Return airports in ID order, each with current runway availability."""
        with self.db.transaction():
            return [self.get_airport(r[0]) for r in self.connection.execute(
                "SELECT airport_id FROM airports ORDER BY airport_id").fetchall()]

    def get_airport(self, airport_id):
        with self.db.transaction():
            row = self.connection.execute("SELECT * FROM airports WHERE airport_id=?", (airport_id,)).fetchone()
            if row is None:
                return None
            runways = self.list_runways(airport_id)
            return {**dict(row), "number_of_runways": len(runways), "runways": runways}

    def list_runways(self, airport_id):
        """Return computed availability; administrative 'open' is kept separately."""
        rows = self.connection.execute("""SELECT r.*, q.request_id, q.aircraft_id
            FROM runways r LEFT JOIN tower_requests q
            ON q.airport_id=r.airport_id AND q.runway_id=r.runway_id AND q.outcome='cleared'
            WHERE r.airport_id=? ORDER BY r.runway_id""", (airport_id,)).fetchall()
        return [{"airport_id": r["airport_id"], "runway_id": r["runway_id"],
                 "id": r["runway_id"], "open": bool(r["available"]),
                 "available": bool(r["available"]) and r["request_id"] is None,
                 "mode": r["mode"], "timestamp": r["timestamp"],
                 "request_id": r["request_id"], "aircraft_id": r["aircraft_id"],
                 "occupied_until": None} for r in rows]

    def _project(self, airport_id):
        """Refresh compatibility views within the caller's transaction."""
        for runway in self.list_runways(airport_id):
            key = self._runway_key(airport_id, runway["id"])
            self.db.upsert_shared_state(key, runway, category="runway")
        airport = self.get_airport(airport_id)
        if airport:
            self.db.upsert_shared_state(f"airport:{airport_id}", airport, category="airport")

    @staticmethod
    def _runway_key(airport_id, runway_id):
        # JSON encoding avoids collisions between IDs containing punctuation.
        return "runway:tower:" + json.dumps([airport_id, runway_id])

    def update_airport(self, airport_id, perimeter, latitude, longitude):
        self.validate_airport(airport_id, perimeter, latitude, longitude)
        with self.db.transaction():
            cursor = self.connection.execute("""UPDATE airports SET perimeter=?,latitude=?,longitude=?
                WHERE airport_id=?""", (perimeter, latitude, longitude, airport_id))
            if not cursor.rowcount:
                raise ValueError("Airport not found")
            self._project(airport_id)

    def _require_idle(self, airport_id, runway_id=None, *, include_queue=True):
        query = "SELECT 1 FROM tower_requests WHERE airport_id=? AND "
        query += "outcome IN ('queued','cleared')" if include_queue else "outcome='cleared'"
        args = [airport_id]
        if runway_id is not None:
            query += " AND (runway_id=? OR (outcome='queued' AND (preferred_runway=? OR preferred_runway IS NULL)))"
            args += [runway_id, runway_id]
        if self.connection.execute(query, args).fetchone():
            raise ValueError("Complete active requests before changing or deleting this resource")

    def delete_airport(self, airport_id):
        """Reject deletion with active requests; retain terminal request audit history."""
        with self.db.transaction():
            self._require_idle(airport_id)
            runways = self.list_runways(airport_id)
            if not self.connection.execute("DELETE FROM airports WHERE airport_id=?", (airport_id,)).rowcount:
                raise ValueError("Airport not found")
            for runway in runways:
                self.connection.execute("DELETE FROM shared_state WHERE key=?",
                                        (self._runway_key(airport_id, runway["id"]),))
            self.connection.execute("DELETE FROM shared_state WHERE key=?", (f"airport:{airport_id}",))

    def add_runway(self, airport_id, runway_id, *, mode="mixed", available=True):
        _identifier(runway_id, "Runway ID")
        self._validate_runway(mode, available)
        try:
            with self.db.transaction():
                self.connection.execute("""INSERT INTO runways
                    (airport_id,runway_id,available,timestamp,mode) VALUES (?,?,?,?,?)""",
                    (airport_id, runway_id, int(available), current_timestamp(), mode))
                self._project(airport_id)
        except IntegrityError as exc:
            raise ValueError("Airport not found or runway already exists") from exc

    @staticmethod
    def _validate_runway(mode, available):
        if mode not in ("landing", "departure", "mixed"):
            raise ValueError("Mode must be landing, departure, or mixed")
        if not isinstance(available, bool):
            raise ValueError("Availability must be True or False")

    def update_runway(self, airport_id, runway_id, *, available, mode):
        """Administrative configuration only; service promotion is explicit afterward."""
        self._validate_runway(mode, available)
        with self.db.transaction():
            self._require_idle(airport_id, runway_id, include_queue=False)
            cursor = self.connection.execute("""UPDATE runways SET available=?,mode=?,timestamp=?
                WHERE airport_id=? AND runway_id=?""",
                (int(available), mode, current_timestamp(), airport_id, runway_id))
            if not cursor.rowcount:
                raise ValueError("Runway not found")
            self._project(airport_id)

    def update_runway_status(self, airport_id, runway_id, available):
        with self.db.transaction():
            runway = next((r for r in self.list_runways(airport_id) if r["id"] == runway_id), None)
            if runway is None:
                raise ValueError("Runway not found")
            self.update_runway(airport_id, runway_id, available=available, mode=runway["mode"])

    def delete_runway(self, airport_id, runway_id):
        with self.db.transaction():
            self._require_idle(airport_id, runway_id)
            if not self.connection.execute("DELETE FROM runways WHERE airport_id=? AND runway_id=?",
                                           (airport_id, runway_id)).rowcount:
                raise ValueError("Runway not found")
            self.connection.execute("DELETE FROM shared_state WHERE key=?",
                                    (self._runway_key(airport_id, runway_id),))
            self._project(airport_id)

    def notify_runway_status(self, airport_id, runway_id):
        with self.db.transaction():
            runway = next((r for r in self.list_runways(airport_id) if r["id"] == runway_id), None)
            if runway is None:
                raise ValueError("Airport or runway not found")
            content = f"Airport {airport_id}, runway {runway_id}: {'available' if runway['available'] else 'unavailable'} at {runway['timestamp']} UTC"
            for recipient in (Team.RADAR, Team.COMMAND):
                self.db.send_message(Team.TOWER, recipient, content)
