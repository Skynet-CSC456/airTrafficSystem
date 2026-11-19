| ID          | Team    | Description                                          | Code                                         | Tests                                          | Metrics |
| ----------- | ------- | ---------------------------------------------------- | -------------------------------------------- | ---------------------------------------------- | ------- |
| REQ-RAD-001 | Radar   | Ingest & Update Aircraft Status                      | src/radar/aircraft.py                        | tests/radar/test_position_update.py            | N/A     |
| REQ-RAD-002 | Radar   | Radar availability check                             | src/radar/availability.py:is_radar_available | tests/radar/test_availability.py (REQ-RAD-002) | N/A     |
| REQ-RAD-003 | Radar   | Radar signal check                                   | src/radar/availability.py:has_radar_signal   | tests/radar/test_availability.py (REQ-RAD-003) | N/A     |
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
| REQ-RAD-006 | Radar   | Request a runway from Tower (v1 runway_request) and record Tower's responses | src/radar/messaging.py, src/radar/aircraft.py | tests/radar/test_radar_messaging.py, tests/radar/test_aircraft_runway.py | N/A     |
| REQ-RAD-007 | Radar   | Carry out a cleared landing/departure, report operation_finished, stop tracking departed aircraft | src/radar/runway_operations.py | tests/radar/test_runway_operations.py | N/A     |
| REQ-RAD-008 | Radar   | Detect conflicts (2h/4h late, low fuel) and signal Tower every second until resolved | src/radar/conflicts.py | tests/radar/test_conflicts.py | N/A     |
| REQ-RAD-009 | Radar   | Move aircraft on Tower's unsafe-separation instruction and report it resolved | src/radar/separation.py | tests/radar/test_separation.py | N/A     |
| REQ-TWR-005 | Tower   | Safe runway assignment and landing clearance          | src/tower/clearance.py                       | tests/tower/test_clearance_safety.py            | N/A     |
| REQ-TWR-006 | Tower   | Assign runway to airborne aircraft via function message | src/tower/messaging.py:assign_runway_to_aircraft | tests/tower/test_assign_runway.py          | N/A     |
| REQ-CMD-004 | Command | Unified dashboard with normal/degraded system state   | src/command/dashboard.py                     | tests/command/test_dashboard.py                | N/A     |
| REQ-TWR-007 | Tower | Authoritative Airport and runway CRUD | src/tower/airport.py | tests/tower/test_airport.py; tests/tower/test_service.py | N/A |
| REQ-TWR-008 | Tower | Atomic request lifecycle and persistent FIFO | src/tower/service.py | tests/tower/test_service.py | N/A |
| REQ-TWR-009 | Tower | Versioned Radar request processing and atomic responses | src/tower/messaging.py | tests/tower/test_service.py | N/A |
| REQ-TWR-010 | Tower | R2 nonemergency queue prioritization and resolution | src/tower/r2.py | tests/tower/test_r2.py | 1, 2 or 3 positions |
| REQ-TWR-011 | Tower | R2 classroom separation detection and correlated Command alerts | src/tower/r2.py | tests/tower/test_r2.py | 10 degrees on either axis |
| REQ-TWR-012 | Tower | R2 earliest runway release after assignment | src/tower/r2.py | tests/tower/test_r2.py | 30 seconds |
