| ID | Team | Description | Code | Tests | Metrics |
|----|----|----|----|----|----|
| REQ-RAD-001 | Radar | Radar tracking and surveillance | src/radar/tracker.py | tests/radar/test_tracker.py | stable/monitor classification |
| REQ-RAD-002 | Radar | Radar availability check | src/radar/availability.py | tests/radar/test_availability.py | N/A |
| REQ-RAD-003 | Radar | Aircraft position updates | src/radar/position-update.ts | tests/radar/position-update-test.ts | position refresh cadence |
| REQ-TWR-001 | Tower | Tower coordination and sequencing | src/tower/sequence.py | tests/tower/test_sequence.py | ascending runway order |
| REQ-CMD-001 | Command | Command and control flow | src/command/dispatch.py | tests/command/test_dispatch.py | priority-based dispatch |
| REQ-CMD-002 | Command | Command availability check | src/command/availability.py | tests/command/test_availability.py | pr testing |
