"""Tower's versioned runway request envelope and response delivery."""

from datetime import datetime, timezone

from src.db import Team


def parse_request_metadata(metadata):
    """Check the wire envelope before the service validates operational fields."""
    if not isinstance(metadata, dict) or type(metadata.get("schema_version")) is not int or metadata["schema_version"] != 1:
        raise ValueError("Expected schema_version 1 metadata object")
    timestamp = metadata.get("timestamp")
    if not isinstance(timestamp, str):
        raise ValueError("Request timestamp must be timezone-aware UTC")
    moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset() != timezone.utc.utcoffset(moment):
        raise ValueError("Request timestamp must be timezone-aware UTC")
    return metadata


def send_to_clients(db, content, *, message_type, metadata):
    return {
        team.value: db.send_message(Team.TOWER, team, content, message_type=message_type, metadata=metadata)
        for team in (Team.RADAR, Team.COMMAND)
    }


def publish_decision(db, decision, *, legacy):
    """Send a persisted request's decision in the existing client format."""
    outcome = decision["outcome"]
    message_type = "runway_completed" if outcome == "completed" else "runway_response"
    metadata = dict(decision)
    if legacy and outcome == "cleared":
        message_type = "function"
        metadata["function"] = "assign_runway"
    content = f"{decision['operation']} {outcome}: {decision['aircraft_id']}"
    return send_to_clients(db, content, message_type=message_type, metadata=metadata)
