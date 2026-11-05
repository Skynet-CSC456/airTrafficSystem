# REQ-TWR-009: Versioned Radar/Tower message processing

Tower shall process schema-version-1 Radar runway_request messages through the
existing SQLite transport and expose runway_response messages to Radar and Command.

- Correlate request_id, aircraft_id, airport_id, landing/departure operation and UTC timestamp.
- Return cleared/queued/rejected outcome, runway ID, queue position or reason.
- Track processing separately from human inbox read status.
- Commit processing acknowledgement only with request effects and outgoing messages.
- Leave failures retryable; preserve text and unrelated messages.
- Preserve the legacy airborne assignment menu/function-message interface as an
  adapter to the same service; completion now explicitly releases reservations.

Source: `src/tower/messaging.py`; lifecycle: `src/tower/service.py`.
Tests: `tests/tower/test_service.py`, `tests/tower/test_assign_runway.py`.
Contract and unimplemented Radar/Command consumer obligations: `docs/tower-integration.md`.
