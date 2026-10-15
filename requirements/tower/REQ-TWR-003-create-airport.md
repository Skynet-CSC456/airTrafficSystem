# REQ-TWR-003: Create and Manage Airport

Tower shall be able to use the Airport process to create, retrieve, update, and delete airport records containing airport dimensions, geographic coordinates, and runway information.

## Acceptance criteria

- Each airport receives a unique database-generated integer ID; callers do not provide or choose the ID.
- Each airport stores its perimeter, latitude, and longitude.
- Airports can be created before runways are added; each airport's runway count is derived from its associated runway records.
- Each runway has a unique ID within its airport, an availability status (available or unavailable), and a timestamp in the format `YYYY-MM-DDTHH:MM:SS`.
- Tower can retrieve an existing airport and its associated runway information from the database.
- Tower can update an airport's stored information, including its dimensions and geographic coordinates.
- Tower can delete an existing airport and its associated runway records.
- Airport and runway information persists in the database after creation or modification.
- Invalid airport information is rejected. Retrieving a nonexistent airport returns `None`; updating or deleting a nonexistent airport returns `False`. Adding a runway to a nonexistent airport is rejected by the database.
- The Airport process can send and receive information about airport and runway updates with Radar and Command through the existing Airport messaging implementation, using the Airport sender identity and inbox.

## Traceability

- The implementation and test paths below refer to `origin/create-airport-processs`; they are not integrated into `feature/tower-airport`.
- Source: `src/db.py` — `MessageDatabase.create_airport`, `get_airport`, `update_airport`, `delete_airport`, `create_runway`, `update_runway_status`, and `delete_runway`; `Airport.number_of_runways`.
- Process: `src/airport/__main__.py` — `main`, invoked with `python -m src.airport`.
- Messaging: `src/airport/messaging.py` — `send_message` and `receive_messages`, using `Team.AIRPORT`.
- Tests: `tests/airport/test_airport.py` — existing Airport CRUD, runway, persistence, validation, and messaging tests marked `REQ-APT-001` and `REQ-APT-002`.
- Database: Reuse `MessageDatabase` and `create_database` from `src/db.py`.
- Timestamp alignment: The professor's required format above is retained; the existing implementation also emits timezone offsets, including `+00:00` by default.
