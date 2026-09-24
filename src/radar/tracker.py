"""Radar tracking logic for REQ-RAD-001."""

REQUIREMENT_ID = "REQ-RAD-001"


def classify_track(altitude_ft: int) -> str:
    """Classify a radar track based on altitude."""
    if altitude_ft < 10_000:
        return "stable"
    return "monitor"
