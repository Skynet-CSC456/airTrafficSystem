# REQ-TWR-006: Assign a runway to an airborne aircraft via a function message

When Tower chooses to send a message, it can instead pick the "assign runway" function: select an aircraft that is in the air, select a runway, and have the assignment sent to Radar and Command as a `function` message.

## Acceptance criteria

- Only aircraft with altitude above ground are offered for assignment.
- The runway must be available and safe for the aircraft (REQ-TWR-005); otherwise nothing is sent or stored.
- On success, Radar and Command each receive a Tower message with `message_type="function"` and metadata `function`, `aircraft_id`, `runway_id` and `occupied_until`.
- The runway is marked occupied and the clearance decision is written to shared state so the Command dashboard reflects it.
- The messaging demo offers this function to Tower in addition to plain text messages.

## Traceability

- Source: src/tower/messaging.py, messaging_demo.py
- Tests: tests/tower/test_assign_runway.py
