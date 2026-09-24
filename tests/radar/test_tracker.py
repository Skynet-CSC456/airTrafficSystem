from src.radar.tracker import REQUIREMENT_ID, classify_track


def test_radar_tracking_requirement_id_and_behavior():
    assert REQUIREMENT_ID == "REQ-RAD-001"
    assert classify_track(8_500) == "stable"
    assert classify_track(10_000) == "monitor"
