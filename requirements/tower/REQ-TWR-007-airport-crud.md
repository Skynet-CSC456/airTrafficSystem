# REQ-TWR-007: Authoritative Airport and runway CRUD

Tower shall store airport identifiers, perimeter in meters, coordinates and runway
configuration in one SQLite schema, retaining the existing Tower-local CRUD API.
Runway availability shall combine administrative openness and active reservation.

- Create/read/list/update/delete airports and create/list/update/delete runways.
- Validate finite dimensions, identifiers, compatibility modes and availability.
- Refuse unsafe mutation/deletion while requests depend on the resource.
- Preserve terminal request history, remove deleted-resource projections, enforce foreign keys.
- Upgrade the original local schema without losing rows; reject incompatible PR #17
  schema or unmanaged runway previews rather than silently reinterpret them.

Source: `src/tower/airport.py`. Tests: `tests/tower/test_airport.py` (original
local assertions preserved) and `tests/tower/test_service.py`.
