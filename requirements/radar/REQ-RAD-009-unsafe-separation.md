# REQ-RAD-009: Support Tower's "Unsafe Separation" messages

When Tower detects that an aircraft is too close to another, it sends Radar an instruction with the coordinates to move to (REQ-TWR-011). Radar shall move the aircraft and tell Tower the separation is resolved.

## Acceptance criteria

- Radar applies each `change_position` message from Tower (`aircraft_id`, `latitude`, `longitude`, `observed_at`, `conflict_id`) once, oldest first.
- An instruction whose `observed_at` no longer matches the aircraft's latest observation is discarded, as Tower asks.
- The aircraft is moved through Radar's normal update path, so the move is validated and passes the stale-update check (its timestamp is newer than the aircraft's last update).
- Only the position changes; status, altitude, assigned runway, delay and fuel are kept.
- After the move, Radar sends Tower a version-1 `unsafe_separation_resolved` message with exactly `schema_version`, `event_id`, `timestamp` and `conflict_id`. Tower clears the alert once Radar's positions are safe.
- Instructions for unknown aircraft, with missing, non-numeric or out-of-range coordinates or no conflict ID, or from other teams are skipped and not retried; later instructions are still applied.
- Radar's background pass (`radar_tick`) carries out new instructions every second in the demo.

## Traceability

- Source: src/radar/separation.py, src/radar/tower_inbox.py, src/radar/runway_operations.py (radar_tick)
- Tests: tests/radar/test_separation.py
