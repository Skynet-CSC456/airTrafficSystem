# REQ-TWR-003: Create and Manage Airport

Tower shall be able to create, retrieve, update, and delete airport records containing airport dimensions, geographic coordinates, and runway information.

## Acceptance criteria

- Tower can create an airport with a unique airport ID.
- Each airport stores its perimeter, latitude, and longitude.
- Each airport maintains a number of runways.
- Each runway has a unique ID within its airport, an availability status (available or unavailable), and a timestamp in the format `YYYY-MM-DDTHH:MM:SS`.
- Tower can retrieve an existing airport and its associated runway information from the database.
- Tower can update an airport's stored information, including its dimensions and geographic coordinates.
- Tower can delete an existing airport and its associated runway records.
- Airport and runway information persists in the database after creation or modification.
- Invalid airport information, duplicate airport IDs, and requests involving nonexistent airports are handled appropriately.
- Tower can use the existing messaging system to send and receive information about airport and runway updates with Radar and Command.

## Traceability

- Planned source: `src/tower/airport.py`
- Planned tests: `tests/tower/test_airport.py`
- Messaging: `src/tower/messaging.py` — reuses `REQ-TWR-001` and `REQ-TWR-002`.
- Database: Reuse existing database infrastructure where appropriate.