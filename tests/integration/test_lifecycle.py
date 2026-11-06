"""Production API/worker lifecycle checks with fresh DBs and bounded waits."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import pytest
from src.integration.application import Application
from src.integration.lifecycle import Lifecycle
from src.integration import lifecycle
from src.db import create_database
from src.radar.aircraft import AircraftStore
from src.tower.service import TowerService
from test_api_integration import live, eventually, add, submit


def aircraft(state,name):
    return next(a for a in state['aircraft'] if a['id']==name)


def ready(request,name):
    return eventually(request,lambda s:aircraft(s,name)['radar_clearance'] and aircraft(s,name)['radar_clearance']['outcome']=='cleared')


def test_creation_rules_automatic_exactly_once_and_restart(live):
    request,app,path=live
    add(request,'GROUND','Parked',0,40.6413)
    assert request('GET','/api/state')[1]['requests']==[]
    for payload in [dict(id='BAD',latitude=40.6413,longitude=-73.7781,altitude=5,status='Parked'),
                    dict(id='BAD',latitude=40.6563,longitude=-73.7781,altitude=0,status='Parked'),
                    dict(id='BAD',latitude=40.6413,longitude=-73.7781,altitude=500,status='Landing'),
                    dict(id='BAD',latitude=40.6563,longitude=-73.7781,altitude=0,status='Landing'),
                    dict(id='BAD',latitude=40.6413,longitude=-73.7781,altitude=0,status='Departing')]:
        assert request('POST','/api/aircraft',payload)[0]==400
    payload=dict(id='AIR',latitude=40.6563,longitude=-73.7781,altitude=500,status='Landing')
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _:request('POST','/api/aircraft',payload)[0],range(4)))==[201]*4
    state=ready(request,'AIR')
    Application(path).create_aircraft(payload)
    state=request('GET','/api/state')[1]
    assert len([e for e in state['events'] if e['message_type']=='runway_request'])==1
    assert len(state['requests'])==1
    assert aircraft(state,'AIR')['status']=='Landing'
    assert aircraft(state,'AIR')['flight'] is None


def test_hold_queue_stationary_fifo_and_r11_stable_positions(live,monkeypatch):
    request,_,path=live
    monkeypatch.setattr(Lifecycle,'duration',0)
    assert request('POST','/api/hold',{'enabled':True})[0]==200
    for i in range(12):add(request,f'A{i:02}')
    state=eventually(request,lambda s:len(s['queue'])==2 and all(a['radar_clearance'] for a in s['aircraft']))
    assert [q['aircraft_id'] for q in state['queue']]==['A10','A11']
    assert len([r for r in state['airport']['runways'] if not r['available']])==10
    for name in ['A10','A11']:
        a=aircraft(state,name)
        assert a['latitude']==40.6563 and a['altitude']==500 and a['flight'] is None
        assert request('POST',f'/api/operations/request-{name}/begin',{})[0]==400
    assert request('POST','/api/operations/request-A00/begin',{'automatic':True})[0]==409
    assert request('POST','/api/operations/request-A00/begin',{})[0]==200
    assert request('POST','/api/operations/request-A00/complete',{})[0]==200
    state=request('GET','/api/state')[1]
    assert [q['aircraft_id'] for q in state['queue']]==['A10','A11']
    submit(request,'A00','departure','depart-A00')
    eventually(request,lambda s:aircraft(s,'A00')['radar_clearance']['operation']=='departure' and aircraft(s,'A00')['radar_clearance']['outcome']=='cleared')
    assert request('POST','/api/operations/depart-A00/begin',{})[0]==200
    assert request('POST','/api/operations/depart-A00/complete',{})[0]==200
    state=ready(request,'A10')
    assert [q['aircraft_id'] for q in state['queue']]==['A11']
    old_positions={r['id']:r['points'] for r in state['airport']['runways']}
    assert request('POST','/api/runways',{'runway_id':'R11'})[0]==201
    state=ready(request,'A11')
    assert not state['queue'] and state['hold_operations']
    assert [r['id'] for r in state['airport']['runways']]==[f'R{i}' for i in range(1,12)]
    assert all(r['points']==old_positions[r['id']] for r in state['airport']['runways'] if r['id']!='R11')
    assert aircraft(state,'A10')['flight'] is None
    with closing(create_database(path)) as db:
        requests=TowerService(db).snapshot()['requests']
        assert len({r['runway_id'] for r in requests if r['outcome']=='cleared'})==11


def test_landing_then_departure_and_idempotent_completion(live,monkeypatch):
    request,_,path=live
    monkeypatch.setattr(Lifecycle,'duration',0)
    add(request,'ROUNDTRIP');ready(request,'ROUNDTRIP')
    first=request('POST','/api/operations/request-ROUNDTRIP/begin',{})[1]
    assert request('POST','/api/operations/request-ROUNDTRIP/begin',{})[1]==first
    assert request('POST','/api/operations/request-ROUNDTRIP/complete',{})[0]==200
    state=eventually(request,lambda s:aircraft(s,'ROUNDTRIP')['radar_clearance']['outcome']=='completed')
    landed=aircraft(state,'ROUNDTRIP')
    assert landed['status']=='Parked' and landed['altitude']==0 and landed['classification']=='inside'
    count=len(state['events'])
    assert request('POST','/api/operations/request-ROUNDTRIP/complete',{})[0]==200
    assert len(request('GET','/api/state')[1]['events'])==count
    departure=submit(request,'ROUNDTRIP','departure','departure-roundtrip')
    duplicate=submit(request,'ROUNDTRIP','departure','another-departure-id')
    assert duplicate==departure
    state=ready(request,'ROUNDTRIP')
    assert aircraft(state,'ROUNDTRIP')['status']=='Parked' # Tower validated before transition
    assert request('POST','/api/operations/departure-roundtrip/begin',{})[0]==200
    assert aircraft(request('GET','/api/state')[1],'ROUNDTRIP')['flight']['request_id']=='departure-roundtrip'
    assert request('POST','/api/operations/departure-roundtrip/complete',{})[0]==200
    state=eventually(request,lambda s:aircraft(s,'ROUNDTRIP')['radar_clearance']['outcome']=='completed' and aircraft(s,'ROUNDTRIP')['status']=='Departing')
    departed=aircraft(state,'ROUNDTRIP')
    assert departed['altitude']>0 and departed['classification']=='outside'
    assert all(r['available'] for r in state['airport']['runways'])
    with closing(create_database(path)) as db:assert AircraftStore(db).get('ROUNDTRIP').status=='Departing'
    assert {e['recipient'] for e in state['events'] if e['message_type']=='runway_completed'}=={'radar','command'}


@pytest.mark.parametrize('failure',['radar','tower'])
def test_failed_completion_retains_reservation_and_retry_is_safe(live,monkeypatch,failure):
    request,_,_=live
    monkeypatch.setattr(Lifecycle,'duration',0)
    add(request,'FAIL');ready(request,'FAIL')
    request('POST','/api/operations/request-FAIL/begin',{})
    if failure=='radar':
        target=lifecycle;name='update_aircraft_position'
    else:target=TowerService;name='complete_request'
    original=getattr(target,name)
    def fail(*args,**kwargs):raise RuntimeError('injected '+failure+' failure')
    monkeypatch.setattr(target,name,fail)
    assert request('POST','/api/operations/request-FAIL/complete',{})[0]==500
    state=request('GET','/api/state')[1]
    a=aircraft(state,'FAIL')
    assert a['tower_decision']['outcome']=='cleared' and a['flight']['phase']=='failed'
    assert len([r for r in state['airport']['runways'] if not r['available']])==1
    assert not any(e['message_type']=='runway_completed' for e in state['events'])
    assert a['status']=='Landing'  # both service failures roll back Radar atomically
    monkeypatch.setattr(target,name,original)
    assert request('POST','/api/operations/request-FAIL/complete',{})[0]==200
    state=eventually(request,lambda s:aircraft(s,'FAIL')['radar_clearance']['outcome']=='completed')
    assert aircraft(state,'FAIL')['occupancy']['phase']=='parked'
    assert len([r for r in state['airport']['runways'] if not r['available']])==1


def test_rejected_departure_never_transitions_or_starts(live):
    request,_,_=live
    add(request,'AIR');ready(request,'AIR')
    assert request('POST','/api/requests',dict(aircraft_id='AIR',operation='departure',request_id='invalid-departure'))[0]==400
    add(request,'GROUND','Parked',0,40.6413)
    submit(request,'GROUND','landing','ground-landing')
    state=eventually(request,lambda s:aircraft(s,'GROUND')['radar_clearance'] is not None)
    assert aircraft(state,'GROUND')['tower_decision']['outcome']=='rejected'
    assert request('POST','/api/operations/ground-landing/begin',{})[0]==400
    assert aircraft(state,'GROUND')['status']=='Parked'
    assert aircraft(state,'GROUND')['flight'] is None


def test_persisted_pause_resume_early_completion_and_independent_plans(live,monkeypatch):
    from types import SimpleNamespace
    from src.integration import application
    request,app,path=live
    clock=[1000.]
    fake=SimpleNamespace(time=lambda:clock[0])
    monkeypatch.setattr(lifecycle,'time',fake)
    monkeypatch.setattr(application,'time',fake)
    for name in ['ONE','TWO']:add(request,name);ready(request,name)
    plans=[request('POST',f'/api/operations/request-{name}/begin',{})[1] for name in ['ONE','TWO']]
    assert len({p['runway_id'] for p in plans})==2
    assert plans[0]['points'][1]['longitude']!=plans[1]['points'][1]['longitude']
    assert request('POST','/api/operations/request-ONE/complete',{})[0]==400
    assert request('PATCH','/api/aircraft/ONE',{'altitude':900})[0]==409
    clock[0]+=2
    request('POST','/api/hold',{'enabled':True})
    restarted=Application(path).snapshot()
    assert all(a['flight']['paused_at']==1002 for a in restarted['aircraft'])
    clock[0]+=50
    assert request('POST','/api/operations/request-ONE/complete',{})[0]==400
    request('POST','/api/operations/request-ONE/begin',{})
    assert aircraft(app.snapshot(),'ONE')['flight']['started_at']==1050
    assert aircraft(app.snapshot(),'TWO')['flight']['paused_at']==1002
    clock[0]+=6
    assert request('POST','/api/operations/request-ONE/complete',{})[0]==200
    assert aircraft(app.snapshot(),'TWO')['tower_decision']['outcome']=='cleared'
    request('POST','/api/hold',{'enabled':False})
    assert not aircraft(app.snapshot(),'TWO')['flight'].get('paused_at')
