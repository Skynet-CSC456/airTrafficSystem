# REQ-TWR-005: Safe runway assignment and landing clearance

Tower shall assign only safe runways and issue landing clearance only when a runway is available and compatible with the aircraft state.

## Acceptance criteria

- Unsafe runway assignments are rejected.
- A runway must be available before it can be selected.
- Clearance decisions are explicit and correct rather than opportunistic.

## Traceability

- Source: src/tower/clearance.py
- Tests: tests/tower/test_clearance_safety.py
