"""Internal SQLite persistence for Tower requests and their saved decisions."""

import json


class RequestStore:
    """Use the service's connection and transaction without committing."""

    def __init__(self, connection):
        self._connection = connection

    def get(self, request_id):
        return self._connection.execute(
            "SELECT * FROM tower_requests WHERE request_id=?", (request_id,)
        ).fetchone()

    def decision(self, request_id):
        row = self._connection.execute(
            "SELECT response FROM tower_requests WHERE request_id=?", (request_id,)
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def active_request_id(self, aircraft_id):
        row = self._connection.execute("""SELECT request_id FROM tower_requests
            WHERE aircraft_id=? AND outcome IN ('queued','cleared')""", (aircraft_id,)).fetchone()
        return row[0] if row is not None else None

    def insert(self, request_id, aircraft_id, airport_id, operation, preferred_runway,
               *, outcome, timestamp, legacy):
        cursor = self._connection.execute("""INSERT INTO tower_requests
            (request_id, aircraft_id, airport_id, operation, preferred_runway, outcome, created_at, legacy)
            VALUES (?,?,?,?,?,?,?,?)""",
            (request_id, aircraft_id, airport_id, operation, preferred_runway, outcome, timestamp, int(legacy)))
        return self._connection.execute(
            "SELECT * FROM tower_requests WHERE sequence=?", (cursor.lastrowid,)
        ).fetchone()

    def queued_rows(self, airport_id):
        return self._connection.execute("""SELECT * FROM tower_requests
            WHERE airport_id=? AND outcome='queued' ORDER BY sequence""", (airport_id,)).fetchall()

    def queue_position(self, airport_id, sequence):
        return self._connection.execute("""SELECT COUNT(*) FROM tower_requests
            WHERE airport_id=? AND outcome='queued' AND sequence<=?""", (airport_id, sequence)).fetchone()[0]

    def set_outcome(self, request_id, outcome, runway_id):
        self._connection.execute("UPDATE tower_requests SET outcome=?,runway_id=? WHERE request_id=?",
                                 (outcome, runway_id, request_id))

    def save_response(self, request_id, response):
        self._connection.execute("UPDATE tower_requests SET response=? WHERE request_id=?",
                                 (json.dumps(response), request_id))

    def queued_decisions(self, airport_id):
        return [json.loads(row[0]) for row in self._connection.execute("""SELECT response FROM tower_requests
            WHERE airport_id=? AND outcome='queued' ORDER BY sequence""", (airport_id,))]

    def decisions(self):
        return [json.loads(row[0]) for row in self._connection.execute(
            "SELECT response FROM tower_requests ORDER BY sequence"
        )]
