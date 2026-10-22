# REQ-CMD-004: Unified command dashboard state

Command shall present a single dashboard that summarises aircraft, runway availability, and decisions from the shared system state.

## Acceptance criteria

- The dashboard is a single, unified view for the current system state.
- Normal and degraded conditions are clearly distinguished.
- Aircraft and runway status appear together with the active decision state.

## Traceability

- Source: src/command/dashboard.py
- Tests: tests/command/test_dashboard.py
