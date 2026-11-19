"""REQ-TWR-007: persistent R2 traffic sequencing and coordinate separation.

The ten-degree rule is the project's simulation rule, not an aviation standard.
Radar receives corrective instructions; only observed positions resolve alerts.
"""

from __future__ import annotations

import json
from itertools import combinations

from src.db import MessageDatabase, Team
from src.radar.aircraft import AircraftStore

MIN_SEPARATION = 10
PRIORITY_STEPS = {"late_2_hours": 1, "late_4_hours": 2, "low_fuel": 3}
STATE_KEY = "tower_traffic"


def has_safe_separation(first, second) -> bool:
    """Safe at >=10 degrees on either axis, using wrapped longitude distance."""
    longitude = abs(first.longitude - second.longitude)
    return (
        abs(first.latitude - second.latitude) >= MIN_SEPARATION
        or min(longitude, 360 - longitude) >= MIN_SEPARATION
    )


class TrafficSequencer:
    """One shared queue, serialized by SQLite, surviving application restarts."""

    def __init__(self, db: MessageDatabase):
        self.db = db
        self.store = AircraftStore(db)
        self.connection = db.connection
        with self.connection:
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS traffic_processed_messages (
                    message_id INTEGER PRIMARY KEY REFERENCES messages(id)
                )
            """)

    def snapshot(self) -> dict:
        record = self.db.get_shared_state(STATE_KEY)
        return record["payload"] if record else {"queue": [], "unsafe_pairs": []}

    def _save(self, key, payload, category):
        self.connection.execute("""
            INSERT INTO shared_state (key, category, payload) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,
                category=excluded.category, updated_at=CURRENT_TIMESTAMP
        """, (key, category, json.dumps(payload)))

    def _emit(self, recipient, content, metadata):
        # Use this transaction rather than send_message(), which commits itself.
        self.connection.execute("""
            INSERT INTO messages
                (sender_team, recipient_team, content, message_type, metadata)
            VALUES ('tower', ?, ?, 'function', ?)
        """, (recipient.value, content, json.dumps(metadata)))

    def _publish(self, state):
        for position, aircraft_id in enumerate(state["queue"], 1):
            self._save(f"decision:{aircraft_id}", {
                "aircraft_id": aircraft_id, "status": "queued", "safe": False,
                "runway_id": None, "queue_position": position,
                "sequenced": True,
            }, "decision")
        # Remove obsolete queued decisions after completion/deletion/landing.
        for row in self.db.list_shared_state(category="decision"):
            payload = row["payload"]
            if payload.get("sequenced") and payload.get("status") == "queued" and payload.get("aircraft_id") not in state["queue"]:
                self.connection.execute("DELETE FROM shared_state WHERE key=?", (row["key"],))
        self._save(STATE_KEY, state, "traffic")

    def _request(self, state, aircraft_id):
        if not isinstance(aircraft_id, str) or not aircraft_id.strip():
            raise ValueError("Aircraft ID must be a nonempty string")
        aircraft = self.store.get(aircraft_id)
        if aircraft is None or aircraft.altitude <= 0:
            raise ValueError(f"Aircraft {aircraft_id!r} must be registered and airborne")
        if aircraft_id not in state["queue"]:
            state["queue"].append(aircraft_id)

    def request_landing(self, aircraft_id: str) -> dict:
        """Append once in request order; retries retain the current position."""
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            state = self.snapshot()
            self._request(state, aircraft_id)
            self._publish(state)
        return state

    def complete_request(self, aircraft_id: str) -> dict:
        """Remove a served/cancelled request and compact remaining positions."""
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            state = self.snapshot()
            state["queue"] = [item for item in state["queue"] if item != aircraft_id]
            self._publish(state)
        return state

    def process_radar_messages(self) -> list[dict]:
        """Consume landing_request/traffic_conflict functions exactly once.

        Inbox read status is independent of processing. Malformed recognized
        functions are rejected and reported without blocking later requests.
        """
        results = []
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            state = self.snapshot()
            airborne = {item.id for item in self.store.list() if item.altitude > 0}
            state["queue"] = [item for item in state["queue"] if item in airborne]
            rows = self.connection.execute("""
                SELECT * FROM messages WHERE sender_team='radar'
                    AND recipient_team='tower' AND message_type='function'
                    AND id NOT IN (SELECT message_id FROM traffic_processed_messages)
                ORDER BY id
            """).fetchall()
            for row in rows:
                metadata = json.loads(row["metadata"]) if row["metadata"] else {}
                if not isinstance(metadata, dict):
                    continue
                function = metadata.get("function")
                if not isinstance(function, str) or function not in {"landing_request", "traffic_conflict"}:
                    continue
                result = {"source_message_id": row["id"], "aircraft_id": metadata.get("aircraft_id")}
                try:
                    if function == "landing_request":
                        self._request(state, metadata.get("aircraft_id"))
                    else:
                        aircraft_id = metadata.get("aircraft_id")
                        reason = metadata.get("reason")
                        if metadata.get("emergency", False) is not False:
                            raise ValueError("Emergency conflicts require separate handling")
                        if not isinstance(reason, str) or reason not in PRIORITY_STEPS:
                            raise ValueError("Unknown non-emergency conflict reason")
                        if aircraft_id not in state["queue"]:
                            raise ValueError("Aircraft has no pending landing request")
                        old = state["queue"].index(aircraft_id)
                        new = max(0, old - PRIORITY_STEPS[reason])
                        event = {**result, "function": "traffic_conflict", "reason": reason,
                                 "old_position": old + 1, "queue_position": new + 1}
                        self._emit(Team.COMMAND, "Conflict", {**event, "status": "active"})
                        state["queue"].insert(new, state["queue"].pop(old))
                        for recipient in (Team.RADAR, Team.COMMAND):
                            self._emit(recipient, "Resolved", {**event, "status": "resolved"})
                    result["status"] = "resolved"
                except (ValueError, TypeError) as error:
                    result.update(status="rejected", error=str(error))
                    self._emit(Team.COMMAND, "Conflict rejected", {"function": function, **result})
                self.connection.execute(
                    "INSERT INTO traffic_processed_messages VALUES (?)", (row["id"],)
                )
                self.connection.execute(
                    "UPDATE messages SET read_at=COALESCE(read_at, CURRENT_TIMESTAMP) WHERE id=?", (row["id"],)
                )
                results.append(result)
            self._publish(state)
        return results

    def refresh(self) -> dict:
        """Detect pair transitions, issue +/-10 degree instructions, prune queue."""
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            state = self.snapshot()
            airborne = {item.id: item for item in self.store.list() if item.altitude > 0}
            state["queue"] = [item for item in state["queue"] if item in airborne]
            unsafe = [
                [first.id, second.id]
                for first, second in combinations(airborne.values(), 2)
                if not has_safe_separation(first, second)
            ]
            previous = state["unsafe_pairs"]
            for pair in unsafe:
                if pair in previous:
                    continue
                first, second = (airborne[item] for item in pair)
                event = {"function": "unsafe_separation", "aircraft_ids": pair, "status": "active"}
                self._emit(Team.COMMAND, "Unsafe Separation", event)
                # Move the second aircraft away from the first on both axes.
                # Near a pole, choose the inward direction to stay in bounds.
                latitude_delta = 10 if second.latitude >= first.latitude else -10
                if not -90 <= second.latitude + latitude_delta <= 90:
                    latitude_delta *= -1
                longitude_delta = 10 if (second.longitude - first.longitude) % 360 < 180 else -10
                target = {
                    "latitude": second.latitude + latitude_delta,
                    "longitude": (second.longitude + longitude_delta + 180) % 360 - 180,
                }
                self._emit(Team.RADAR, "Change latitude and longitude for unsafe separation", {
                    **event, "function": "change_position", "aircraft_id": second.id,
                    "latitude_delta": latitude_delta, "longitude_delta": longitude_delta,
                    "target": target,
                })
            for pair in previous:
                if pair not in unsafe:
                    event = {"function": "unsafe_separation", "aircraft_ids": pair, "status": "resolved"}
                    self._emit(Team.COMMAND, "Resolved", event)
            state["unsafe_pairs"] = unsafe
            self._publish(state)
        return state

    def check_clearance(self, aircraft_id: str) -> None:
        """Apply R2 ordering/separation to aircraft enrolled in the R2 queue."""
        if aircraft_id not in self.snapshot()["queue"]:
            return
        state = self.refresh()
        if aircraft_id not in state["queue"]:
            raise ValueError("Aircraft must be registered and airborne for clearance")
        if aircraft_id in state["queue"]:
            if state["queue"][0] != aircraft_id:
                raise ValueError("Aircraft must wait for its turn in the landing queue")
            if any(aircraft_id in pair for pair in state["unsafe_pairs"]):
                raise ValueError("Unsafe separation must be resolved before clearance")
