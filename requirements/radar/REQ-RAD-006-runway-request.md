# REQ-RAD-006: Request a runway and record Tower's answers

Radar shall ask Tower for a runway to land or take off using Tower's version-1 request protocol (REQ-TWR-009), and record each answer Tower sends.

## Acceptance criteria

### Requesting a runway

- Radar can request a runway only for an aircraft it is tracking; empty or unknown aircraft IDs are rejected.
- The request type is "landing" or "departure". When it is not given, it follows the aircraft's status (Landing and Airborne request landing; Parked and Departing request departure).
- The request goes to the one airport Tower has configured (REQ-TWR-007), or to an airport named by the caller. With no airport, or several and none named, nothing is sent.
- The request is sent as a `runway_request` message with metadata `schema_version` 1, a new `request_id`, `aircraft_id`, `airport_id`, `operation` and a timezone-aware UTC `timestamp`.
- The request is recorded as pending in shared state (`request:<aircraft_id>`) so the Command dashboard can show it.
- An invalid request sends and stores nothing.

### Recording Tower's answers

- Radar reads Tower's `runway_response` messages (cleared, queued, rejected), `runway_completed` events, and the legacy Tower menu's `assign_runway` function messages (REQ-TWR-006).
- Cleared: the runway is recorded on the aircraft and in its shared state, with the time Radar recorded it.
- Queued: the queue position is recorded. Rejected: the reason is recorded, and a cancelled clearance frees the aircraft's runway. Completed: the request is marked completed (see REQ-RAD-007).
- Answers for Radar's current request are applied; a clearance without a Radar request (the Tower menu) is applied too; other answers to requests Radar no longer tracks are ignored.
- Each answer is applied once, oldest first. Applying answers does not change read/unread status in Radar's inbox.
- Malformed answers and answers for unknown aircraft are skipped and not retried.
- Position updates keep the assigned runway. Databases created before this requirement gain the runway column automatically.

## Traceability

- Source: src/radar/messaging.py, src/radar/aircraft.py (`AircraftState.runway_id`, `AircraftStore.assign_runway`), src/radar/tower_inbox.py, messaging_demo.py (Radar option 5)
- Tests: tests/radar/test_radar_messaging.py, tests/radar/test_aircraft_runway.py
- Contract: docs/tower-integration.md (request/response version 1)
