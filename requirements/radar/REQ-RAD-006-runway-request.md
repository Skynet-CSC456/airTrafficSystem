# REQ-RAD-006: Request a runway and record Tower's assignment

Radar shall ask Tower for a runway to land or take off, and record the runway Tower assigns on the aircraft.

## Acceptance criteria

### Requesting a runway

- Radar can request a runway only for an aircraft it is tracking; empty or unknown aircraft IDs are rejected.
- The request type is "landing" or "departure". When it is not given, it follows the aircraft's status (Landing requests landing; Parked and Departing request departure).
- The request is sent to Tower as a message with `message_type="runway_request"` and metadata `aircraft_id` and `request_type`.
- The request is published to shared state as pending so the Command dashboard can show it.
- An invalid request sends and stores nothing.

### Recording Tower's assignment

- Each aircraft has an optional runway ID; new aircraft have none.
- Radar reads Tower's runway assignments, the `function` messages with `function="assign_runway"` defined in REQ-TWR-006, and records the runway on the aircraft and in its shared state.
- Assignments are applied once each, oldest first, so the latest assignment wins.
- Applying assignments does not change read/unread status in Radar's inbox.
- Assignments for unknown aircraft or without an aircraft or runway ID are skipped.
- When an assignment is recorded, the aircraft's pending request is marked answered with the runway.
- Position updates keep the assigned runway.
- Databases created before this requirement gain the runway column automatically.


## Out of scope

- Tower answering "no runway available": Tower does not send such a message yet (REQ-TWR-006).
- Tower acting on `runway_request` messages: Tower currently chooses airborne aircraft itself (REQ-TWR-006).

## Traceability

- Source: src/radar/aircraft.py (`AircraftState.runway_id`, `AircraftStore.assign_runway`), src/radar/messaging.py, messaging_demo.py
- Tests: tests/radar/test_aircraft_runway.py, tests/radar/test_radar_messaging.py
- Adapted from the Radar-Requirement-1-Option2 branch (`aircraft_state.py`, `messaging.py`) onto the existing aircraft model.
