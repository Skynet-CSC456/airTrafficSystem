# ATC System

This repository contains the ATC (Air Traffic Control) subsystem structure for Radar, Tower, and Command teams.

## Directory Overview

- `.github/` — repository automation and ownership rules
- `docs/` — project documentation and traceability index
- `requirements/` — subsystem requirement documents
- `src/` — implementation code for each subsystem
- `tests/` — automated tests for each subsystem
- `scripts/` — validation and quality checks

## Subsystems

- `radar/`
- `tower/`
- `command/`

## Development Notes

- Keep requirement IDs aligned with the validation script naming convention.
- Update `docs/requirements-index.md` whenever a requirement is added.
- Every requirement should be tied to source code and automated tests.

## Team messaging database

The SQLite message store is implemented in `src/db.py`. It uses the standard
library and stores data in `data/atc_system.db` by default. Set
`SQLITE_DATABASE` to use another database path.

```python
from src.db import Team, create_database

db = create_database()
db.send_message(Team.RADAR, Team.TOWER, "Aircraft AAL123 entering sector 4")
messages = db.receive_messages(Team.TOWER, unread_only=True, mark_as_read=True)
db.close()
```

`create_database()` creates the database file, the `teams` table, and the
`messages` table, and registers the Radar, Tower, and Command teams.

## Next plan

Write the REST API endpoints using the `src/db.py` database functions (e.g. `send_message`, `receive_messages`) so the API accesses the database properly instead of duplicating logic.

## Aircraft state demo

Run a small aircraft process that sends updates to Radar and exchanges messages
with Tower:

```sh
.venv/bin/python airplane_demo.py --updates 3 --interval 1
```

The default interval is five seconds (REQ-RAD-003). The demo uses
`data/airplane_demo.db`, separate from the main database. Set `--database` and
`--id` to choose a database and aircraft. Repeated runs resume the existing state.
Each accepted update replaces the same aircraft row; no position history is kept.
Altitude is meters above ground and timestamps are UTC `YYYY-MM-DDTHH:MM:SS`.
Motion is a simple coordinate increment for demonstration only.

`src/radar/aircraft.py` provides `AircraftState` and `AircraftStore` for CRUD and
addressed aircraft/Tower messages. `src/radar/position_update.py` validates complete
state payloads and applies only newer updates to registered aircraft. The local
prototype calls Radar directly; it does not yet provide a network API or broker.
Deleting an aircraft also deletes its aircraft-specific messages.
