# REQ-RAD-007: Carry out a cleared runway operation and report it

When Tower clears an aircraft for a runway, Radar shall carry out the landing or take-off, record the result, and tell Tower the operation has finished, so Tower can make the runway available again (REQ-TWR-012, R2 additional requirement: 30-second delay after runway assignment).

## Acceptance criteria

- The operation takes 30 seconds from when Radar records the clearance (`RUNWAY_OPERATION_SECONDS`), matching Tower's 30-second release delay; until then the aircraft stays on its runway.
- Landing: the aircraft becomes Parked at 0 m and moves to the first free parking stand (one no other aircraft is on); when every stand is taken it shares the last one.
- Departure: the aircraft is airborne (Departing, at least 300 m) above the runway.
- The aircraft's runway is cleared, and Radar sends Tower a version-1 `operation_finished` message (`schema_version`, `event_id`, `timestamp`, `request_id`, `aircraft_id`, `operation`). Each operation is finished and reported once, even with several Radar processes.
- Tower releases the runway once it has the report and its own 30-second hold has passed, and sends `runway_completed`. A completed departure has left Radar's airspace: Radar stops tracking the aircraft. Landed aircraft stay tracked.
- Aircraft without a current clearance, or whose operation time has not passed, are unchanged.
- Radar's background pass records Tower's decisions before finishing operations.

## Command dashboard

- While its operation is under way, an aircraft is drawn on its assigned runway; afterwards it is drawn at its position (a landed aircraft at its parking stand). The parking stands are drawn on the map.
- Decisions for departed aircraft are no longer listed; completed operations leave the landing queue.

## Traceability

- Source: src/radar/runway_operations.py, src/radar/messaging.py (completed departures), src/command/dashboard.py, messaging_demo.py (background Radar tracker)
- Tests: tests/radar/test_runway_operations.py, tests/command/test_dashboard.py
