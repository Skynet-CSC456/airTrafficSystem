# REQ-TWR-008: Atomic Tower requests and persistent FIFO

Tower shall validate landing/departure requests against known airports, aircraft,
flight state, compatibility and authoritative runway availability.

- Airborne landing and grounded Parked departure requests share one allocator.
- Clear on a compatible available runway, queue when busy, reject invalid requests.
- Persist strict per-airport FIFO ordering and prevent duplicate active aircraft or runway ownership.
- Complete/release/cancel atomically; automatically promote and revalidate waiters.
- Replay a request ID without duplicate effects; reject conflicting reuse.
- Commit reservation, decision, projections and Radar/Command outgoing messages together.
- Preserve reservations and queues across reopening; no implicit timer release.

Source: `src/tower/service.py`; shared transactions: `src/db.py`.
Tests: `tests/tower/test_service.py`, including independent-connection concurrency
and injected failure/rollback checks. Contract: `docs/tower-integration.md`.
