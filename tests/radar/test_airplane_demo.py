"""REQ-RAD-003 / REQ-RAD-004: aircraft demo integration with Radar and Tower."""

import json

import airplane_demo
from src.db import create_database
from src.radar.aircraft import AircraftStore


def test_demo_updates_one_aircraft_and_exchanges_messages(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / 'demo.db')
    monkeypatch.setattr('sys.argv', ['airplane_demo.py', '--database', path,
                                    '--updates', '1'])
    intervals = []

    def advance_clock(interval):
        intervals.append(interval)
        # Give the next update a later timestamp without waiting in the test.
        db = create_database(path)
        try:
            with db.connection:
                db.connection.execute("UPDATE aircraft SET timestamp='2000-01-01T00:00:00'")
        finally:
            db.close()

    monkeypatch.setattr(airplane_demo.time, 'sleep', advance_clock)
    airplane_demo.main()
    output = capsys.readouterr().out
    initial = json.loads(output.splitlines()[0].removeprefix('Initial state: '))
    assert intervals == [5]
    assert 'Tower received: Requesting departure clearance' in output
    assert 'Aircraft received: Departure approved (simulation)' in output
    assert 'Radar accepted: True' in output
    db = create_database(path)
    try:
        store = AircraftStore(db)
        aircraft = store.get('AC001')
        assert len(store.list()) == 1
        assert aircraft.status == 'Departing'
        assert aircraft.altitude == initial['altitude'] + 50
        assert aircraft.latitude == initial['latitude'] + 0.001
        assert aircraft.longitude == initial['longitude'] + 0.001
        assert store.receive_messages(for_tower=True) == []
        assert store.receive_messages(for_tower=False, aircraft_id='AC001') == []
    finally:
        db.close()
