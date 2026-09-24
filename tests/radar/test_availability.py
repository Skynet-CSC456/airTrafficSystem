from src.radar.availability import is_radar_available


def test_radar_is_available():
    # REQ-RAD-002
    assert is_radar_available() is True
