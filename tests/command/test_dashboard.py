# REQ-CMD-004
from datetime import datetime, timedelta, timezone

import pytest

from src.command.dashboard import AIRPORT_CENTER, MAP_HALF_SPAN_DEGREES, aircraft_map_position, aircraft_map_state, build_dashboard_snapshot, system_status_for_snapshot
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


def test_aircraft_map_position_follows_latitude_and_longitude():
    lat0, lon0 = AIRPORT_CENTER
    span = MAP_HALF_SPAN_DEGREES
    x, y, inside = aircraft_map_position({'latitude': lat0, 'longitude': lon0}, 600, 400)
    assert (round(x), round(y), inside) == (300, 200, True)  # airport center = middle of the map
    x, y, inside = aircraft_map_position({'latitude': lat0 + span, 'longitude': lon0 - span}, 600, 400)
    assert (round(x), round(y), inside) == (45, 45, True)  # north-west corner of the mapped area
    x, y, _ = aircraft_map_position({'latitude': lat0 - span / 2, 'longitude': lon0 + span / 2}, 600, 400)
    assert x > 300 and y > 200  # further east is further right, further south is further down


@pytest.mark.parametrize('latitude, longitude, expected', [
    (AIRPORT_CENTER[0] + 1, AIRPORT_CENTER[1], (300, 45)),        # far north -> top edge
    (AIRPORT_CENTER[0], AIRPORT_CENTER[1] - 1, (45, 200)),        # far west -> left edge
    (AIRPORT_CENTER[0] - 1, AIRPORT_CENTER[1] + 1, (555, 355)),   # far south-east -> bottom-right corner
])
def test_aircraft_outside_the_map_is_clamped_to_its_edge(latitude, longitude, expected):
    x, y, inside = aircraft_map_position({'latitude': latitude, 'longitude': longitude}, 600, 400)
    assert (round(x), round(y)) == expected
    assert inside is False


def test_seeded_aircraft_all_fit_on_the_map():
    from seed_aircraft import SEED_AIRCRAFT
    for _, latitude, longitude, _, _ in SEED_AIRCRAFT:
        assert aircraft_map_position({'latitude': latitude, 'longitude': longitude}, 600, 400)[2]


def test_dashboard_snapshot_keeps_runways_in_id_order_after_updates():
    db = create_database(':memory:')
    try:
        db.upsert_shared_state('runway:RWY 1', {'id': 'RWY 1', 'available': True, 'mode': 'landing'}, category='runway')
        db.upsert_shared_state('runway:RWY 2', {'id': 'RWY 2', 'available': True, 'mode': 'landing'}, category='runway')
        # Tower re-saves RWY 1 after assigning it, making it the most recently updated row.
        publish_runway_decision(db, {'id': 'AC001', 'status': 'Landing'}, [{'id': 'RWY 1', 'available': True, 'mode': 'landing'}])
        db.connection.execute("UPDATE shared_state SET updated_at = '2099-01-01 00:00:00' WHERE key = 'runway:RWY 1'")
        assert [row['key'] for row in db.list_shared_state(category='runway')] == ['runway:RWY 2', 'runway:RWY 1']
        assert [runway['id'] for runway in build_dashboard_snapshot(db)['runways']] == ['RWY 1', 'RWY 2']
    finally:
        db.close()


def test_dashboard_snapshot_shows_only_unanswered_runway_requests():
    db = create_database(':memory:')
    try:
        db.upsert_shared_state('request:AC001', {'aircraft_id': 'AC001', 'status': 'requested', 'request_type': 'landing'}, category='request')
        db.upsert_shared_state('request:AC002', {'aircraft_id': 'AC002', 'status': 'answered', 'request_type': 'departure'}, category='request')
        assert [item.get('aircraft_id') for item in build_dashboard_snapshot(db)['decisions']] == ['AC001']
    finally:
        db.close()


NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize('decision, expected', [
    (None, ('position', None)),
    ({'status': 'queued', 'runway_id': None, 'occupied_until': None}, ('position', None)),
    ({'status': 'cleared', 'runway_id': 'RWY 1', 'occupied_until': (NOW + timedelta(seconds=5)).isoformat()}, ('runway', 'RWY 1')),
    ({'status': 'cleared', 'runway_id': 'RWY 1', 'occupied_until': (NOW - timedelta(seconds=1)).isoformat()}, ('hidden', None)),
])
def test_aircraft_map_state(decision, expected):
    # REQ-RAD-007: on its runway during the hold, off the map afterwards.
    assert aircraft_map_state(decision, NOW) == expected


def test_snapshot_marks_aircraft_on_runway_and_after_the_hold():
    from src.radar.aircraft import AircraftState, AircraftStore
    db = create_database(':memory:')
    try:
        store = AircraftStore(db)
        for aircraft_id in ('ON1', 'DONE1', 'FREE1'):
            store.create(AircraftState(aircraft_id, 40.64, -73.78, 'Landing', 500, '2026-11-05T12:00:00'))
        future = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        db.upsert_shared_state('decision:ON1', {'aircraft_id': 'ON1', 'status': 'cleared', 'runway_id': 'RWY 2', 'occupied_until': future}, category='decision')
        db.upsert_shared_state('decision:DONE1', {'aircraft_id': 'DONE1', 'status': 'cleared', 'runway_id': 'RWY 1', 'occupied_until': '2000-01-01T00:00:00+00:00'}, category='decision')

        planes = {plane['id']: plane for plane in build_dashboard_snapshot(db)['aircraft']}
        assert (planes['ON1']['map_state'], planes['ON1']['on_runway']) == ('runway', 'RWY 2')
        assert planes['DONE1']['map_state'] == 'hidden'  # still listed in the table
        assert planes['FREE1']['map_state'] == 'position'
    finally:
        db.close()


def test_snapshot_drops_decisions_for_departed_aircraft():
    db = create_database(':memory:')
    try:
        # Departed: no longer tracked and its hold is over.
        db.upsert_shared_state('decision:GONE1', {'aircraft_id': 'GONE1', 'status': 'cleared', 'runway_id': 'RWY 1', 'occupied_until': '2000-01-01T00:00:00+00:00'}, category='decision')
        snapshot = build_dashboard_snapshot(db)
        assert snapshot['decisions'] == []
        assert snapshot['landing_queue'] == []
    finally:
        db.close()


def test_landed_aircraft_leaves_the_landing_queue_but_stays_in_decisions():
    from src.radar.aircraft import AircraftState, AircraftStore
    db = create_database(':memory:')
    try:
        AircraftStore(db).create(AircraftState('LAND1', 40.64, -73.78, 'Parked', 0, '2026-11-05T12:00:00'))
        db.upsert_shared_state('decision:LAND1', {'aircraft_id': 'LAND1', 'status': 'cleared', 'runway_id': 'RWY 1', 'occupied_until': '2000-01-01T00:00:00+00:00'}, category='decision')
        snapshot = build_dashboard_snapshot(db)
        assert snapshot['landing_queue'] == []
        assert [decision['aircraft_id'] for decision in snapshot['decisions']] == ['LAND1']
    finally:
        db.close()

