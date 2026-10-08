from src.radar.availability import has_radar_signal, is_radar_available


def test_radar_is_available():
    # REQ-RAD-002
    assert is_radar_available() is True


def test_radar_has_signal():
    # REQ-RAD-003
    assert has_radar_signal() is True
