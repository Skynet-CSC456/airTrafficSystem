"""Tower sequencing logic for REQ-TWR-001."""

REQUIREMENT_ID = "REQ-TWR-001"


def sequence_arrivals(arrivals: list[int]) -> list[int]:
    """Return runway arrivals in ascending order."""
    return sorted(arrivals)
