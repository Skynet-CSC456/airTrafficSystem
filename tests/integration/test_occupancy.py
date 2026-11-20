"""Authoritative stationary runway extension; real HTTP/independent workers."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import pytest
from src.integration.application import Application
from src.integration.lifecycle import Lifecycle
from src.tower.occupancy import OccupancyTowerService, process_requests_with_occupancy
from src.db import create_database,Team
from src.radar.aircraft import AircraftStore
from src.tower.airport import AirportDatabase
from src.tower.service import TowerService
from test_api_integration import live,add,submit,eventually
from test_lifecycle import aircraft,ready


def finish(request,request_id):
    assert request('POST',f'/api/operations/{request_id}/begin',{})[0]==200
    assert request('POST',f'/api/operations/{request_id}/complete',{})[0]==200


def test_ten_parked_two_waiters_departure_and_landing_retention(live,monkeypatch):
    request,app,path=live
    monkeypatch.setattr(Lifecycle,'duration',0)
    for i in range(10):add(request,f'P{i}','Parked',0,40.6413)
    state=request('GET','/api/state')[1]
    owners=[a['occupancy']['runway_id'] for a in state['aircraft']]
    assert len(set(owners))==10
    assert all(a['status']=='Parked' and a['altitude']==0 and a['classification']=='inside' for a in state['aircraft'])
    assert state['requests']==[]  # parking is not a fabricated departure request
    with closing(create_database(path)) as db:
        assert all(not r['available'] for r in TowerService(db).airports.list_runways('SKYNET'))
    code,error=request('POST','/api/aircraft',dict(id='FULL',latitude=40.6413,longitude=-73.7781,altitude=0,status='Parked'))
    assert code==400 and 'full' in error['error'].lower()
    assert len(app.snapshot()['aircraft'])==10
    with closing(create_database(path)) as db:assert AircraftStore(db).get('FULL') is None
    for name in ['WAIT1','WAIT2']:add(request,name)
    state=eventually(request,lambda s:len(s['queue'])==2 and all(aircraft(s,n)['radar_clearance'] for n in ['WAIT1','WAIT2']))
    assert [q['aircraft_id'] for q in state['queue']]==['WAIT1','WAIT2']
    for name in ['WAIT1','WAIT2']:
        assert aircraft(state,name)['latitude']==40.6563 and aircraft(state,name)['flight'] is None
    occupied=aircraft(state,'P0')['occupancy']['runway_id']
    sent=submit(request,'P0','departure','leave-P0')
    state=ready(request,'P0')
    assert aircraft(state,'P0')['tower_decision']['runway_id']==occupied
    assert aircraft(state,'P0')['status']=='Parked' and len(state['queue'])==2
    assert submit(request,'P0','departure','duplicate-departure')==sent
    plan=request('POST','/api/operations/leave-P0/begin',{})[1]
    assert all(a['latitude']<=b['latitude'] for a,b in zip(plan['points'],plan['points'][1:]))
    assert len({p['longitude'] for p in plan['points']})==1 # straight upward
    assert request('POST','/api/operations/leave-P0/complete',{})[0]==200
    state=ready(request,'WAIT1')
    assert aircraft(state,'WAIT1')['tower_decision']['runway_id']==occupied
    assert aircraft(state,'P0')['status']=='Departing' and aircraft(state,'P0')['altitude']>0
    assert aircraft(state,'P0')['classification']=='outside' and aircraft(state,'P0')['occupancy'] is None
    assert [q['aircraft_id'] for q in state['queue']]==['WAIT2']
    plan=request('POST','/api/operations/request-WAIT1/begin',{})[1]
    assert all(a['latitude']>=b['latitude'] for a,b in zip(plan['points'],plan['points'][1:]))
    assert request('POST','/api/operations/request-WAIT1/complete',{})[0]==200
    state=eventually(request,lambda s:aircraft(s,'WAIT1')['radar_clearance']['outcome']=='completed')
    landed=aircraft(state,'WAIT1')
    assert landed['status']=='Parked' and landed['altitude']==0
    assert landed['occupancy']['runway_id']==occupied
    assert [q['aircraft_id'] for q in state['queue']]==['WAIT2']
    assert all(not r['available'] for r in state['airport']['runways'])
    assert request('POST','/api/runways',{'runway_id':'R11'})[0]==201
    state=ready(request,'WAIT2')
    assert aircraft(state,'WAIT2')['tower_decision']['runway_id']=='R11'
    assert [r['id'] for r in state['airport']['runways']]==[f'R{i}' for i in range(1,12)]
    assert [r['points'][0]['longitude'] for r in state['airport']['runways']]==sorted(r['points'][0]['longitude'] for r in state['airport']['runways'])
    state=eventually(request,lambda s:all(e['status'] in ('applied','received') for e in s['events'] if e['sender']=='tower'))
    assert {e['recipient'] for e in state['events'] if e['message_type']=='runway_completed'}=={'radar','command'}
    before=len(state['events'])
    assert request('POST','/api/operations/request-WAIT1/complete',{})[0]==200
    assert len(app.snapshot()['events'])==before
    assert aircraft(Application(path).snapshot(),'WAIT1')['occupancy']['runway_id']==occupied


def test_concurrent_parked_creation_no_duplicate_or_orphan(live):
    request,_,path=live
    def create(i):
        return request('POST','/api/aircraft',dict(id=f'C{i}',latitude=40.6413,longitude=-73.7781,altitude=0,status='Parked'))[0]
    with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(create,range(11)))
    assert sorted(results)==[201]*10+[400]
    with closing(create_database(path)) as db:
        assert len(AircraftStore(db).list())==10
        owners=OccupancyTowerService(db).occupancy()
        assert len(owners)==len({r['runway_id'] for r in owners})==10


@pytest.mark.parametrize('failure',['radar','closure'])
def test_park_creation_rolls_back_existing_store_and_tower(live,monkeypatch,failure):
    request,app,_=live
    cls,name=(AircraftStore,'create') if failure=='radar' else (AirportDatabase,'update_runway_status')
    original=getattr(cls,name)
    def fail_after_write(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('failure after '+failure+' write')
    monkeypatch.setattr(cls,name,fail_after_write)
    payload=dict(id='RETRY',latitude=40.6413,longitude=-73.7781,altitude=0,status='Parked')
    assert request('POST','/api/aircraft',payload)[0]==500
    state=app.snapshot()
    assert not state['aircraft'] and all(r['available'] for r in state['airport']['runways'])
    monkeypatch.setattr(cls,name,original)
    assert request('POST','/api/aircraft',payload)[0]==201
    assert len(app.snapshot()['aircraft'])==1


def test_landing_transfer_failure_rolls_back_radar_completion_messages_and_occupancy(live,monkeypatch):
    request,app,_=live
    monkeypatch.setattr(Lifecycle,'duration',0)
    add(request,'ARRIVE');ready(request,'ARRIVE')
    request('POST','/api/operations/request-ARRIVE/begin',{})
    original=OccupancyTowerService._record
    def fail(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise RuntimeError('failed transfer')
    monkeypatch.setattr(OccupancyTowerService,'_record',fail)
    assert request('POST','/api/operations/request-ARRIVE/complete',{})[0]==500
    state=app.snapshot();a=aircraft(state,'ARRIVE')
    assert a['status']=='Landing' and a['altitude']==500
    assert a['tower_decision']['outcome']=='cleared' and a['occupancy'] is None
    assert not any(e['message_type']=='runway_completed' for e in state['events'])
    assert sum(not r['available'] for r in state['airport']['runways'])==1
    monkeypatch.setattr(OccupancyTowerService,'_record',original)
    assert request('POST','/api/operations/request-ARRIVE/complete',{})[0]==200
    assert aircraft(app.snapshot(),'ARRIVE')['occupancy']['phase']=='parked'


def test_worker_retry_after_preparation_emits_no_duplicate_events(tmp_path,monkeypatch):
    from src.tower import occupancy as tower_occupancy
    path=str(tmp_path/'crash.db');app=Application(path)
    app.create_aircraft(dict(id='P',latitude=40.6413,longitude=-73.7781,altitude=0,status='Parked'))
    sent=app.request_clearance(dict(aircraft_id='P',operation='departure',request_id='leave'))
    with closing(create_database(path)) as db:
        original=tower_occupancy.process_requests
        def crash(*args,**kwargs):raise RuntimeError('worker interrupted after preparation')
        monkeypatch.setattr(tower_occupancy,'process_requests',crash)
        with pytest.raises(RuntimeError):process_requests_with_occupancy(db)
        assert TowerService(db).get_decision('leave')['outcome']=='cleared'
        before=len(db.receive_messages(Team.RADAR))+len(db.receive_messages(Team.COMMAND))
        monkeypatch.setattr(tower_occupancy,'process_requests',original)
        assert process_requests_with_occupancy(db)[0]['outcome']=='cleared'
        assert process_requests_with_occupancy(db)==[]
        assert len(db.receive_messages(Team.RADAR))+len(db.receive_messages(Team.COMMAND))==before==2
        assert db.connection.execute('SELECT COUNT(*) FROM tower_processed_messages WHERE message_id=?',(sent['message_id'],)).fetchone()[0]==1


def test_owned_departure_recovers_if_original_processor_queues_it_first(tmp_path):
    from src.tower.messaging import process_requests
    path=str(tmp_path/'scan-race.db');app=Application(path)
    app.create_aircraft(dict(id='P',latitude=40.6413,longitude=-73.7781,altitude=0,status='Parked'))
    app.request_clearance(dict(aircraft_id='P',operation='departure',request_id='leave'))
    with closing(create_database(path)) as db:
        assert process_requests(db)[0]['outcome']=='queued'
        process_requests_with_occupancy(db)
        decision=TowerService(db).get_decision('leave')
        assert decision['outcome']=='cleared'
        assert decision['runway_id']==OccupancyTowerService(db).occupancy(aircraft_id='P')[0]['runway_id']
        count=len(db.receive_messages(Team.RADAR))
        process_requests_with_occupancy(db)
        assert len(db.receive_messages(Team.RADAR))==count


def test_independent_radar_producer_uses_existing_contract_without_api_intent(tmp_path):
    from datetime import datetime,timezone
    path=str(tmp_path/'independent.db');app=Application(path)
    app.create_aircraft(dict(id='P',latitude=40.6413,longitude=-73.7781,altitude=0,status='Parked'))
    with closing(create_database(path)) as producer:
        producer.send_message(Team.RADAR,Team.TOWER,'Depart P',message_type='runway_request',metadata={
            'schema_version':1,'request_id':'external','aircraft_id':'P','airport_id':'SKYNET',
            'operation':'departure','timestamp':datetime.now(timezone.utc).isoformat()})
    with closing(create_database(path)) as worker:
        assert process_requests_with_occupancy(worker)[0]['outcome']=='cleared'
        assert TowerService(worker).get_decision('external')['runway_id']==OccupancyTowerService(worker).occupancy()[0]['runway_id']


def test_malformed_departure_does_not_transfer_stationary_ownership(tmp_path):
    path=str(tmp_path/'malformed.db');app=Application(path)
    app.create_aircraft(dict(id='P',latitude=40.6413,longitude=-73.7781,altitude=0,status='Parked'))
    with closing(create_database(path)) as db:
        db.send_message(Team.RADAR,Team.TOWER,'Bad',message_type='runway_request',metadata={
            'schema_version':1,'request_id':'invalid','aircraft_id':'P','airport_id':'SKYNET',
            'operation':'departure','timestamp':'not-a-time'})
        assert process_requests_with_occupancy(db)[0]['outcome']=='rejected'
        assert OccupancyTowerService(db).occupancy()[0]['phase']=='parked'
        assert sum(not r['available'] for r in TowerService(db).airports.list_runways('SKYNET'))==1


def test_cancelling_owned_departure_retains_parked_runway(tmp_path):
    path=str(tmp_path/'cancel.db');app=Application(path)
    app.create_aircraft(dict(id='P',latitude=40.6413,longitude=-73.7781,altitude=0,status='Parked'))
    app.request_clearance(dict(aircraft_id='P',operation='departure',request_id='leave'))
    with closing(create_database(path)) as db:
        process_requests_with_occupancy(db)
        tower=OccupancyTowerService(db)
        assert tower.cancel_request('leave')['outcome']=='rejected'
        assert tower.occupancy()[0]['phase']=='parked'
        assert sum(not r['available'] for r in tower.airports.list_runways('SKYNET'))==1


def test_landing_creation_message_failure_leaves_no_orphan_and_retry_sends_once(live,monkeypatch):
    from src.db import MessageDatabase
    request,app,_=live
    original=MessageDatabase.send_message
    def fail_request(self,sender,recipient,*args,**kwargs):
        if sender==Team.RADAR and recipient==Team.TOWER:raise RuntimeError('request write failed')
        return original(self,sender,recipient,*args,**kwargs)
    monkeypatch.setattr(MessageDatabase,'send_message',fail_request)
    payload=dict(id='RETRY',latitude=40.6563,longitude=-73.7781,altitude=500,status='Landing')
    assert request('POST','/api/aircraft',payload)[0]==500
    state=app.snapshot()
    assert state['aircraft']==[] and state['events']==[] and state['requests']==[]
    monkeypatch.setattr(MessageDatabase,'send_message',original)
    assert request('POST','/api/aircraft',payload)[0]==201
    assert request('POST','/api/aircraft',payload)[0]==201
    state=ready(request,'RETRY')
    assert len([e for e in state['events'] if e['message_type']=='runway_request'])==1


def test_independent_coordinators_cannot_claim_last_runway_twice(tmp_path):
    path=str(tmp_path/'independent-claims.db');Application(path)
    with closing(create_database(path)) as db:
        tower=OccupancyTowerService(db)
        for r in tower.airports.list_runways('SKYNET'):
            if r['id']!='R1':tower.airports.update_runway_status('SKYNET',r['id'],False)
    def create(name):
        try:
            Application(path).create_aircraft(dict(id=name,latitude=40.6413,longitude=-73.7781,altitude=0,status='Parked'))
            return 'created'
        except ValueError as exc:
            assert 'full' in str(exc).lower()
            return 'full'
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(create,['ONE','TWO']))
    assert sorted(result)==['created','full']
    with closing(create_database(path)) as db:
        assert len(AircraftStore(db).list())==1
        owners=OccupancyTowerService(db).occupancy()
        assert len(owners)==1 and owners[0]['runway_id']=='R1'
