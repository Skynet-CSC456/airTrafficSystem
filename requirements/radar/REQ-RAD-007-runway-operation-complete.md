# REQ-RAD-007: Complete an aircraft's runway operation

When Tower's hold on an aircraft's assigned runway ends, Radar shall treat the runway operation as finished: a landing aircraft is parked, and a departing aircraft has left the airspace.

## Acceptance criteria

- Only aircraft whose recorded runway (REQ-RAD-006) matches Tower's cleared decision, with the runway hold ended, are changed.
- A landing aircraft becomes Parked at 0 m, keeps its position, and its runway is cleared so it is not processed again.
- A departing aircraft is no longer tracked (deleted, with its live state).
- Whether an aircraft is landing or departing follows Radar's runway request; without one, it follows the aircraft's status.
- Aircraft still holding their runway, and aircraft without a runway, are unchanged.
- Radar's background pass records new Tower assignments before completing runway operations.

## Command dashboard

- While its runway hold is active, an aircraft is drawn on its assigned runway.
- After the hold ends, the aircraft is no longer drawn on the map; landed aircraft remain in the aircraft table.
- Decisions for departed aircraft are no longer listed.
- Once its runway hold has ended, an aircraft leaves the landing queue; its decision stays in the decisions list.

## Traceability

- Source: src/radar/runway_operations.py, src/command/dashboard.py, messaging_demo.py (background Radar tracker)
- Tests: tests/radar/test_runway_operations.py, tests/command/test_dashboard.py
