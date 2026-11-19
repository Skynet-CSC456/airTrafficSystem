# REQ-TWR-007: R2 traffic sequencing

Tower manages a persistent landing request queue and separation using the R2
high-level requirements supplied in the project slides.

## Acceptance criteria

- Start requests in FIFO order. Duplicate pending requests retain their position.
- Process Radar non-emergency conflict messages: move an aircraft toward the head
  by one place for `late_2_hours`, two for `late_4_hours`, or three for `low_fuel`.
  Clamp at position one and preserve the relative order of other aircraft.
- Send Command a `Conflict` message, then send Radar and Command `Resolved`
  messages with the resulting position. Process each message once across restarts,
  independently of whether a person has read the inbox.
- Reject unsupported reasons, unknown/nonqueued aircraft, malformed requests,
  and emergency conflicts without reordering the queue. Report rejection to
  Command and continue processing subsequent messages. Emergency handling is
  outside these non-emergency R2 rules.
- Under the slides' simulation rule, a pair of airborne aircraft is unsafe when
  both latitude difference and shortest longitude difference are less than 10
  degrees. Exactly 10 degrees on either axis is safe. Ground aircraft are excluded.
- On a new unsafe pair, send Command `Unsafe Separation` and Radar a
  `change_position` function containing signed ten-degree changes on both axes
  and valid target coordinates. Sending an instruction does not move aircraft.
- Resolve an unsafe pair only when a later scan observes safe separation or
  either aircraft is no longer airborne/present. Send Command `Resolved` once;
  repeated scans do not duplicate alerts. A subsequent incursion alerts again.
- Only the head of the R2 queue can receive clearance, and only after its unsafe
  pairs resolve and a safe runway is available. Both persisted clearance paths
  enforce this guard for enrolled aircraft.
- Remove served/cancelled requests and prune landed/deleted aircraft on refresh;
  compact positions so the queue can drain rather than accumulating stale entries.
- Command shows current queue positions and unsafe pair details, and reports a
  degraded state while separation alerts remain active.

## Interpretation and integration

The slides' `+(1/2/3) in queue` is interpreted as promotion toward the front,
not increasing the numeric position. Priority changes are per conflict message;
there is no additional emergency or scheduling policy inferred from the slides.
The ten-degree coordinate rule is a project simulation rule.

`TrafficSequencer.request_landing()` enrolls an aircraft. Radar can also send a
`function` message to Tower with `function="landing_request"` and `aircraft_id`.
Conflict messages use `function="traffic_conflict"`, `aircraft_id`, `reason`, and
optional `emergency=false`. `process_radar_messages()` handles these functions;
unrelated inbox messages are left untouched. Changes and outbound messages commit
in one SQLite transaction. The Tower messaging demo processes these messages each
menu cycle. Accepted Radar updates automatically refresh separation; explicit
`refresh()` also reconciles aircraft creation/deletion and grounded aircraft.

Radar must execute the supplied position instructions through its normal update
path. Targets are per-pair instructions, not a global route planner; conflicts
involving other aircraft remain active until observed safe. Legacy callers that
do not enroll in the R2 queue retain their existing direct clearance behavior.

## Traceability

- Source: src/tower/sequencing.py, src/tower/messaging.py, src/tower/clearance.py,
  src/radar/position_update.py, src/command/dashboard.py, messaging_demo.py
- Tests: tests/tower/test_sequencing.py
