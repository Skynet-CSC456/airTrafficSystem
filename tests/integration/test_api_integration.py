"""Real HTTP + independent production Radar/Tower/Command workers; no fake clients."""
import json
import threading
import time
from contextlib import closing
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from src.integration.application import Application
from src.integration.consumers import consume
from src.shared.geometry import classify
from src.integration.runtime import Runtime
from src.integration.server import make_server
from src.db import create_database, Team
from src.radar.aircraft import AircraftStore


@pytest.fixture
def live(tmp_path):
    path=str(tmp_path/'live-demo.db')
    app=Application(path,reset_root=tmp_path)
    runtime=Runtime(app,interval=0.01).start()
    server=make_server(app,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(method, route, data=None, *, generation=None):
        body=json.dumps(data).encode() if data is not None else None
        req=Request(f'http://127.0.0.1:{server.server_port}'+route,data=body,method=method,
                    headers={'Content-Type':'application/json',**({'X-Demo-Generation':generation} if generation else {})})
        try:
            with urlopen(req,timeout=5) as response:return response.status,json.load(response)
        except HTTPError as exc:return exc.code,json.load(exc)
    yield request,app,path
    server.shutdown();server.server_close();thread.join(timeout=5);runtime.close()


def eventually(request, predicate):
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        status,state=request('GET','/api/state')
        assert status==200,state
        if predicate(state):return state
        time.sleep(0.02)
    pytest.fail('Timed out waiting for real integration workers: '+str(state))


def add(request, name, status='Landing', altitude=500, latitude=40.6563):
    code,result=request('POST','/api/aircraft',dict(id=name,latitude=latitude,
        longitude=-73.7781,altitude=altitude,status=status))
    assert code==201,result
    return result


def submit(request,name,operation='landing',request_id=None):
    request_id=request_id or 'request-'+name
    code,result=request('POST','/api/requests',dict(request_id=request_id,aircraft_id=name,operation=operation))
    assert code==202,result
    return result


def test_real_http_six_cases_queue_release_r11_and_applied_events(live,monkeypatch):
    from src.integration.lifecycle import Lifecycle
    monkeypatch.setattr(Lifecycle,"duration",0)
    request,app,path=live
    code,state=request('GET','/api/state')
    assert code==200 and len(state['airport']['runways'])==10
    assert all(w['healthy'] for w in state['workers'].values())
    before=add(request,'OUTSIDE')
    submit(request,'OUTSIDE')
    state=eventually(request,lambda s:any(a['id']=='OUTSIDE' and a['radar_clearance'] for a in s['aircraft']))
    outside=next(a for a in state['aircraft'] if a['id']=='OUTSIDE')
    assert outside['classification']=='outside'
    assert outside['radar_clearance']['outcome']=='cleared'
    assert outside['altitude']==before['altitude']
    add(request,'PARKED','Parked',0,40.6413)
    submit(request,'PARKED','departure')
    for i in range(8):add(request,f'FILL{i}');submit(request,f'FILL{i}')
    for name in ['Q1','Q2']:add(request,name);submit(request,name)
    state=eventually(request,lambda s:len(s['queue'])==2 and all(a['radar_clearance'] for a in s['aircraft']))
    assert [q['aircraft_id'] for q in state['queue']]==['Q1','Q2']
    assert [q['queue_position'] for q in state['queue']]==[1,2]
    assert all(not r['available'] for r in state['airport']['runways'])
    parked=next(a for a in state['aircraft'] if a['id']=='PARKED')
    assert parked['classification']=='inside' and parked['radar_clearance']['operation']=='departure'
    assert request('POST','/api/operations/request-OUTSIDE/begin',{})[0]==200
    assert request('POST','/api/operations/request-OUTSIDE/complete',{})[0]==200
    state=request('GET','/api/state')[1]
    assert [q['aircraft_id'] for q in state['queue']]==['Q1','Q2']
    landed=next(a for a in state['aircraft'] if a['id']=='OUTSIDE')
    assert landed['occupancy']['phase']=='parked'
    assert not next(r for r in state['airport']['runways'] if r['id']==landed['occupancy']['runway_id'])['available']
    assert request('POST','/api/operations/request-PARKED/begin',{})[0]==200
    assert request('POST','/api/operations/request-PARKED/complete',{})[0]==200
    state=eventually(request,lambda s:next(a for a in s['aircraft'] if a['id']=='Q1')['radar_clearance']['outcome']=='cleared')
    assert [q['aircraft_id'] for q in state['queue']]==['Q2']
    assert request('POST','/api/runways',{'runway_id':'R11'})[0]==201
    state=eventually(request,lambda s:len(s['airport']['runways'])==11 and not s['queue'] and
        next(a for a in s['aircraft'] if a['id']=='Q2')['radar_clearance']['outcome']=='cleared' and
        all(e['status'] in ('applied','received') for e in s['events'] if e['sender']=='tower'))
    assert next(a for a in state['aircraft'] if a['id']=='Q2')['radar_clearance']['runway_id']=='R11'
    assert {e['recipient'] for e in state['events'] if e['sender']=='tower'}=={'radar','command'}
    assert any(e['message_type']=='runway_completed' and e['status']=='applied' for e in state['events'])
    assert any(e['sender']=='radar' and e['recipient']=='tower' and e['status']=='processed' for e in state['events'])
    sent_events=len([e for e in state['events'] if e['sender']=='tower'])
    replay=submit(request,'OUTSIDE')
    state=eventually(request,lambda s:any(e['id']==replay['message_id'] and e['status']=='processed' for e in s['events']))
    assert len([e for e in state['events'] if e['sender']=='tower'])==sent_events
    assert len(state['requests'])==12
    assert request('PATCH','/api/aircraft/OUTSIDE',{'altitude':750})[0]==400
    assert request('PATCH','/api/aircraft/Q2',{'altitude':750})[0]==200
    state=eventually(request,lambda s:next(a for a in s['aircraft'] if a['id']=='Q2')['altitude']==750)
    with closing(create_database(path)) as observer:
        state_row=AircraftStore(observer).get('OUTSIDE')
        assert state_row.altitude==0 and state_row.status=='Parked'
        assert AircraftStore(observer).get('Q2').altitude==750
        applied=observer.get_shared_state('radar_clearance:OUTSIDE')['payload']
        assert applied['outcome']=='completed' and applied['applied_at']
        assert consume(observer,Team.RADAR)==0
        assert consume(observer,Team.COMMAND)==0
    # Durable actual Radar integration state survives a new coordinator instance.
    assert next(a for a in Application(path).snapshot()['aircraft'] if a['id']=='Q2')['radar_clearance']['runway_id']=='R11'


@pytest.mark.parametrize('method,route,body,status',[
    ('POST','/api/aircraft',{'id':'A'},400),
    ('POST','/api/aircraft',dict(id='A',latitude=40,longitude=-73,altitude=-1,status='Landing'),400),
    ('POST','/api/aircraft',dict(id='A',latitude=91,longitude=-73,altitude=1,status='Landing'),400),
    ('POST','/api/requests',dict(aircraft_id='MISSING',operation='landing'),404),
    ('POST','/api/requests',dict(aircraft_id='A',operation='teleport'),400),
    ('PATCH','/api/aircraft/MISSING',{'altitude':1},404),
    ('POST','/api/operations/MISSING/complete',{},404),
    ('POST','/api/missing',{},404),
])
def test_api_validation_errors(live,method,route,body,status):
    request,_,_=live
    actual,result=request(method,route,body)
    assert actual==status and result['error']


def test_duplicate_aircraft_and_real_rejection_events(live):
    request,_,_=live
    add(request,'GROUND','Parked',0,40.6413)
    code,result=request('POST','/api/aircraft',dict(id='GROUND',latitude=40.6413,longitude=-73.7781,altitude=0,status='Parked'))
    assert code==409 and result['error']
    submit(request,'GROUND','landing')
    state=eventually(request,lambda s:s['aircraft'][0]['radar_clearance'] is not None)
    assert state['aircraft'][0]['radar_clearance']['outcome']=='rejected'
    assert len([r for r in state['airport']['runways'] if not r['available']])==1
    assert state['aircraft'][0]['occupancy']['phase']=='parked'


def test_geometry_boundaries_are_inclusive():
    assert classify(40.6513,-73.7681)=='inside'
    assert classify(40.65131,-73.7681)=='outside'
