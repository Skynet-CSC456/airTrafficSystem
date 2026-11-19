"""Read Tower's messages to Radar, each one once, and stamp Radar's outgoing messages.

Used by REQ-RAD-006 to REQ-RAD-009. Each consumer keeps its own "processed
through" message ID in shared state, so progress survives restarts and does not
depend on whether a person has read Radar's inbox. Messages are read by ID
rather than through ``receive_messages``, whose oldest-first limit would hide
new messages once Radar's inbox grows large.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone

from src.db import MessageDatabase, Team

SCHEMA_VERSION = 1  # Tower's versioned message contract (REQ-TWR-009 to REQ-TWR-012)


def utc_timestamp(now: datetime | None = None) -> str:
    """Timezone-aware UTC timestamp, as Tower requires on versioned messages."""
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def send_to_tower(db: MessageDatabase, message_type: str, content: str, fields: dict, *, now: datetime | None = None) -> int:
    """Send Tower a versioned message: ``schema_version`` and ``timestamp`` plus ``fields``."""
    metadata = {"schema_version": SCHEMA_VERSION, **fields, "timestamp": utc_timestamp(now)}
    return db.send_message(Team.RADAR, Team.TOWER, content, message_type=message_type, metadata=metadata)


def unprocessed_tower_messages(db: MessageDatabase, processed_key: str, message_types: tuple[str, ...]) -> Iterator[tuple[int, str, dict]]:
    """Yield (message_id, message_type, metadata) for Tower messages to Radar not yet processed.

    Oldest first, only the given message types. The processed-through ID
    advances as each message is yielded, so a message is handed out once even if
    the caller skips it.
    """
    record = db.get_shared_state(processed_key)
    processed_through = record["payload"]["message_id"] if record else 0
    placeholders = ", ".join("?" * len(message_types))
    rows = db.connection.execute(
        f"""
        SELECT id, message_type, metadata FROM messages
        WHERE sender_team = 'tower' AND recipient_team = 'radar'
          AND message_type IN ({placeholders}) AND id > ?
        ORDER BY id
        """,
        (*message_types, processed_through),
    ).fetchall()
    for row in rows:
        try:
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        except (TypeError, ValueError):
            metadata = {}
        db.upsert_shared_state(processed_key, {"message_id": row["id"]}, category="radar")
        yield row["id"], row["message_type"], metadata if isinstance(metadata, dict) else {}
