# REQ-TWR-005
from datetime import datetime, timedelta, timezone

import pytest

from src.db import create_database
from src.tower.clearance import assign_runway, is_aircraft_cleared, publish_runway_decision, update_runway_availability


def test_assign_runway_rejects_unsafe_choices():
    runways = [
        {'id': 'RWY 1', 'available': False, 'mode': 'landing'},
        {'id': 'RWY 2', 'available': True, 'mode': 'landing'},
    ]
    aircraft = {'id': 'AC011', 'status': 'Landing'}

    decision = assign_runway(runways, aircraft)
    assert decision['runway_id'] == 'RWY 2'
    assert decision['status'] == 'cleared'
    assert is_aircraft_cleared(decision['status']) is True

    with pytest.raises(ValueError):
        assign_runway([{'id': 'RWY 9', 'available': False}], aircraft)


def test_publish_runway_decision_updates_shared_state():
    db = create_database(':memory:')
    try:
        runways = [
            {'id': 'RWY 1', 'available': True, 'mode': 'landing'},
            {'id': 'RWY 2', 'available': True, 'mode': 'landing'},
        ]
        aircraft = {'id': 'AC022', 'status': 'Landing'}

        decision = publish_runway_decision(db, aircraft, runways)
        assert decision['runway_id'] == 'RWY 1'
        assert db.get_shared_state('decision:AC022')['payload']['status'] == 'cleared'
        runway_record = db.get_shared_state('runway:RWY 1')
        assert runway_record is not None
        assert runway_record['payload']['id'] == 'RWY 1'
    finally:
        db.close()


def test_runway_becomes_available_after_hold_window():
    runway = {
        'id': 'RWY 9',
        'available': False,
        'mode': 'landing',
        'occupied_until': (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    }
    refreshed = update_runway_availability([runway])
    assert refreshed[0]['available'] is True
