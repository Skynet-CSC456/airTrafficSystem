# REQ-RAD-001: Radar tracking and surveillance

The radar subsystem shall provide track status classification for aircraft based on the reported altitude.

## Acceptance criteria
- The system shall classify altitude values below 10,000 ft as "stable".
- The system shall classify altitude values at or above 10,000 ft as "monitor".
- The classification logic shall be covered by an automated test.
