"""Tower's versioned runway request envelope and response delivery."""

from datetime import datetime, timezone

from src.db import Team

R2_INPUT_TYPES = ('conflict', 'operation_finished', 'unsafe_separation_resolved')


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


def parse_r2_event(message):
    """Decode the proposed Radar-to-Tower R2 wire contract."""
    metadata = parse_request_metadata(message.metadata)
    common = ('event_id', 'timestamp', 'schema_version')
    fields = {
        'conflict': ('request_id', 'aircraft_id', 'reason', 'emergency'),
        'operation_finished': ('request_id', 'aircraft_id', 'operation'),
        'unsafe_separation_resolved': ('conflict_id',),
    }[message.message_type]
    if metadata.keys() != set(common + fields):
        raise ValueError('R2 event has missing or unexpected fields')
    for field in fields + ('event_id',):
        if field != 'emergency':
            value = metadata[field]
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f'Invalid R2 field {field}')
    if message.message_type == 'conflict' and metadata['emergency'] is not False:
        raise ValueError('R2 conflict must be explicitly nonemergency')
    return {**metadata, 'type': message.message_type}


def send_r2_message(db, recipient, message_type, content, payload):
    return db.send_message(Team.TOWER, recipient, content, message_type=message_type,
                           metadata={'schema_version': 1, **payload})
