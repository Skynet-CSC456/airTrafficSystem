# REQ-RAD-008: Detect potential conflicts and signal them to Tower

Radar shall detect non-emergency conflicts from each aircraft's reported delay and fuel, and send them to Tower continuously until Tower resolves them (REQ-TWR-010).

## Acceptance criteria

- Each aircraft can report `delay_hours` (0 or more) and `fuel_percent` (0 to 100), set when Radar creates or updates it. Unreported values (None) raise no conflict, and updates that do not report them keep the stored values.
- Conflicts: late by 2 hours or more is `delay_2h`; late by 4 hours or more is `delay_4h`, which replaces `delay_2h` (one lateness conflict that escalates); fuel below 25% is `low_fuel`. An aircraft can have a lateness and a low-fuel conflict at once.
- Conflicts are signalled only while the aircraft's landing request is queued at Tower (REQ-RAD-006), since Tower only re-orders queued landing requests.
- Each open conflict is sent to Tower once per second as a version-1 `conflict` message with exactly `schema_version`, `event_id`, `timestamp`, `request_id`, `aircraft_id`, `reason` and `emergency=false`. Every repeat of one conflict carries the same `event_id`; a stronger reason gets a new one. Several Radar processes share the "last sent" time, so they do not multiply the messages.
- On Tower's `conflict_resolved` with the conflict's event ID, Radar records the conflict as resolved and stops sending it. On a `tower_r2_rejected` for one of the conflict's messages, Radar records it as rejected and stops sending it. Other replies are ignored.
- A conflict is raised again only after its condition clears and returns (for example, fuel rises above 25% and drops again), when lateness escalates from 2 to 4 hours, or for a new request. Conflicts of an aircraft that is no longer queued are forgotten.
- Conflict signals are generated continuously by Radar's background pass (`radar_tick`, every second in the demo), and one pass handles hundreds of aircraft well within a second.

## Traceability

- Source: src/radar/conflicts.py, src/radar/aircraft.py, src/radar/tower_inbox.py, src/radar/runway_operations.py (radar_tick), messaging_demo.py (delay and fuel prompts)
- Tests: tests/radar/test_conflicts.py, tests/radar/test_radar_demo.py
