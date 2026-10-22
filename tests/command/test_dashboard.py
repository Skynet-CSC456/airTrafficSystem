# REQ-CMD-004
from src.command.dashboard import build_dashboard_snapshot, system_status_for_snapshot
from src.db import create_database
from src.tower.clearance import publish_runway_decision


def test_dashboard_snapshot_is_normal_when_all_fields_are_healthy():
    db = create_database(':memory:')
    try:
        snapshot = build_dashboard_snapshot(db)
        assert snapshot['system_status'] == 'normal'
        assert snapshot['aircraft'] == []
        assert snapshot['runways']
    finally:
        db.close()


def test_dashboard_snapshot_marks_degraded_state_when_needed():
    snapshot = {
        'aircraft': [{'id': 'AC001', 'status': 'warning'}],
        'runways': [{'id': 'RWY 1', 'available': True}],
        'decisions': [{'aircraft_id': 'AC001', 'status': 'degraded'}],
    }
    assert system_status_for_snapshot(snapshot) == 'degraded'


def test_dashboard_snapshot_includes_live_tower_decisions():
    db = create_database(':memory:')
    try:
        publish_runway_decision(db, {'id': 'AC075', 'status': 'Landing'}, [{'id': 'RWY 1', 'available': True, 'mode': 'landing'}])
        snapshot = build_dashboard_snapshot(db)
        assert snapshot['decisions'][0]['aircraft_id'] == 'AC075'
        assert snapshot['decisions'][0]['status'] == 'cleared'
        assert snapshot['runways'][0]['id'] == 'RWY 1'
        assert snapshot['landing_queue'][0]['display'].startswith('Cleared for landing on RWY 1')
    finally:
        db.close()


def test_dashboard_snapshot_releases_runway_when_hold_has_expired():
    db = create_database(':memory:')
    try:
        db.upsert_shared_state('runway:RWY 9', {'id': 'RWY 9', 'available': False, 'mode': 'landing', 'occupied_until': '2000-01-01T00:00:00+00:00'}, category='runway')
        snapshot = build_dashboard_snapshot(db)
        assert any(runway['id'] == 'RWY 9' and runway['available'] is True for runway in snapshot['runways'])
    finally:
        db.close()
