# REQ-CMD-004
from datetime import datetime, timedelta, timezone

import pytest

from src.command.dashboard import (
    AIRPORT_CENTER,
    AIRPORT_HALF_SPAN_DEGREES,
    MAP_HALF_SPAN_DEGREES,
    airport_map_box,
    aircraft_map_position,
    aircraft_map_state,
    build_dashboard_snapshot,
    format_clock,
    system_status_for_snapshot,
)
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


def test_parking_stands_are_inside_the_airport():
    from src.radar.runway_operations import PARKING_STANDS
    lat0, lon0 = AIRPORT_CENTER
    for latitude, longitude in PARKING_STANDS:
        assert abs(latitude - lat0) < AIRPORT_HALF_SPAN_DEGREES and abs(longitude - lon0) < AIRPORT_HALF_SPAN_DEGREES


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


@pytest.mark.parametrize('decision, runway_on_plane, expected', [
    (None, None, ('position', None)),
    ({'status': 'queued', 'runway_id': None}, None, ('position', None)),
    ({'status': 'cleared', 'runway_id': 'RWY 1'}, 'RWY 1', ('runway', 'RWY 1')),      # landing/take-off under way
    ({'status': 'cleared', 'runway_id': 'RWY 1'}, None, ('position', None)),          # Radar has finished it
    ({'status': 'cleared', 'runway_id': 'RWY 1'}, 'RWY 2', ('position', None)),
    ({'status': 'completed', 'runway_id': 'RWY 1'}, None, ('position', None)),        # landed: at its stand
])
def test_aircraft_map_state(decision, runway_on_plane, expected):
    # REQ-RAD-007: on its runway while the operation is under way, at its position afterwards.
    assert aircraft_map_state(decision, runway_on_plane) == expected


def test_snapshot_marks_aircraft_on_runway_and_after_the_operation():
    from src.radar.aircraft import AircraftState, AircraftStore
    db = create_database(':memory:')
    try:
        store = AircraftStore(db)
        for aircraft_id in ('ON1', 'DONE1', 'FREE1'):
            store.create(AircraftState(aircraft_id, 40.64, -73.78, 'Landing', 500, '2026-11-05T12:00:00'))
        store.assign_runway('ON1', 'RWY 2')
        for aircraft_id, runway_id in (('ON1', 'RWY 2'), ('DONE1', 'RWY 1')):
            db.upsert_shared_state(f'decision:{aircraft_id}', {'aircraft_id': aircraft_id, 'status': 'cleared', 'runway_id': runway_id, 'operation': 'landing'}, category='decision')

        planes = {plane['id']: plane for plane in build_dashboard_snapshot(db)['aircraft']}
        assert (planes['ON1']['map_state'], planes['ON1']['on_runway']) == ('runway', 'RWY 2')
        assert planes['DONE1']['map_state'] == 'position'  # Radar finished: drawn at its stand
        assert planes['FREE1']['map_state'] == 'position'
    finally:
        db.close()


def test_snapshot_drops_decisions_for_departed_aircraft():
    db = create_database(':memory:')
    try:
        # Departed: Tower completed it and Radar no longer tracks it.
        db.upsert_shared_state('decision:GONE1', {'aircraft_id': 'GONE1', 'status': 'completed', 'runway_id': 'RWY 1', 'operation': 'departure'}, category='decision')
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
        db.upsert_shared_state('decision:LAND1', {'aircraft_id': 'LAND1', 'status': 'completed', 'runway_id': 'RWY 1', 'operation': 'landing'}, category='decision')
        snapshot = build_dashboard_snapshot(db)
        assert snapshot['landing_queue'] == []
        assert [decision['aircraft_id'] for decision in snapshot['decisions']] == ['LAND1']
    finally:
        db.close()


@pytest.mark.parametrize('decision, expected', [
    ({'aircraft_id': 'A1', 'status': 'cleared', 'runway_id': 'RWY 1', 'operation': 'landing'}, 'Cleared for landing on RWY 1 - A1'),
    ({'aircraft_id': 'A1', 'status': 'cleared', 'runway_id': 'RWY 2', 'operation': 'departure'}, 'Cleared for departure on RWY 2 - A1'),
    ({'aircraft_id': 'A1', 'status': 'queued', 'queue_position': 2, 'operation': 'departure'}, 'Queued for departure (position 2) - A1'),
    ({'aircraft_id': 'A1', 'status': 'completed', 'runway_id': 'RWY 1', 'operation': 'landing'}, 'Landed on RWY 1 - A1'),
    ({'aircraft_id': 'A1', 'status': 'completed', 'runway_id': 'RWY 1', 'operation': 'departure'}, 'Departed on RWY 1 - A1'),
    ({'aircraft_id': 'A1', 'status': 'rejected', 'reason': 'Unknown runway', 'operation': 'landing'}, 'Rejected (Unknown runway) - A1'),
    ({'aircraft_id': 'A1', 'status': 'requested', 'request_type': 'departure'}, 'A1 requested departure clearance'),
])
def test_decisions_name_the_operation(decision, expected):
    from src.command.dashboard import _decision_display
    assert _decision_display(decision) == expected


def test_zooming_in_shows_a_smaller_area():
    lat0, lon0 = AIRPORT_CENTER
    plane = {'latitude': lat0, 'longitude': lon0 + 0.4 * MAP_HALF_SPAN_DEGREES}  # 40% of the way to the east edge at zoom 1
    assert round(aircraft_map_position(plane, 600, 400)[0]) == 402  # 300 + 0.4 * 255
    x, _, inside = aircraft_map_position(plane, 600, 400, zoom=2.0)
    assert (round(x), inside) == (504, True)  # 80% of the way at zoom 2
    assert aircraft_map_position(plane, 600, 400, zoom=4.0)[2] is False  # off the map at zoom 4


def test_airport_box_is_centered_and_grows_with_zoom():
    left, top, right, bottom = airport_map_box(600, 400)
    assert (round((left + right) / 2), round((top + bottom) / 2)) == (300, 200)
    assert round(right - left) == round(510 * AIRPORT_HALF_SPAN_DEGREES / MAP_HALF_SPAN_DEGREES)  # 20% of the map width
    left2, _, right2, _ = airport_map_box(600, 400, zoom=2.0)
    assert round(right2 - left2) == round(2 * (right - left))


@pytest.mark.parametrize('value, expected', [
    ('2026-11-05T14:32:10', '14:32'),
    ('14:32:10', '14:32'),
    (None, '--'),
    ('', '--'),
    ('soon', 'soon'),
])
def test_format_clock(value, expected):
    assert format_clock(value) == expected


def test_snapshot_shows_placeholder_runways_without_writing_them():
    db = create_database(':memory:')
    try:
        assert [runway['id'] for runway in build_dashboard_snapshot(db)['runways']] == ['RWY 1', 'RWY 2']
        assert db.list_shared_state(category='runway') == []
    finally:
        db.close()


def test_tower_can_configure_its_airport_after_the_dashboard_opens():
    # REQ-TWR-007 refuses to configure an airport over leftover runway rows.
    from src.tower.airport import AirportDatabase
    db = create_database(':memory:')
    try:
        build_dashboard_snapshot(db)
        AirportDatabase(db).create_airport('JFK', 22000, 40.6413, -73.7781, ['RWY 1', 'RWY 2'])
        runways = build_dashboard_snapshot(db)['runways']
        assert [runway['id'] for runway in runways] == ['RWY 1', 'RWY 2']
        assert all(runway['airport_id'] == 'JFK' for runway in runways)  # Tower's own runway records
    finally:
        db.close()


def test_snapshot_falls_back_to_shared_aircraft_state():
    db = create_database(':memory:')
    try:
        db.upsert_shared_state('aircraft:EXT1', {'id': 'EXT1', 'latitude': 40.64, 'longitude': -73.78, 'status': 'Airborne', 'altitude': 900, 'timestamp': '2026-11-05T12:00:00'}, category='aircraft')
        (plane,) = build_dashboard_snapshot(db)['aircraft']
        assert (plane['id'], plane['map_state']) == ('EXT1', 'position')
    finally:
        db.close()


def test_unsafe_separation_pairs_are_listed_and_degrade_the_status():
    # REQ-TWR-011: Tower's active unsafe pairs, as shown by Command; resolved ones are not.
    db = create_database(':memory:')
    try:
        db.upsert_shared_state('tower_r2_separation:["AC1", "AC2"]', {'aircraft_ids': ['AC1', 'AC2'], 'active': True, 'severity': 1}, category='tower_r2_separation')
        db.upsert_shared_state('tower_r2_separation:["AC3", "AC4"]', {'aircraft_ids': ['AC3', 'AC4'], 'active': False, 'severity': 1}, category='tower_r2_separation')
        snapshot = build_dashboard_snapshot(db)
        assert snapshot['system_status'] == 'degraded'
        assert snapshot['unsafe_pairs'] == [['AC1', 'AC2']]
        assert {'status': 'degraded', 'message': 'Unsafe Separation: AC1 / AC2'} in snapshot['decisions']
    finally:
        db.close()
