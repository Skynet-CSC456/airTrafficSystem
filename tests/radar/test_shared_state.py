# REQ-RAD-005
from dataclasses import replace

from src.db import create_database
from src.radar.aircraft import AircraftState, AircraftStore


def test_shared_state_tracks_latest_aircraft_snapshot():
    db = create_database(':memory:')
    try:
        store = AircraftStore(db)
        state = AircraftState('AC007', 40.0, -73.0, 'Landing', 1200, '2026-10-08T14:30:00')
        store.create(state)

        db.upsert_shared_state('aircraft:AC007', {
            'id': state.id,
            'status': state.status,
            'timestamp': state.timestamp,
            'fresh': True,
        }, category='aircraft')

        record = db.get_shared_state('aircraft:AC007')
        assert record is not None
        assert record['payload']['status'] == 'Landing'
        assert record['payload']['fresh'] is True

        newer = replace(state, altitude=1500, timestamp='2026-10-08T14:30:02')
        assert store.update(newer)
        shared = db.get_shared_state('aircraft:AC007')
        assert shared['payload']['timestamp'] == '2026-10-08T14:30:02'
    finally:
        db.close()


def test_create_database_reenables_writes_for_readonly_sqlite_file(tmp_path):
    path = tmp_path / 'readonly.db'
    path.write_bytes(b'')
    path.chmod(0o444)
    try:
        db = create_database(str(path))
        db.upsert_shared_state('health', {'status': 'ok'}, category='system')
        record = db.get_shared_state('health')
        assert record is not None
        assert record['payload']['status'] == 'ok'
    finally:
        path.chmod(0o644)
        db.close()
