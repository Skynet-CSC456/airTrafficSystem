# REQ-TWR-002: Receive messages

Tower shall be able to receive messages addressed to Tower from other ATC subsystems.

## Acceptance criteria

- Receiving returns messages sent to Tower by Radar and Command, preserving sender and content.
- Messages addressed to other subsystems are excluded.
- An empty Tower inbox returns an empty list.
- The existing database options for unread filtering, marking messages as read, and result limits are available and retain their database behavior.
- Invalid limits are rejected using the existing database validation.

## Traceability

- Source: `src/tower/messaging.py` — `receive_messages(db, *, unread_only=False, limit=100, mark_as_read=False)`
- Tests: `tests/tower/test_messaging.py` — tests marked `REQ-TWR-002`
- Persistence and validation: reuse `MessageDatabase` and `Team` from `src/db.py`.
