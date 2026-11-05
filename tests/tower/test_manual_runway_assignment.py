from src.db import Team, create_database
from src.radar.aircraft import AircraftState, AircraftStore
from tower_assign_runway import (
    assign_manual_runway,
    ensure_runways_exist,
    handle_message,
    parse_manual_assignment,
)


def test_parse_manual_assignment_supports_rwypatterns():
    assert parse_manual_assignment("assign AC104 RWY 1 2500") == ("AC104", "RWY 1", 2500.0)
    assert parse_manual_assignment("approve AC202 RWY2 3200") == ("AC202", "RWY 2", 3200.0)


def test_assign_manual_runway_accepts_selected_runway_and_altitude():
    db = create_database(":memory:")
    try:
        ensure_runways_exist(db)
        store = AircraftStore(db)
        store.create(AircraftState("AC123", 40.6408, -73.7794, "Parked", 0, "2026-11-05T14:00:00"))

        assign_manual_runway(
            db,
            "AC123",
            "RWY 2",
            assigned_altitude=2400,
            request_type="departure",
        )

        state = store.get("AC123")
        assert state.status == "Airborne"
        assert state.latitude == 40.6428
        assert state.longitude == -73.7771
        assert state.altitude == 2400.0

        messages = db.receive_messages(Team.RADAR, unread_only=True, mark_as_read=True)
        assert len(messages) == 1
        assert "AC123 departure clearance approved on RWY 2" in messages[0].content
        assert "Assigned altitude 2400.0m" in messages[0].content
    finally:
        db.close()


def test_handle_message_keeps_departure_pending_for_manual_assignment():
    db = create_database(":memory:")
    try:
        pending_requests = {}
        handle_message(
            db,
            "AC222 is on the ground at altitude 0m and requests departure clearance.",
            pending_requests,
        )

        assert pending_requests["AC222"]["type"] == "departure"
        assert pending_requests["AC222"]["altitude"] == 0.0
    finally:
        db.close()
