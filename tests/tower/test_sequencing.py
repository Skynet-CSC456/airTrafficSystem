"""REQ-TWR-007: R2 FIFO, conflict integration, separation, and queue draining."""

from dataclasses import asdict, replace

import pytest

import messaging_demo
from src.command.dashboard import build_dashboard_snapshot
from src.db import Team, create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.position_update import update_aircraft_position
from src.tower.messaging import assign_runway_to_aircraft
from src.tower.clearance import publish_runway_decision
from src.tower.sequencing import TrafficSequencer, has_safe_separation


@pytest.fixture
def db():
    database = create_database(":memory:")
    store = AircraftStore(database)
    for index in range(5):
        store.create(AircraftState(f"AC{index}", -60 + index * 25, 0,
                                   "Landing", 500, "2030-01-01T00:00:00"))
    yield database
    database.close()


def enqueue_all(db):
    sequencing = TrafficSequencer(db)
    for index in range(5):
        sequencing.request_landing(f"AC{index}")
    return sequencing


def conflict(db, reason, aircraft_id="AC4", **extra):
    return db.send_message(Team.RADAR, Team.TOWER, "Conflict", message_type="function",
                           metadata={"function": "traffic_conflict", "aircraft_id": aircraft_id,
                                     "reason": reason, **extra})


def test_fifo_retries_do_not_duplicate_or_reset_priority(db):
    sequencing = enqueue_all(db)
    assert sequencing.request_landing("AC1")["queue"] == [f"AC{i}" for i in range(5)]
    conflict(db, "low_fuel")
    sequencing.process_radar_messages()
    assert sequencing.request_landing("AC4")["queue"] == ["AC0", "AC4", "AC1", "AC2", "AC3"]


@pytest.mark.parametrize("reason,position", [("late_2_hours", 4), ("late_4_hours", 3), ("low_fuel", 2)])
def test_priority_movement_and_resolved_messages(db, reason, position):
    sequencing = enqueue_all(db)
    message_id = conflict(db, reason)
    assert sequencing.process_radar_messages()[0]["status"] == "resolved"
    queue = sequencing.snapshot()["queue"]
    assert queue.index("AC4") + 1 == position
    assert [item for item in queue if item != "AC4"] == ["AC0", "AC1", "AC2", "AC3"]
    command = db.receive_messages(Team.COMMAND)
    assert [message.content for message in command] == ["Conflict", "Resolved"]
    assert command[0].metadata["source_message_id"] == message_id
    assert command[1].metadata["queue_position"] == position
    assert db.receive_messages(Team.RADAR)[0].content == "Resolved"
    assert sequencing.process_radar_messages() == []
    assert len(db.receive_messages(Team.COMMAND)) == 2
    snapshot = build_dashboard_snapshot(db)
    queued = [item for item in snapshot["landing_queue"] if item["status"] == "queued"]
    assert [item["aircraft_id"] for item in queued] == queue
    assert [item["queue_position"] for item in queued] == list(range(1, 6))


def test_processing_does_not_depend_on_inbox_read_state_and_clamps_at_head(db):
    sequencing = enqueue_all(db)
    conflict(db, "low_fuel", "AC1")
    db.receive_messages(Team.TOWER, mark_as_read=True)
    sequencing.process_radar_messages()
    assert sequencing.snapshot()["queue"] == ["AC1", "AC0", "AC2", "AC3", "AC4"]


@pytest.mark.parametrize("reason,aircraft_id,extra", [
    ("late_2_hours", "AC4", {"emergency": True}),
    ("other", "AC4", {}), ("low_fuel", "missing", {}),
])
def test_invalid_conflicts_do_not_mutate_queue_or_block_valid_messages(db, reason, aircraft_id, extra):
    sequencing = enqueue_all(db)
    conflict(db, reason, aircraft_id, **extra)
    conflict(db, "late_2_hours")
    results = sequencing.process_radar_messages()
    assert [item["status"] for item in results] == ["rejected", "resolved"]
    assert sequencing.snapshot()["queue"] == ["AC0", "AC1", "AC2", "AC4", "AC3"]


def test_radar_request_protocol_and_unrelated_inbox_messages(db):
    sequencing = TrafficSequencer(db)
    text_id = db.send_message(Team.RADAR, Team.TOWER, "hello")
    other_id = db.send_message(Team.COMMAND, Team.TOWER, "Conflict", message_type="function",
                               metadata={"function": "traffic_conflict", "reason": "low_fuel", "aircraft_id": "AC4"})
    db.send_message(Team.RADAR, Team.TOWER, "Request landing", message_type="function",
                    metadata={"function": "landing_request", "aircraft_id": "AC4"})
    db.send_message(Team.RADAR, Team.TOWER, "Bad request", message_type="function",
                    metadata={"function": "landing_request", "aircraft_id": []})
    assert [item["status"] for item in sequencing.process_radar_messages()] == ["resolved", "rejected"]
    assert sequencing.snapshot()["queue"] == ["AC4"]
    assert [message.id for message in db.receive_messages(Team.TOWER, unread_only=True)] == [text_id, other_id]


def test_fifo_clearance_guard_and_queue_drains_without_gaps(db):
    sequencing = enqueue_all(db)
    with pytest.raises(ValueError, match="wait for its turn"):
        assign_runway_to_aircraft(db, "AC1", "RWY 1")
    assert db.get_shared_state("runway:RWY 1") is None
    assign_runway_to_aircraft(db, "AC0", "RWY 1")
    assert sequencing.snapshot()["queue"] == ["AC1", "AC2", "AC3", "AC4"]
    assert db.get_shared_state("decision:AC1")["payload"]["queue_position"] == 1
    assert db.get_shared_state("decision:AC0")["payload"]["status"] == "cleared"
    for aircraft_id in list(sequencing.snapshot()["queue"]):
        sequencing.complete_request(aircraft_id)
    assert sequencing.snapshot()["queue"] == []
    assert db.get_shared_state("decision:AC4") is None


def test_prune_landed_and_deleted_aircraft(db):
    sequencing = enqueue_all(db)
    store = AircraftStore(db)
    store.delete("AC0")
    landed = replace(store.get("AC1"), altitude=0, status="Parked", timestamp="2030-01-01T00:00:01")
    update_aircraft_position(store, asdict(landed))
    assert sequencing.snapshot()["queue"] == ["AC2", "AC3", "AC4"]
    assert db.get_shared_state("decision:AC0") is None
    assert db.get_shared_state("decision:AC2")["payload"]["queue_position"] == 1


@pytest.mark.parametrize("latitude,longitude,safe", [(0, 0, False), (9.99, 9.99, False),
    (10, 0, True), (0, -10, True), (-10, 0, True), (0, 10, True)])
def test_separation_boundary(latitude, longitude, safe):
    first = AircraftState("A", 0, 0, "Landing", 500, "2030-01-01T00:00:00")
    assert has_safe_separation(first, replace(first, id="B", latitude=latitude, longitude=longitude)) is safe


def test_separation_wraps_longitude_and_ignores_ground_aircraft(db):
    store = AircraftStore(db)
    first = replace(store.get("AC0"), latitude=89, longitude=179)
    second = replace(store.get("AC1"), latitude=89, longitude=-179)
    store.update(replace(first, timestamp="2030-01-01T00:00:01"))
    store.update(replace(second, timestamp="2030-01-01T00:00:01"))
    sequencing = TrafficSequencer(db)
    assert sequencing.refresh()["unsafe_pairs"] == [["AC0", "AC1"]]
    instruction = db.receive_messages(Team.RADAR)[0].metadata
    assert instruction["latitude_delta"] == -10
    assert instruction["target"] == {"latitude": 79, "longitude": -169}
    assert has_safe_separation(first, replace(second, **instruction["target"]))
    update_aircraft_position(store, asdict(replace(second, altitude=0, timestamp="2030-01-01T00:00:02")))
    assert sequencing.snapshot()["unsafe_pairs"] == []


def test_alert_lifecycle_requires_observed_resolution_and_blocks_clearance(db):
    store = AircraftStore(db)
    sequencing = TrafficSequencer(db)
    sequencing.request_landing("AC0")
    second = replace(store.get("AC1"), latitude=-59, longitude=1, timestamp="2030-01-01T00:00:01")
    assert update_aircraft_position(store, asdict(second))
    assert sequencing.snapshot()["unsafe_pairs"] == [["AC0", "AC1"]]
    assert build_dashboard_snapshot(db)["system_status"] == "degraded"
    with pytest.raises(ValueError, match="Unsafe separation"):
        assign_runway_to_aircraft(db, "AC0", "RWY 1")
    assert [message.content for message in db.receive_messages(Team.COMMAND)] == ["Unsafe Separation"]
    instruction = db.receive_messages(Team.RADAR)[0].metadata
    assert abs(instruction["latitude_delta"]) == abs(instruction["longitude_delta"]) == 10
    assert store.get("AC1") == second  # sending an instruction does not move aircraft
    assert not update_aircraft_position(store, asdict(second))  # stale/duplicate
    assert len(db.receive_messages(Team.RADAR)) == 1
    safe = replace(second, **instruction["target"], timestamp="2030-01-01T00:00:02")
    assert update_aircraft_position(store, asdict(safe))
    assert sequencing.snapshot()["unsafe_pairs"] == []
    assert [message.content for message in db.receive_messages(Team.COMMAND)] == ["Unsafe Separation", "Resolved"]
    sequencing.complete_request("AC0")
    assert build_dashboard_snapshot(db)["system_status"] == "normal"
    # A new incursion creates a new alert rather than remaining suppressed.
    update_aircraft_position(store, asdict(replace(second, timestamp="2030-01-01T00:00:03")))
    assert len(db.receive_messages(Team.RADAR)) == 2


def test_queue_and_message_deduplication_survive_restart(tmp_path):
    path = str(tmp_path / "sequencing.db")
    database = create_database(path)
    store = AircraftStore(database)
    for index in range(2):
        store.create(AircraftState(f"AC{index}", index * 20, 0, "Landing", 500, "2030-01-01T00:00:00"))
    sequencing = TrafficSequencer(database)
    sequencing.request_landing("AC0")
    sequencing.request_landing("AC1")
    conflict(database, "low_fuel", "AC1")
    sequencing.process_radar_messages()
    database.close()
    database = create_database(path)
    try:
        sequencing = TrafficSequencer(database)
        assert sequencing.snapshot()["queue"] == ["AC1", "AC0"]
        assert sequencing.process_radar_messages() == []
        assert len(database.receive_messages(Team.COMMAND)) == 2
    finally:
        database.close()


def test_demo_tower_can_request_fifo_landing(db, monkeypatch, capsys):
    answers = iter(["3", "AC2"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    messaging_demo.send_messages(db, Team.TOWER)
    assert TrafficSequencer(db).snapshot()["queue"] == ["AC2"]
    assert "Landing queue: AC2" in capsys.readouterr().out


def test_shared_state_clearance_cannot_bypass_queue(db):
    sequencing = enqueue_all(db)
    runways = [{"id": "RWY 1", "available": True, "mode": "landing"}]
    decision = publish_runway_decision(db, {"id": "AC1", "status": "Landing"}, runways)
    assert decision["status"] == "queued"
    assert decision["queue_position"] == 2
    assert runways[0]["available"] is True
    decision = publish_runway_decision(db, {"id": "AC0", "status": "Landing"}, runways)
    assert decision["status"] == "cleared"
    assert sequencing.snapshot()["queue"][0] == "AC1"
    decision = publish_runway_decision(db, {"id": "AC1", "status": "Landing"}, runways)
    assert decision["status"] == "queued"
    assert decision["queue_position"] == 1


def test_refresh_preserves_legacy_decisions(db):
    db.upsert_shared_state("decision:legacy", {"aircraft_id": "legacy", "status": "queued", "queue_position": 7}, category="decision")
    TrafficSequencer(db).refresh()
    assert db.get_shared_state("decision:legacy")["payload"]["queue_position"] == 7


def test_conflict_failure_rolls_back_messages_order_and_processed_marker(db, monkeypatch):
    sequencing = enqueue_all(db)
    conflict(db, "low_fuel")
    original = sequencing._emit

    def fail_resolution(recipient, content, metadata):
        if content == "Resolved":
            raise RuntimeError("Simulated delivery failure")
        original(recipient, content, metadata)

    monkeypatch.setattr(sequencing, "_emit", fail_resolution)
    with pytest.raises(RuntimeError, match="delivery failure"):
        sequencing.process_radar_messages()
    assert sequencing.snapshot()["queue"] == [f"AC{i}" for i in range(5)]
    assert db.receive_messages(Team.COMMAND) == []
    monkeypatch.setattr(sequencing, "_emit", original)
    assert sequencing.process_radar_messages()[0]["status"] == "resolved"
