"""Command dispatch logic for REQ-CMD-001."""

REQUIREMENT_ID = "REQ-CMD-001"


def prioritize_flights(flights: list[dict]) -> list[dict]:
    """Return flights in descending priority order."""
    return sorted(flights, key=lambda flight: flight["priority"], reverse=True)
