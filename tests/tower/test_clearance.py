# REQ-TWR-003
from src.tower.clearance import is_aircraft_cleared


def test_cleared_status_returns_true():
    assert is_aircraft_cleared("cleared") is True


def test_waiting_status_returns_false():
    assert is_aircraft_cleared("waiting") is False
