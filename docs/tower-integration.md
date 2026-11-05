# Tower integration and Session 8 modularity review

This implementation follows the modularity criteria supplied in the task: cohesive
responsibilities, information hiding, stable boundaries, low coupling, deterministic
tests and reuse. No separate Session 8 lecture document was supplied; this is not a
claim that additional lecture-specific requirements have been checked.

## Scope and provenance

- Worktree: `/Users/darienramdass/Downloads/airTrafficSystem-tower-restored-integration`.
- Branch: `feature/tower-restored-integration`.
- Base: freshly fetched `origin/main`, `2ef55655bc021d40e995f1034367cb4b68d62e42`.
- Restored from the November 1 session file-writing records, including both later
  compatibility fixes and the automatic messaging test. The recovered older
  `airTrafficSystem-tower-only` repository is preserved separately and was not merged.
- The restored implementation is committed locally after validation. No push, merge,
  PR or remote write is part of this restoration; see Git history for its commit.
- Original checkout and its untracked Airport files are preserved. The eight original
  Airport tests were copied unchanged; existing repository assertions were not weakened.
- Radar and Command production files are unchanged. No frontend was created.

The original Tower-local `AirportDatabase` was adapted rather than merging PR #17.
It keeps domain-specific CRUD out of generic messaging and preserves text airport IDs,
method argument order, runway status calls and notifications. PR #17 uses integer
`airports.id` and different runway columns; mixing the two schemas is rejected with a
clear error. No automatic PR #17 data conversion is claimed.

## Authoritative data and consistency

`airports` stores text `airport_id`, finite positive perimeter in meters and latitude/
longitude. A perimeter length and center alone do not define a boundary polygon or
inside/outside classifier; teams must agree on that geometry before implementing it.

`runways` stores `(airport_id, runway_id)`, administrative `available`, `timestamp`
and `mode` (`landing`, `departure`, `mixed`). The original local schema is upgraded
by adding `mode='mixed'` if absent; no rows are discarded. Runway availability in the
public read model is **administratively open AND no cleared reservation**.

`tower_requests` is the single request, reservation and queue store. Sequence numbers
provide persistent FIFO. Partial unique indexes enforce one queued/cleared request
per aircraft and one cleared owner per airport/runway. Terminal requests remain as
audit history; they do not reserve a runway. Airport deletion cascades runway rows,
removes airport/runway projections, and refuses to proceed with active requests.
Runway deletion/configuration refuses unsafe changes. Queued requests can be
cancelled to permit cleanup without pretending a flight completed.

`tower_processed_messages` separates durable processing from a human reading the
inbox. A read request is still processed. Only messages from Radar to Tower of type
`runway_request` are claimed; text and other senders/types remain untouched.

`MessageDatabase.transaction()` uses BEGIN IMMEDIATE and nested savepoints. Tower
validates, reserves, saves decisions/projections, creates both outgoing messages and
records processing inside one transaction. The messages table itself is the durable
outbox; no second broker/outbox table is necessary. A failed write leaves the input
retryable and rolls back all effects. Use one connection per worker/thread. SQLite
lock errors propagate for caller retry; there is no unbounded automatic retry loop.

`shared_state` is a compatibility projection only. The allocator never reads it.
Keys are `airport:{airport_id}`, `runway:tower:{JSON [airport_id,runway_id]}`,
`tower_request:{request_id}`, and the existing latest-per-aircraft `decision:{id}`.
Existing unmanaged runway previews require explicit migration or a fresh database
before configuring the first airport; they are not silently imported as real state.

Construct `TowerService` outside an existing transaction. Its constructor reuses
Radar's existing AircraftStore to initialize/read aircraft storage; do not invoke
Radar's connection-context write methods inside a shared Tower transaction. Tower
never changes aircraft flight state. Radar must coordinate deletion/state changes
with Tower rather than deleting an actively reserved aircraft without releasing it.

## Public function responsibility map

| Module | Stable entry points | Responsibility |
| --- | --- | --- |
| `src/tower/airport.py` | `AirportDatabase(db)` | Initialize/check the one Tower schema; CRUD repository |
| AirportDatabase | `create_airport(airport_id, perimeter, latitude, longitude, runway_ids)` | Atomically create airport and nonempty initial runway inventory |
| AirportDatabase | `get_airport(airport_id)`, `list_airports()` | Consistent detached dictionaries with computed runway availability |
| AirportDatabase | `update_airport(airport_id, perimeter, latitude, longitude)`, `delete_airport(airport_id)` | Validated metadata change or safe cascade cleanup |
| AirportDatabase | `add_runway(airport_id, runway_id, *, mode='mixed', available=True)`, `list_runways(airport_id)` | Create/list authoritative runway configuration |
| AirportDatabase | `update_runway(airport_id, runway_id, *, available, mode)`, `update_runway_status(airport_id, runway_id, available)`, `delete_runway(airport_id, runway_id)` | Guarded configuration/cleanup; status call preserves original API |
| AirportDatabase | `notify_runway_status(airport_id, runway_id)` | Explicit compatible text notifications to Radar and Command, atomically |
| `src/tower/service.py` | `TowerService(db)` | Operational lifecycle boundary; `.airports` exposes the CRUD repository |
| TowerService | `request_runway(request_id, aircraft_id, airport_id, operation, *, runway_id=None)` | Validate, allocate/reject/queue, persist and notify atomically |
| TowerService | `get_decision(request_id)`, `list_queue(airport_id)`, `snapshot()` | Detached versioned read models, not mutable database records |
| TowerService | `complete_request(request_id)` | Idempotent completion, reservation release and automatic promotion |
| TowerService | `release_runway(airport_id, runway_id, request_id)` | Ownership-checked completion for runway-oriented callers |
| TowerService | `cancel_request(request_id, *, reason='Cancelled by operator')` | Reject/cancel active request, release if needed and promote |
| TowerService | `promote_queue(airport_id)` | Explicit recheck after reopening/adding runways or changing flight states |
| `src/tower/messaging.py` | `process_requests(db, *, limit=100)` | Versioned inbound validation, durable claim/acknowledgement and service dispatch |
| messaging | `send_message(db, recipient, content)`, `receive_messages(db, *, unread_only=False, limit=100, mark_as_read=False)` | Unchanged text-message wrappers |
| messaging | `list_airborne_aircraft(db)`, `load_runways(db)`, `assign_runway_to_aircraft(db, aircraft_id, runway_id)` | Legacy CLI adapter to the same repository/service; single-airport only |
| `src/tower/clearance.py` | `runway_is_safe(runway, aircraft=None)`, `find_safe_runway(runways, aircraft=None)` | One independently testable compatibility/selection policy |
| clearance | `is_aircraft_cleared(status)` | Original clearance-status predicate |
| clearance | `assign_runway`, `issue_landing_clearance`, `evaluate_runway_decision`, `update_runway_availability` | Preserved offline list/timer preview helpers; no operational caller allocates through them |
| clearance | `publish_runway_decision(db, aircraft, runways, *, runway_id=None, queue_position=None)` | Legacy offline snapshot tests/previews only; refuses configured airports |
| `src/tower/airportui.py` | `create_runways`, `update_runway_visuals`, `toggle_runway`, `create_airport_ui` | Standalone canvas example, documented offline; cannot affect Tower reservations |
| `src/db.py` | `transaction`, `send_message`, `receive_messages`, shared-state helpers | Reused transport, claims, serialization and atomic write boundaries |
| `airplane_demo.py` | `main`, `simulate_departure`, `simulate_landing`, `fabricate_landing_queue` | Simulated aircraft movement/completion; all live clearances/queues delegate to Tower |

Internal `_validation_error`, `_save`, `_promote`, `_submit`, projection/key helpers
and schema SQL are package implementation details, not integration contracts.
`current_timestamp`, validation/schema methods and legacy constants remain for local
compatibility; external teams should use the documented domain APIs.

Dependencies are acyclic: messaging -> service -> Airport repository / clearance
policy / existing Radar AircraftStore -> shared DB. Airport storage depends only on
shared DB. Neither policy nor storage imports messaging/service. Command and future
React adapters need no SQL, locking, queue logic or private helpers. An HTTP adapter
can expose these synchronous domain methods later; one is not needed inside Tower.

## Request/response contract (version 1)

Radar inserts through `db.send_message(Team.RADAR, Team.TOWER, ...,
message_type='runway_request', metadata=...)`:

```json
{
  "schema_version": 1,
  "request_id": "radar-AC001-landing-1",
  "aircraft_id": "AC001",
  "airport_id": "JFK",
  "operation": "landing",
  "timestamp": "2026-11-05T15:00:00+00:00"
}
```

Optional `runway_id` restricts selection to a specific runway. Identifiers must be
nonempty strings without surrounding whitespace. Incoming timestamps must carry UTC
`Z` or zero offset; these timestamps are informational, not queue-order authority.
Landing requires altitude > 0 and a non-Parked state; departure requires altitude 0
and Parked. Current status is never proof of clearance. Invalid known-shaped requests
produce durable rejection decisions. Malformed envelopes receive rejection messages
with `source_message_id`; they are not inserted as valid request records.

Tower sends `message_type='runway_response'` to both Radar and Command:

```json
{
  "schema_version": 1,
  "request_id": "radar-AC001-landing-1",
  "aircraft_id": "AC001",
  "airport_id": "JFK",
  "operation": "landing",
  "outcome": "cleared",
  "status": "cleared",
  "runway_id": "R1",
  "queue_position": null,
  "reason": null,
  "timestamp": "2026-11-05T15:00:00.123456+00:00",
  "safe": true,
  "occupied_until": null
}
```

A queued response has null runway, positive queue_position and safe=false. A rejected
response has a reason, null runway and null queue position. Unknown resources and
invalid flight state are rejections; busy compatible resources queue. Direct calls
with malformed identifiers or conflicting reuse of a request ID raise ValueError.

Identical request-ID replay returns the **latest** decision without another outbox
message. Changed aircraft/airport/operation/preference under the same ID is rejected
without changing the original. Separate request IDs for an already active aircraft
are rejected; their projection cannot replace its active clearance. Promotion and
queue-position changes generate fresh responses for the same request ID. Returned
read models also contain `message_ids` for diagnostics; outgoing metadata does not.

Completion sends `runway_completed` with outcome/status `completed`; it is a separate
terminal event, not a fourth runway_response outcome. Cancellation sends a rejected
runway_response with the supplied reason. Consumers must handle these events and
terminal states rather than infer completion from elapsed time.

## FIFO and worker use

FIFO is strict per airport and survives restart. The oldest valid waiting request is
considered first; an unavailable preferred runway blocks followers even if another
runway is free. Invalidated waiters are rejected and processing continues. Request
insertion, completion, release and cancellation reevaluate/promote the queue. After
administrative configuration changes call `promote_queue(airport_id)` explicitly.
There is no expiry-based release of an operational reservation. Radar/operator must
confirm completion or cancellation; an abandoned request safely holds its reservation.

Use a new disposable path, never a project database, for initial rehearsal:

```sh
export SQLITE_DATABASE="$(mktemp -d /private/tmp/atc-tower.XXXXXX)/demo.db"
python -B - <<'PY'
import os
from src.db import create_database
from src.tower.service import TowerService

db = create_database(os.environ['SQLITE_DATABASE'])
tower = TowerService(db)
tower.airports.create_airport('JFK', 22000, 40.6413, -73.7781, ['R1', 'R2'])
print(tower.snapshot())
db.close()
PY
python -B -m src.tower.messaging --database "$SQLITE_DATABASE" --once
# Omit --once for the foreground polling worker. Ctrl-C closes its connection.
```

Radar must first create the aircraft through its existing store, then send the
contract above. It must consume responses idempotently, apply approved flight/assigned
runway state, acknowledge after successful application, and report completion using
`complete_request` or ownership-checked `release_runway`. The current Radar production
code does not do this automatically. Message types are data, never arbitrary function
names to execute. The alternate Radar branch's schema and names still need an adapter.

## Command/future frontend data contract

Use `TowerService.snapshot()` as the stable authoritative read model:

- `schema_version: 1`.
- `airports`: airport metadata and `runways`, each with airport_id, runway_id/id,
  administrative `open`, computed `available`, mode, timestamp, active request_id,
  aircraft_id and occupied_until=null.
- `requests`: ordered decisions with request_id, operation, outcome/status, runway_id,
  queue_position, reason, timestamp and diagnostic message_ids. Filter queued/cleared
  for active views; completed/rejected are history.

Command may consume the response/completion events instead, but must correlate by
request_id and tolerate replay. The existing dashboard can display compatibility
shared-state projections; it does not yet consume these events or use snapshot().
Its landing-only labels, map, three runway positions, freshness rules and creation/
altitude controls remain Command work. No end-to-end UI readiness is claimed.

## Modularity review result and remaining debt

The operational path has one allocator (`TowerService._promote`), one persistent FIFO
(`tower_requests.sequence`), one compatibility policy (`find_safe_runway`), one Airport
CRUD implementation and one authoritative reservation source. The simulator/menu
are adapters, not independent allocators. There are two new production modules only:
Airport repository and lifecycle service; no framework, generic repository hierarchy,
broker, HTTP server or frontend abstraction was introduced.

Callers/tests were checked before retaining or refactoring legacy behavior:

- List/timer clearance functions remain for existing Tower/Command snapshot tests.
  evaluate_runway_decision reuses assign_runway instead of duplicating its algorithm.
  publish_runway_decision cannot overwrite a configured airport. These are previews,
  not a second operational clearance path.
- AirportUI's local dictionaries remain for its canvas example/tests. They are not
  loaded by the allocator; the module documents that purpose.
- The legacy messaging menu preserves function messages and airborne listing. It now
  uses explicit release instead of a three-second expiry and refuses ambiguous
  multiple-airport selection. Its clean-database defaults are mixed-use for departure.
- Unknown statuses and departure-only compatibility fail closed in the policy.
- Simulator movement/aircraft acknowledgement is still direct and simulated. Its
  batch helper submits before completing flights, so excess demand reaches the real
  Tower queue. Departure now obtains Tower clearance before moving. Its old
  initialize_runways/simulate_aircraft/record_clearance_request entry points remain
  compatibility helpers; record_clearance_request only creates a display preview and
  is not called by the live simulator. UTC generation no longer applies local timezone
  offsets to timezone-free Radar timestamps. The default departure-only completion
  message no longer claims an unexecuted landing. Re-running departure for an
  already airborne ID now rejects rather than granting another departure; complete
  its landing or use a fresh demo database.
- The original Airport tests still say REQ-TWR-003; preserved assertions are linked to
  the new REQ-TWR-007. Tracked REQ-TWR-003 remains the clearance predicate.

Remaining debt is explicit: old standalone previews, JSON display projections,
Radar model initialization dependency, no spatial perimeter classifier, no consumer
implementation or UI changes, no network transport, no large-scale queue performance
guarantee, and no automatic migration from PR #17 or unmanaged snapshots. These do
not create competing operational Tower algorithms. Full six-case UI integration
still requires team agreement and Radar/Command implementation.

## Validation

Run the following from this worktree with isolated test databases:

```sh
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider tests/tower
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider
npm test -- 'tests/**/*.ts'
bash scripts/validate-requirements.sh radar
bash scripts/validate-requirements.sh tower
bash scripts/validate-requirements.sh command
git diff --check
```

Tests cover CRUD, invalid dimensions/states/resources, compatibility, strict FIFO,
release/cancellation/promotion, independent-connection races, idempotent replay,
message read-vs-processing distinction, failure rollback, reopen persistence, schema
compatibility, projection tampering and existing legacy behavior. No tests use an
existing project/production database. Exact final results are reported below.

Restoration validation results:

| Check | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| Tower Python | 94 | 0 | 0 |
| Complete Python repository | 144 | 0 | 0 |
| Complete repository TypeScript glob | 2 | 0 | 0 |
| Radar / Tower / Command requirement validators | 3 | 0 | 0 |

`git diff --check` passed. Source import-graph inspection found no cycles. The worker
module entry point was tested in a subprocess against a temporary database. Local
runtimes: Python 3.10.9, Node 22.12.0; CI's Python 3.12 runtime was not reproduced.
Locked npm dependencies were installed only in this worktree from the temporary
cache. No dependency manifest changed.

Modified files: README.md, airplane_demo.py, docs/requirements-index.md,
requirements/tower/REQ-TWR-006-assign-runway-message.md, src/db.py,
src/tower/airportui.py (purpose documentation only), src/tower/clearance.py,
src/tower/messaging.py, messaging_demo.py, tests/tower/test_assign_runway.py.

New files: docs/tower-integration.md, requirements/tower/REQ-TWR-007-airport-crud.md,
requirements/tower/REQ-TWR-008-request-lifecycle.md,
requirements/tower/REQ-TWR-009-structured-messages.md, src/tower/airport.py,
src/tower/service.py, tests/tower/test_airport.py (unchanged copy of original),
tests/tower/test_service.py, tests/tower/test_automatic_messaging.py.


## Restoration differences and repeatable demonstration

Operational source behavior is restored from the recorded November 1 writes and
patches, including the two verified compatibility fixes: list_airborne_aircraft
reads existing rows using Radar's AircraftState without constructing AircraftStore
or committing the caller's transaction; the legacy menu catches ambiguous multi-airport
selection and returns to the menu without selecting an airport. Radar and Command
production files and the older repositories remain unchanged.

The recovered source initially reproduced the historical 93 Tower / 143 Python
results. One new test now makes the previously separate large demonstration repeatable:
`test_ten_runways_twelve_requests_restart_promotion_and_added_runway` configures ten
mixed-use runways and sends twelve alternating landing/departure requests through
independent SQLite connections. It asserts ten distinct reservations and two FIFO
waiters, consumes both clients' messages, closes/reopens all connections, completes
one operation, verifies automatic promotion of the oldest waiter, adds R11, explicitly
calls promote_queue after that administrative change, and verifies the final waiter
is cleared onto R11. Aircraft records stay unchanged.

The smaller automatic test is preserved in both public-processor and bounded --once
subprocess modes, with simulated Radar/Command consumers only in tests. Documentation
paths, provenance and result counts are updated for this permanent worktree. These
are restoration/validation changes, not a new message contract or production client.
