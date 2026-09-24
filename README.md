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
