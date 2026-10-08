| ID | Team | Description | Code | Tests | Metrics |
|----|----|----|----|----|----|
| REQ-RAD-001 | Radar | Radar tracking and surveillance | TBD | TBD | TBD |
| REQ-RAD-002 | Radar | Radar availability check | src/radar/availability.py:is_radar_available | tests/radar/test_availability.py (REQ-RAD-002) | N/A |
| REQ-RAD-003 | Radar | Radar signal check | src/radar/availability.py:has_radar_signal | tests/radar/test_availability.py (REQ-RAD-003) | N/A |
| REQ-TWR-001 | Tower | Send a message to another subsystem | src/tower/messaging.py:send_message | tests/tower/test_messaging.py (REQ-TWR-001) | N/A |
| REQ-TWR-002 | Tower | Receive messages addressed to Tower | src/tower/messaging.py:receive_messages | tests/tower/test_messaging.py (REQ-TWR-002) | N/A |
| REQ-TWR-003 | Tower | Check aircraft clearance | src/tower/clearance.py | tests/tower/test_clearance.py | N/A |
| REQ-CMD-001 | Command | Command and control flow | TBD | TBD | TBD |
| REQ-COM-002 | Command | Command availability check | src/command/availability.py | tests/command/test_availability.py | N/A |
| REQ-RAD-002 | Radar | Radar availability | src/radar/availability.py | tests/radar/test_availability.py | N/A |
| REQ-RAD-003 | Radar | Radar updates aircraft position data every 5 seconds | src/radar/position_update.py | tests/radar/test_position_update.py | TBD |
