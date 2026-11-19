# REQ-TWR-010 · R2 queue priority

Session 10, slides 32–33. Start with request-arrival FIFO. A supported nonemergency
Radar conflict advances a queued request one place for a two-hour delay, two for
a four-hour delay, or three for low fuel. Send Radar a resolution and publish
Conflict and Conflict Resolved to Command.

The Radar producer should reuse one `event_id` per request, conflict reason,
and episode across one-second repeats. Transport timestamps may change. A
stronger reason needs a new event ID. Other changes to an existing event ID are
invalid. Tower applies an event once transactionally, and uses a persistent
per-request priority limit even if the sender creates new IDs for retries.
A stronger signal grants only the difference from the strongest reason
already applied to that request. This
precedence is a documented implementation decision. The course slides specify
the movement per reason but leave escalation and ties open. Other requests keep
relative order. Allocation still validates compatibility and ownership.

Implementation: `src/tower/r2.py`. Tests: `tests/tower/test_r2.py`.
