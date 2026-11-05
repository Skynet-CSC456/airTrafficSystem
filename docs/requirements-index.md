| ID          | Team    | Description                                          | Code                                         | Tests                                          | Metrics |
| ----------- | ------- | ---------------------------------------------------- | -------------------------------------------- | ---------------------------------------------- | ------- |
| REQ-RAD-001 | Radar   | Ingest & Update Aircraft Status                      | src/radar/aircraft.py                        | tests/radar/test_position_update.py            | N/A     |
| REQ-TWR-001 | Tower   | Send a message to another subsystem                  | src/tower/messaging.py:send_message          | tests/tower/test_messaging.py (REQ-TWR-001)    | N/A     |
| REQ-TWR-002 | Tower   | Receive messages addressed to Tower                  | src/tower/messaging.py:receive_messages      | tests/tower/test_messaging.py (REQ-TWR-002)    | N/A     |
| REQ-TWR-003 | Tower   | Check aircraft clearance                             | src/tower/clearance.py                       | tests/tower/test_clearance.py                  | N/A     |
| REQ-CMD-001 | Command | Command and control flow                             | TBD                                          | TBD                                            | TBD     |
| REQ-CMD-002 | Command | Command availability check                           | src/command/availability.py                  | tests/command/test_availability.py             | N/A     |
| REQ-CMD-003 | Command | Command availability check                           | src/command/availability.ts                  | tests/command/test_availability.ts             | N/A     |
| REQ-TWR-004 | Tower   | AirportUI                                            | src/tower/airportui.py                       | tests/tower/test_airportui.py                 | TBD     |
| REQ-RAD-002 | Radar   | Radar availability                                   | src/radar/availability.py                    | tests/radar/test_availability.py               | N/A     |
| REQ-RAD-003 | Radar   | Radar updates aircraft position data every 5 seconds | src/radar/position_update.py                 | tests/radar/test_position_update.py            | TBD     |
| REQ-RAD-004 | Radar   | Aircraft state CRUD and aircraft/Tower messaging     | src/radar/aircraft.py                        | tests/radar/test_position_update.py            | N/A     |
| REQ-RAD-005 | Radar   | Shared live state updates for real-time radar tracking | src/db.py and src/radar/aircraft.py         | tests/radar/test_shared_state.py               | N/A     |
| REQ-RAD-006 | Radar   | Request a runway and record Tower's assignment   | src/radar/messaging.py, src/radar/aircraft.py | tests/radar/test_radar_messaging.py, tests/radar/test_aircraft_runway.py | N/A     |
| REQ-RAD-007 | Radar   | Park landed aircraft, stop tracking departed ones after the runway hold | src/radar/runway_operations.py | tests/radar/test_runway_operations.py | N/A     |
| REQ-TWR-005 | Tower   | Safe runway assignment and landing clearance          | src/tower/clearance.py                       | tests/tower/test_clearance_safety.py            | N/A     |
| REQ-TWR-006 | Tower   | Assign runway (airborne or departing aircraft) via function message | src/tower/messaging.py:assign_runway_to_aircraft | tests/tower/test_assign_runway.py          | N/A     |
| REQ-CMD-004 | Command | Unified dashboard with normal/degraded system state   | src/command/dashboard.py                     | tests/command/test_dashboard.py                | N/A     |
