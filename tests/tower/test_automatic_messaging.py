"""REQ-TWR-008 / REQ-TWR-009: real Tower transport with test-only clients."""

import os
import subprocess
import sys
from contextlib import ExitStack, closing
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.db import Team, create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.tower.messaging import process_requests
from src.tower.service import TowerService


def _consume_tower_events(connection, team, latest):
    """Minimal simulated client: consume, parse and update client-local decisions."""
    events = []
    for message in connection.receive_messages(
        team, sender=Team.TOWER, unread_only=True, mark_as_read=True
    ):
        assert message.sender == Team.TOWER
        assert message.recipient == team
        payload = message.metadata  # Deserialized from real SQLite JSON by the transport.
        assert isinstance(payload, dict)
        assert type(payload["schema_version"]) is int
        assert payload["schema_version"] == 1
        assert payload["airport_id"] == "JFK"
        assert payload["status"] == payload["outcome"]
        assert datetime.fromisoformat(payload["timestamp"]).utcoffset() == timedelta(0)
        latest[payload["request_id"]] = payload
        events.append((
            message.message_type, payload["request_id"], payload["aircraft_id"],
            payload["operation"], payload["outcome"], payload["runway_id"],
            payload["queue_position"],
        ))
    return events


@pytest.mark.parametrize("processor_mode", ["public_entrypoint", "worker_once"])
def test_automatic_tower_radar_command_message_cycle(tmp_path, processor_mode):
    path = str(tmp_path / "automatic-messaging.db")
    with ExitStack() as stack:
        producer, tower_connection, radar_consumer, command_consumer = [
            stack.enter_context(closing(create_database(path))) for _ in range(4)
        ]
        assert len({id(db.connection) for db in (
            producer, tower_connection, radar_consumer, command_consumer
        )}) == 4

        tower = TowerService(tower_connection)
        tower.airports.create_airport("JFK", 22000, 40.6413, -73.7781, ["R1", "R2"])
        assert [r["mode"] for r in tower.airports.list_runways("JFK")] == ["mixed", "mixed"]
        store = AircraftStore(producer)
        for aircraft_id, status, altitude in [
            ("ARRIVAL", "Landing", 500),
            ("DEPARTURE", "Parked", 0),
            ("WAITING", "Landing", 600),
        ]:
            store.create(AircraftState(
                aircraft_id, 40.65, -73.79, status, altitude, "2026-11-05T15:00:00"
            ))
        aircraft_before = store.list()

        def send_requests():
            for request_id, aircraft_id, operation in [
                ("land-first", "ARRIVAL", "landing"),
                ("depart", "DEPARTURE", "departure"),
                ("land-waiting", "WAITING", "landing"),
            ]:
                producer.send_message(
                    Team.RADAR, Team.TOWER, "Request runway clearance",
                    message_type="runway_request",
                    metadata={
                        "schema_version": 1, "request_id": request_id,
                        "aircraft_id": aircraft_id, "airport_id": "JFK",
                        "operation": operation, "timestamp": "2026-11-05T15:00:00+00:00",
                    },
                )

        def run_processor():
            if processor_mode == "public_entrypoint":
                process_requests(tower_connection, limit=3)
            else:
                subprocess.run(
                    [sys.executable, "-B", "-m", "src.tower.messaging",
                     "--database", path, "--once"],
                    cwd=Path(__file__).resolve().parents[2],
                    env={**os.environ, "SQLITE_DATABASE": path, "PYTHONDONTWRITEBYTECODE": "1"},
                    check=True, capture_output=True, text=True, timeout=10,
                )

        clients = [
            (radar_consumer, Team.RADAR, {}),
            (command_consumer, Team.COMMAND, {}),
        ]
        send_requests()
        run_processor()
        initial_events = [
            ("runway_response", "land-first", "ARRIVAL", "landing", "cleared", "R1", None),
            ("runway_response", "depart", "DEPARTURE", "departure", "cleared", "R2", None),
            ("runway_response", "land-waiting", "WAITING", "landing", "queued", None, 1),
        ]
        for connection, team, latest in clients:
            assert _consume_tower_events(connection, team, latest) == initial_events
            assert _consume_tower_events(connection, team, latest) == []
        assert clients[0][2] == clients[1][2]
        assert producer.receive_messages(Team.TOWER, unread_only=True) == []
        assert [r["aircraft_id"] for r in tower.airports.list_runways("JFK")] == [
            "ARRIVAL", "DEPARTURE"
        ]
        assert [r["request_id"] for r in tower.list_queue("JFK")] == ["land-waiting"]
        assert store.list() == aircraft_before

        # No unread input: polling again must not resend any events or change state.
        before_poll = tower.snapshot()
        run_processor()
        assert tower.snapshot() == before_poll
        for connection, team, latest in clients:
            assert _consume_tower_events(connection, team, latest) == []

        completed = tower.complete_request("land-first")
        transition_events = [
            ("runway_completed", "land-first", "ARRIVAL", "landing", "completed", "R1", None),
            ("runway_response", "land-waiting", "WAITING", "landing", "cleared", "R1", None),
        ]
        for connection, team, latest in clients:
            assert _consume_tower_events(connection, team, latest) == transition_events
            assert latest["land-first"]["outcome"] == "completed"
            assert latest["land-waiting"]["runway_id"] == "R1"
        assert clients[0][2] == clients[1][2]
        assert tower.list_queue("JFK") == []
        assert [r["aircraft_id"] for r in tower.airports.list_runways("JFK")] == [
            "WAITING", "DEPARTURE"
        ]

        # Actual duplicate messages, not merely another empty poll: replay after
        # completion/promotion must preserve two reservations and all decisions.
        before_replay = tower.snapshot()
        send_requests()
        run_processor()
        assert tower.complete_request("land-first") == completed
        assert tower.snapshot() == before_replay
        assert len(tower.snapshot()["requests"]) == 3
        assert producer.receive_messages(Team.TOWER, unread_only=True) == []
        for connection, team, latest in clients:
            assert _consume_tower_events(connection, team, latest) == []
        assert store.list() == aircraft_before



def test_ten_runways_twelve_requests_restart_promotion_and_added_runway(tmp_path):
    path = str(tmp_path / "twelve-aircraft.db")
    with ExitStack() as stack:
        producer, processor, radar, command = [
            stack.enter_context(closing(create_database(path))) for _ in range(4)
        ]
        tower = TowerService(processor)
        tower.airports.create_airport(
            "JFK", 22000, 40.6413, -73.7781, [f"R{i}" for i in range(1, 11)]
        )
        store = AircraftStore(producer)
        for i in range(12):
            operation = "landing" if i % 2 == 0 else "departure"
            store.create(AircraftState(
                f"AIR{i:02}", 40.65, -73.79,
                "Landing" if operation == "landing" else "Parked",
                500 if operation == "landing" else 0, "2026-11-05T15:00:00",
            ))
            producer.send_message(
                Team.RADAR, Team.TOWER, "Request runway clearance",
                message_type="runway_request",
                metadata={
                    "schema_version": 1, "request_id": f"req-{i:02}",
                    "aircraft_id": f"AIR{i:02}", "airport_id": "JFK",
                    "operation": operation, "timestamp": "2026-11-05T15:00:00+00:00",
                },
            )
        aircraft_before = store.list()
        process_requests(processor)
        states = {}
        for connection, team in [(radar, Team.RADAR), (command, Team.COMMAND)]:
            latest = {}
            events = _consume_tower_events(connection, team, latest)
            assert [event[4] for event in events] == ["cleared"] * 10 + ["queued"] * 2
            assert len({event[5] for event in events[:10]}) == 10
            assert [event[6] for event in events[10:]] == [1, 2]
            states[team] = latest
        assert states[Team.RADAR] == states[Team.COMMAND]
        before_restart = tower.snapshot()
        assert len(tower.list_queue("JFK")) == 2

    # Reopening preserves pending work, exclusive reservations and read acknowledgements.
    with ExitStack() as stack:
        producer, processor, radar, command = [
            stack.enter_context(closing(create_database(path))) for _ in range(4)
        ]
        tower = TowerService(processor)
        assert tower.snapshot() == before_restart
        assert process_requests(processor) == []
        for connection, team in [(radar, Team.RADAR), (command, Team.COMMAND)]:
            assert _consume_tower_events(connection, team, states[team]) == []
        released = tower.get_decision("req-00")["runway_id"]
        tower.complete_request("req-00")
        assert tower.get_decision("req-10")["runway_id"] == released
        assert tower.get_decision("req-10")["outcome"] == "cleared"
        assert [d["request_id"] for d in tower.list_queue("JFK")] == ["req-11"]
        for connection, team in [(radar, Team.RADAR), (command, Team.COMMAND)]:
            events = _consume_tower_events(connection, team, states[team])
            assert [(e[1], e[4]) for e in events] == [
                ("req-00", "completed"), ("req-10", "cleared"), ("req-11", "queued")
            ]
            assert events[1][5] == released
            assert events[2][6] == 1

        tower.airports.add_runway("JFK", "R11")
        tower.promote_queue("JFK")  # Explicit recheck after administrative configuration.
        for connection, team in [(radar, Team.RADAR), (command, Team.COMMAND)]:
            assert _consume_tower_events(connection, team, states[team]) == [
                ("runway_response", "req-11", "AIR11", "departure", "cleared", "R11", None)
            ]
        runways = tower.airports.list_runways("JFK")
        assert len(runways) == 11
        assert all(r["mode"] == "mixed" and not r["available"] for r in runways)
        assert len({r["request_id"] for r in runways}) == 11
        assert tower.list_queue("JFK") == []
        assert states[Team.RADAR] == states[Team.COMMAND]
        assert AircraftStore(producer).list() == aircraft_before
