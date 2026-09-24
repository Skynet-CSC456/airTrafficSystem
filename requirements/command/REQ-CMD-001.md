# REQ-CMD-001: Command and control flow

The command subsystem shall prioritize flight plans in a deterministic, reviewable order.

## Acceptance criteria
- The system shall rank flights by highest priority number first.
- The system shall keep the dispatch payload explicit and easy to audit.
- The dispatch decision shall be validated by automated tests.
