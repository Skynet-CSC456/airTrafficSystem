"""Production consumer acknowledgements and rollback, not test-only consumers."""
from contextlib import closing
import pytest
from src.integration.application import Application
from src.integration.consumers import consume
from src.db import create_database, Team
from src.tower.messaging import process_requests


def prepare(path):
    app=Application(path)
    app.create_aircraft(dict(id='A',latitude=40.6563,longitude=-73.7781,altitude=500,status='Landing'))
    app.request_clearance(dict(request_id='first',aircraft_id='A',operation='landing'))
    with closing(create_database(path)) as db:process_requests(db)
    return app


def test_human_read_does_not_prevent_actual_application_and_receipts_are_idempotent(tmp_path):
    path=str(tmp_path/'receipts.db');app=prepare(path)
    with closing(create_database(path)) as human:
        human.receive_messages(Team.RADAR,mark_as_read=True)
    with closing(create_database(path)) as radar:
        assert consume(radar,Team.RADAR)==1
        first=radar.get_shared_state('radar_clearance:A')
        assert consume(radar,Team.RADAR)==0
        assert radar.get_shared_state('radar_clearance:A')==first
    state=app.snapshot()
    radar_event=next(e for e in state['events'] if e['recipient']=='radar')
    command_event=next(e for e in state['events'] if e['recipient']=='command')
    assert radar_event['status']=='applied' and radar_event['receipt']['applied_at']
    assert command_event['status']=='sent' and command_event['receipt'] is None
    with closing(create_database(path)) as command:assert consume(command,Team.COMMAND)==1
    assert next(e for e in app.snapshot()['events'] if e['recipient']=='command')['status']=='received'


def test_failed_radar_write_cannot_claim_application(tmp_path,monkeypatch):
    path=str(tmp_path/'rollback.db');prepare(path)
    with closing(create_database(path)) as db:
        original=db.upsert_shared_state
        def fail(*args,**kwargs):raise RuntimeError('failed persistence')
        monkeypatch.setattr(db,'upsert_shared_state',fail)
        with pytest.raises(RuntimeError,match='failed persistence'):consume(db,Team.RADAR)
        assert db.get_shared_state('radar_clearance:A') is None
        assert db.connection.execute("SELECT COUNT(*) FROM integration_receipts WHERE consumer='radar'").fetchone()[0]==0
        assert len(db.receive_messages(Team.RADAR,unread_only=True))==1
        monkeypatch.setattr(db,'upsert_shared_state',original)
        assert consume(db,Team.RADAR)==1


def test_malformed_response_is_received_but_never_applied(tmp_path):
    path=str(tmp_path/'invalid.db');app=Application(path)
    with closing(create_database(path)) as db:
        db.send_message(Team.TOWER,Team.RADAR,'invalid',message_type='runway_response',metadata={'schema_version':9})
        assert consume(db,Team.RADAR)==1
        assert db.list_shared_state(category='radar_clearance')==[]
    event=app.snapshot()['events'][0]
    assert event['status']=='failed' and event['receipt']['error']
    assert event['receipt']['applied_at'] is None
