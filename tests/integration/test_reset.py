"""Guarded reset, actual workers/HTTP, rollback and stale-client generations."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import threading
import time
import pytest
from src.integration.application import Application,InputError
from src.integration.lifecycle import Lifecycle
from src.integration import runtime
from src.integration.demo_reset import assert_safe_path
from src.db import create_database
from src.tower.airport import AirportDatabase
from test_api_integration import live,add,submit,eventually
from test_lifecycle import aircraft,ready


def empty(state):
    assert not state['aircraft'] and not state['requests'] and not state['queue'] and not state['events']
    assert [r['id'] for r in state['airport']['runways']]==[f'R{i}' for i in range(1,11)]
    assert all(r['available'] and r['mode']=='mixed' and r['aircraft_id'] is None for r in state['airport']['runways'])


def test_reset_real_lifecycle_contention_promotion_r11_and_new_aircraft(live,monkeypatch):
    request,app,path=live
    monkeypatch.setattr(Lifecycle,'duration',0)
    add(request,'FIRST','Parked',0,40.6413);submit(request,'FIRST','departure','first-departure');ready(request,'FIRST')
    assert request('POST','/api/operations/first-departure/begin',{})[0]==200
    assert request('POST','/api/operations/first-departure/complete',{})[0]==200
    add(request,'ARRIVAL');ready(request,'ARRIVAL')
    assert request('POST','/api/operations/request-ARRIVAL/begin',{})[0]==200
    assert request('POST','/api/operations/request-ARRIVAL/complete',{})[0]==200
    for i in range(9):add(request,f'P{i}','Parked',0,40.6413)
    add(request,'WAIT1');add(request,'WAIT2')
    state=eventually(request,lambda s:len(s['queue'])==2)
    assert [q['aircraft_id'] for q in state['queue']]==['WAIT1','WAIT2']
    submit(request,'ARRIVAL','departure','leave-arrival')
    eventually(request,lambda s:aircraft(s,'ARRIVAL')['radar_clearance']['request_id']=='leave-arrival' and aircraft(s,'ARRIVAL')['radar_clearance']['outcome']=='cleared')
    assert request('POST','/api/operations/leave-arrival/begin',{})[0]==200
    assert request('POST','/api/operations/leave-arrival/complete',{})[0]==200
    ready(request,'WAIT1')
    assert request('POST','/api/runways',{'runway_id':'R11'})[0]==201
    state=ready(request,'WAIT2');token=state['generation']
    with closing(create_database(path)) as db:
        AirportDatabase(db).update_airport('SKYNET',24000,40.6415,-73.7782)
    code,state=request('POST','/api/reset',{},generation=token)
    assert code==200,state
    empty(state);assert state['generation']!=token
    assert (state['airport']['perimeter'],state['airport']['latitude'],state['airport']['longitude'])==(24000,40.6415,-73.7782)
    with closing(create_database(path)) as db:
        for table in ['integration_receipts','tower_processed_messages','messages','tower_requests','aircraft','aircraft_messages']:
            assert db.connection.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0
        for category in ['flight','tower_stationary','radar_clearance','radar_request','runway_intent']:
            assert db.list_shared_state(category=category)==[]
    token=state['generation']
    code,new=request('POST','/api/aircraft',dict(id='AFTER',latitude=40.6413,longitude=-73.7781,altitude=0,status='Parked'),generation=token)
    assert code==201,new
    state=request('GET','/api/state')[1]
    assert len(state['aircraft'])==1 and aircraft(state,'AFTER')['occupancy']['runway_id']=='R1'
    assert all(w['healthy'] for w in state['workers'].values())


def test_reset_rejects_stale_completions_begin_create_and_missing_token(live):
    request,_,_=live
    add(request,'SAME');ready(request,'SAME')
    old=request('GET','/api/state')[1]['generation']
    new=request('POST','/api/reset',{},generation=old)[1]['generation']
    payload=dict(id='SAME',latitude=40.6563,longitude=-73.7781,altitude=500,status='Landing')
    assert request('POST','/api/aircraft',payload,generation=new)[0]==201
    ready(request,'SAME')
    for route,body in [('/api/operations/request-SAME/complete',{}),('/api/operations/request-SAME/begin',{}),('/api/aircraft',{**payload,'id':'STALE'})]:
        code,result=request('POST',route,body,generation=old)
        assert code==409 and 'reset' in result['error']
    assert request('POST','/api/runways',{'runway_id':'R11'})[0]==409
    state=request('GET','/api/state')[1]
    assert len(state['aircraft'])==1 and aircraft(state,'SAME')['status']=='Landing'
    assert aircraft(state,'SAME')['tower_decision']['outcome']=='cleared'


def test_reset_failure_rolls_back_everything_and_keeps_generation(live,monkeypatch):
    request,app,_=live
    add(request,'KEEP','Parked',0,40.6413)
    state=app.snapshot();token=state['generation']
    original=AirportDatabase.update_runway
    def fail(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('reset configuration write failed')
    monkeypatch.setattr(AirportDatabase,'update_runway',fail)
    code,result=request('POST','/api/reset',{},generation=token)
    assert code==500 and 'configuration write failed' in result['error']
    after=app.snapshot()
    assert after['generation']==token and len(after['aircraft'])==1
    assert aircraft(after,'KEEP')['occupancy']==aircraft(state,'KEEP')['occupancy']
    assert sum(not r['available'] for r in after['airport']['runways'])==1


def test_reset_waits_for_active_worker_then_workers_continue(live,monkeypatch):
    request,app,_=live
    add(request,'OLD')
    entered,release=threading.Event(),threading.Event()
    original=runtime.process_requests
    def block(db,**kwargs):
        if not entered.is_set():
            entered.set()
            if not release.wait(4):raise RuntimeError('bounded test worker wait expired')
        return original(db,**kwargs)
    monkeypatch.setattr(runtime,'process_requests',block)
    assert entered.wait(3)
    # Read the token directly; a live snapshot correctly waits at the same barrier.
    with closing(create_database(app.database)) as db:token=db.get_shared_state('demo:generation')['payload']['token']
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(request,'POST','/api/reset',{},generation=token)
        try:
            time.sleep(.05);assert not future.done()
        finally:release.set()
        code,state=future.result(timeout=5)
    assert code==200;empty(state)
    state=eventually(request,lambda s:all(w['healthy'] for w in s['workers'].values()))
    empty(state)


def test_reset_default_guard_refuses_other_database_and_bad_identity(tmp_path):
    app=Application(tmp_path/'outside.db')
    before=Path(app.database).read_bytes()
    with pytest.raises(InputError,match='restricted'):app.reset()
    assert Path(app.database).read_bytes()==before
    allowed=Application(tmp_path/'allowed.db',reset_root=tmp_path)
    with closing(create_database(allowed.database)) as db:
        db.upsert_shared_state('demo:identity',{'path':'wrong','purpose':'wrong'},category='demo_settings')
    with pytest.raises(InputError,match='identity'):allowed.reset()


def test_reset_path_guard_rejects_symlinks_and_hard_links(tmp_path):
    allowed=tmp_path/'allowed';allowed.mkdir()
    other=tmp_path/'other.db';other.write_text('untouched')
    link=allowed/'link.db';link.symlink_to(other)
    with pytest.raises(PermissionError):assert_safe_path(link,allowed)
    hard=allowed/'hard.db';hard.hardlink_to(other)
    with pytest.raises(PermissionError):assert_safe_path(hard,allowed)
    assert other.read_text()=='untouched'


def test_natural_selection_and_departure_completion_timestamp(live,monkeypatch):
    request,_,_=live
    monkeypatch.setattr(Lifecycle,'duration',0)
    add(request,'P1','Parked',0,40.6413);add(request,'P2','Parked',0,40.6413)
    state=request('GET','/api/state')[1]
    assert aircraft(state,'P1')['occupancy']['runway_id']=='R1'
    assert aircraft(state,'P2')['occupancy']['runway_id']=='R2'
    add(request,'LAND');state=ready(request,'LAND')
    assert aircraft(state,'LAND')['tower_decision']['runway_id']=='R3'
    submit(request,'P1','departure','depart')
    ready(request,'P1')
    plan=request('POST','/api/operations/depart/begin',{})[1]
    assert plan['segment_weights']==[.75,.25]
    assert request('POST','/api/operations/depart/complete',{})[0]==200
    state=request('GET','/api/state')[1]
    assert aircraft(state,'P1')['departure_completed_at']>0
    assert aircraft(state,'P1')['status']=='Departing'
    assert aircraft(state,'P1')['flight']['completed_at']>0


def test_new_message_after_preparation_waits_for_natural_selection_pass(tmp_path,monkeypatch):
    from src.tower import occupancy as tower_occupancy
    from src.tower.occupancy import process_requests_with_occupancy
    path=tmp_path/'ordering-race.db';app=Application(path)
    app.create_aircraft(dict(id='P',latitude=40.6413,longitude=-73.7781,altitude=0,status='Parked'))
    app.create_aircraft(dict(id='FIRST',latitude=40.6563,longitude=-73.7781,altitude=500,status='Landing'))
    original=tower_occupancy.process_requests
    injected=False
    def append_then_process(db,**kwargs):
        nonlocal injected
        if not injected:
            injected=True
            app.create_aircraft(dict(id='NEW',latitude=40.6563,longitude=-73.7781,altitude=500,status='Landing'))
        return original(db,**kwargs)
    monkeypatch.setattr(tower_occupancy,'process_requests',append_then_process)
    with closing(create_database(path)) as db:
        process_requests_with_occupancy(db)
        tower=tower_occupancy.OccupancyTowerService(db)
        assert tower.get_decision('request-NEW') is None
        process_requests_with_occupancy(db)
        assert tower.get_decision('request-FIRST')['runway_id']=='R2'
        assert tower.get_decision('request-NEW')['runway_id']=='R3'


def test_reset_guard_rejects_symlinked_demo_directory(tmp_path):
    actual=tmp_path/'actual';actual.mkdir()
    database=actual/'demo.db';database.write_text('preserved')
    directory=tmp_path/'data';directory.symlink_to(actual,target_is_directory=True)
    with pytest.raises(PermissionError,match='symlink'):
        assert_safe_path(directory/'demo.db',directory)
    assert database.read_text()=='preserved'
