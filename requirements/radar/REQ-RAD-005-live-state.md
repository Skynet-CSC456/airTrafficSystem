# REQ-RAD-005: Shared live state for radar

Radar shall persist the latest aircraft state to the shared SQLite database so the Tower and Command subsystems can consume a single live view.

## Acceptance criteria

- Each aircraft update is stored as the latest state for that aircraft.
- The aircraft freshness timestamp remains available for dashboard and safety logic.
- A shared-state table stores the live state in a form usable by Command.

## Traceability

- Source: src/db.py, src/radar/aircraft.py
- Tests: tests/radar/test_shared_state.py
