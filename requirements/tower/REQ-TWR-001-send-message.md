# REQ-TWR-001: Send a message

Tower shall be able to send a message to another ATC subsystem (Radar or Command).

## Acceptance criteria

- Sending records Tower as the sender and the selected subsystem as the recipient.
- The recipient can retrieve the original content using the shared message database.
- Sending returns the message ID supplied by the database.
- Unknown recipients, Tower-to-Tower messages, and blank content are rejected using the existing database validation.

## Traceability

- Source: `src/tower/messaging.py` — `send_message(db, recipient, content)`
- Tests: `tests/tower/test_messaging.py` — tests marked `REQ-TWR-001`
- Persistence and validation: reuse `MessageDatabase` and `Team` from `src/db.py`.
