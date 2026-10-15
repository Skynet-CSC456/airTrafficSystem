# ATC System

This repository contains the ATC (Air Traffic Control) subsystem structure for Radar, Tower, Command, and Airport teams.

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
- `airport/`

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

`create_database()` creates the database file, the `teams`, `messages`,
`airports`, and `runways` tables, and registers the Radar, Tower, Command, and
Airport teams.

## Airport process

Run the Airport command line process from the repository root. It uses the same
`SQLITE_DATABASE` setting as the message store; pass `--database PATH` to use a
specific file. Perimeter is in meters; longitude and latitude are decimal
degrees. Runway count is computed from its records.

```sh
python -m src.airport create 12000 -73.7781 40.6413
python -m src.airport add-runway 1 04L available --time 2026-10-08T09:30:00
python -m src.airport get 1
python -m src.airport status 1 04L unavailable
python -m src.airport send radar "Runway 04L unavailable"
python -m src.airport receive --unread --mark-read
```

Other commands are `list`, `update`, `delete`, and `remove-runway`. The Python
API is available through `create_database()` (`create_airport`, `get_airport`,
`list_airports`, `update_airport`, `delete_airport`, `create_runway`,
`update_runway_status`, and `delete_runway`) and
`src.airport.messaging` (`send_message` and `receive_messages`).

## Next plan

Write the REST API endpoints using the `src/db.py` database functions (e.g. `send_message`, `receive_messages`) so the API accesses the database properly instead of duplicating logic.
