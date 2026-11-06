"""REQ-TWR-007, REQ-TWR-008, REQ-TWR-009: real Tower contracts, transactions, FIFO and compatibility."""
import concurrent.futures
import threading

import pytest

from src.db import Team, create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.tower.airport import AirportDatabase, current_timestamp
from src.tower.clearance import publish_runway_decision
from src.tower.messaging import assign_runway_to_aircraft, process_requests
from src.tower.service import TowerService


@pytest.fixture
def system(tmp_path):
    path = str(tmp_path / 'tower.db')
    db = create_database(path)
    service = TowerService(db)
    service.airports.create_airport('JFK', 22000, 40.6413, -73.7781, ['R1'])
    store = AircraftStore(db)
    for aircraft_id, status, altitude in [('A','Landing',500), ('B','Landing',600),
                                          ('C','Landing',700), ('D','Parked',0),
                                          ('E','Departing',900)]:
        store.create(AircraftState(aircraft_id,40,-73,status,altitude,'2026-11-01T12:00:00'))
    yield db, service, store, path
    db.close()


def request(service, request_id='r1', aircraft_id='A', **kwargs):
    return service.request_runway(request_id,aircraft_id,'JFK','landing',**kwargs)


def incoming(db, request_id='r1', aircraft_id='A', **changes):
    metadata = dict(schema_version=1,request_id=request_id,aircraft_id=aircraft_id,
                    airport_id='JFK',operation='landing',timestamp=current_timestamp())
    metadata.update(changes)
    return db.send_message(Team.RADAR,Team.TOWER,'Request runway',message_type='runway_request',metadata=metadata)


def test_landing_and_departure_share_one_allocator(system):
    db,service,store,_ = system
    landing = request(service)
    departure = service.request_runway('d1','D','JFK','departure')
    assert landing['outcome'] == 'cleared'
    assert departure['outcome'] == 'queued'
    assert store.get('D').status == 'Parked'  # Tower does not pretend Radar applied its decision.
    service.release_runway('JFK','R1','r1')
    assert service.get_decision('d1')['outcome'] == 'cleared'
    assert service.get_decision('r1')['outcome'] == 'completed'
    assert service.airports.list_runways('JFK')[0]['aircraft_id'] == 'D'
    assert {m.metadata['operation'] for m in db.receive_messages(Team.RADAR)} == {'landing','departure'}


@pytest.mark.parametrize('aircraft_id,airport_id,operation,runway,reason',[
    ('NOPE','JFK','landing',None,'Unknown aircraft'),
    ('A','NOPE','landing',None,'Unknown airport'),
    ('A','JFK','landing','NOPE','Unknown runway'),
    ('D','JFK','landing',None,'airborne'),
    ('A','JFK','departure',None,'grounded'),
    ('A','JFK','invalid',None,'Operation'),
])
def test_invalid_requests_are_persisted_rejections(system,aircraft_id,airport_id,operation,runway,reason):
    db,service,_,_ = system
    response = service.request_runway('bad',aircraft_id,airport_id,operation,runway_id=runway)
    assert response['outcome'] == 'rejected'
    assert reason in response['reason']
    assert service.get_decision('bad') == response
    assert service.airports.list_runways('JFK')[0]['available']
    assert len(db.receive_messages(Team.RADAR)) == len(db.receive_messages(Team.COMMAND)) == 1


def test_compatibility_is_operation_specific(system):
    _,service,_,_ = system
    service.airports.update_runway('JFK','R1',available=True,mode='departure')
    assert request(service)['outcome'] == 'rejected'
    assert service.request_runway('d','D','JFK','departure')['outcome'] == 'cleared'


def test_fifo_positions_promotion_and_idempotent_completion(system):
    db,service,_,_ = system
    assert request(service,'r1','A')['outcome'] == 'cleared'
    assert request(service,'r2','B')['queue_position'] == 1
    assert request(service,'r3','C')['queue_position'] == 2
    first = service.complete_request('r1')
    before = len(db.receive_messages(Team.RADAR))
    assert service.complete_request('r1') == first
    assert len(db.receive_messages(Team.RADAR)) == before
    assert service.get_decision('r2')['outcome'] == 'cleared'
    assert service.list_queue('JFK')[0]['queue_position'] == 1
    service.complete_request('r2')
    assert service.get_decision('r3')['outcome'] == 'cleared'
    assert service.list_queue('JFK') == []


def test_busy_closed_runway_reopens_without_inventing_availability(system):
    _,service,_,_ = system
    service.airports.update_runway_status('JFK','R1',False)
    assert request(service)['outcome'] == 'queued'
    service.airports.update_runway_status('JFK','R1',True)
    service.promote_queue('JFK')
    assert service.get_decision('r1')['outcome'] == 'cleared'
    with pytest.raises(ValueError,match='active requests'):
        service.airports.update_runway_status('JFK','R1',True)
    assert not service.airports.list_runways('JFK')[0]['available']


def test_strict_fifo_preferred_runway_blocks_overtaking(system):
    _,service,_,_ = system
    service.airports.add_runway('JFK','R2')
    request(service,'r1','A',runway_id='R1')
    request(service,'r2','B',runway_id='R1')
    assert request(service,'r3','C',runway_id='R2')['outcome'] == 'queued'
    service.complete_request('r1')
    assert service.get_decision('r2')['runway_id'] == 'R1'
    assert service.get_decision('r3')['runway_id'] == 'R2'


def test_replay_collision_and_duplicate_active_aircraft(system):
    db,service,_,_ = system
    first = request(service)
    assert request(service) == first
    assert len(db.receive_messages(Team.RADAR)) == 1
    with pytest.raises(ValueError,match='different request'):
        request(service,'r1','B')
    assert request(service,'another','A')['outcome'] == 'rejected'
    assert db.get_shared_state('decision:A')['payload']['request_id'] == 'r1'
    assert not service.airports.list_runways('JFK')[0]['available']


def test_simultaneous_requests_cannot_share_runway(system):
    _,_,_,path = system
    barrier = threading.Barrier(2)
    def submit(aircraft_id):
        db = create_database(path)
        try:
            service = TowerService(db)
            barrier.wait(timeout=5)
            return request(service,'race-'+aircraft_id,aircraft_id)
        finally:
            db.close()
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(submit,['A','B']))
    assert sorted(r['outcome'] for r in responses) == ['cleared','queued']


def test_restart_retains_queue_reservation_and_replay(system):
    _,service,_,path = system
    first = request(service)
    request(service,'r2','B')
    db2 = create_database(path)
    try:
        reopened = TowerService(db2)
        assert reopened.get_decision('r1') == first
        assert not reopened.airports.list_runways('JFK')[0]['available']
        reopened.complete_request('r1')
        assert reopened.get_decision('r2')['outcome'] == 'cleared'
    finally:
        db2.close()


@pytest.mark.parametrize('stage',['second_message','projection'])
def test_assignment_failure_rolls_back_all_effects(system,monkeypatch,stage):
    db,service,_,_ = system
    baseline = db.list_shared_state()
    if stage == 'second_message':
        original = db.send_message
        def fail(sender,recipient,*args,**kwargs):
            if recipient == Team.COMMAND:
                raise RuntimeError('injected failure')
            return original(sender,recipient,*args,**kwargs)
        monkeypatch.setattr(db,'send_message',fail)
    else:
        def fail(*args,**kwargs):
            raise RuntimeError('injected failure')
        monkeypatch.setattr(db,'upsert_shared_state',fail)
    with pytest.raises(RuntimeError,match='injected'):
        request(service)
    assert service.get_decision('r1') is None
    assert db.receive_messages(Team.RADAR) == db.receive_messages(Team.COMMAND) == []
    assert service.airports.list_runways('JFK')[0]['available']
    assert db.list_shared_state() == baseline


def test_completion_failure_preserves_original_reservation_and_queue(system,monkeypatch):
    db,service,_,_ = system
    request(service)
    request(service,'r2','B')
    original = db.send_message
    def fail(sender,recipient,*args,**kwargs):
        if kwargs.get('metadata',{}).get('request_id') == 'r2':
            raise RuntimeError('promotion failed')
        return original(sender,recipient,*args,**kwargs)
    monkeypatch.setattr(db,'send_message',fail)
    with pytest.raises(RuntimeError,match='promotion'):
        service.complete_request('r1')
    assert service.get_decision('r1')['outcome'] == 'cleared'
    assert service.get_decision('r2')['outcome'] == 'queued'
    assert service.airports.list_runways('JFK')[0]['aircraft_id'] == 'A'


def test_worker_processes_real_messages_atomically_and_does_not_claim_text(system):
    db,service,_,_ = system
    text = db.send_message(Team.RADAR,Team.TOWER,'human text')
    other = db.send_message(Team.COMMAND,Team.TOWER,'not Radar',message_type='runway_request')
    incoming(db)
    incoming(db)  # duplicated delivery of one business request
    results = process_requests(db)
    assert len(results) == 2 and results[0] == results[1]
    assert {m.id for m in db.receive_messages(Team.TOWER,unread_only=True)} == {text,other}
    assert len(db.receive_messages(Team.RADAR)) == len(db.receive_messages(Team.COMMAND)) == 1
    assert process_requests(db) == []
    assert service.get_decision('r1')['outcome'] == 'cleared'


@pytest.mark.parametrize('changes',[
    {'schema_version':2}, {'schema_version':True}, {'timestamp':'2026-11-01T12:00:00'},
    {'timestamp':'nonsense'}, {'aircraft_id':''}, {'request_id':None}, {'operation':[]},
])
def test_malformed_messages_are_rejected_without_reservation(system,changes):
    db,service,_,_ = system
    incoming(db,**changes)
    assert process_requests(db)[0]['outcome'] == 'rejected'
    assert service.airports.list_runways('JFK')[0]['available']
    assert db.receive_messages(Team.TOWER,unread_only=True) == []
    assert len(db.receive_messages(Team.RADAR)) == 1


def test_worker_failure_keeps_message_unread_and_no_partial_outgoing(system,monkeypatch):
    db,service,_,_ = system
    incoming(db)
    original = db.send_message
    def fail(sender,recipient,*args,**kwargs):
        if recipient == Team.COMMAND:
            raise RuntimeError('outbox failed')
        return original(sender,recipient,*args,**kwargs)
    monkeypatch.setattr(db,'send_message',fail)
    with pytest.raises(RuntimeError,match='outbox'):
        process_requests(db)
    assert len(db.receive_messages(Team.TOWER,unread_only=True)) == 1
    assert db.receive_messages(Team.RADAR) == []
    assert service.get_decision('r1') is None
    monkeypatch.setattr(db,'send_message',original)
    assert process_requests(db)[0]['outcome'] == 'cleared'


def test_two_workers_claim_one_request_once(system):
    db,_,_,path = system
    incoming(db)
    barrier = threading.Barrier(2)
    def worker(_):
        worker_db = create_database(path)
        try:
            TowerService(worker_db)
            barrier.wait(timeout=5)
            return process_requests(worker_db)
        finally:
            worker_db.close()
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(worker,[1,2]))
    assert sorted(map(len,results)) == [0,1]
    assert len(db.receive_messages(Team.RADAR)) == 1


def test_invalidated_waiter_is_rejected_and_next_waiter_promoted(system):
    _,service,store,_ = system
    request(service)
    request(service,'r2','B')
    request(service,'r3','C')
    store.delete('B')
    service.complete_request('r1')
    assert service.get_decision('r2')['outcome'] == 'rejected'
    assert service.get_decision('r3')['outcome'] == 'cleared'


def test_release_requires_matching_owner(system):
    _,service,_,_ = system
    request(service)
    with pytest.raises(ValueError,match='belong'):
        service.release_runway('JFK','WRONG','r1')
    with pytest.raises(ValueError,match='cleared'):
        request(service,'r2','B')
        service.complete_request('r2')
    with pytest.raises(KeyError):
        service.complete_request('missing')
    assert service.get_decision('r1')['outcome'] == 'cleared'


def test_resource_cleanup_preserves_history_and_blocks_active_requests(system):
    db,service,_,_ = system
    service.airports.add_runway('JFK','R2')
    service.airports.delete_runway('JFK','R2')
    assert len(service.airports.list_runways('JFK')) == 1
    request(service)
    with pytest.raises(ValueError,match='active requests'):
        service.airports.delete_airport('JFK')
    with pytest.raises(ValueError,match='active requests'):
        service.airports.delete_runway('JFK','R1')
    service.complete_request('r1')
    service.airports.delete_airport('JFK')
    assert service.airports.get_airport('JFK') is None
    assert db.list_shared_state(category='runway') == []
    assert db.list_shared_state(category='airport') == []
    assert service.get_decision('r1')['outcome'] == 'completed'
    assert db.connection.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('value',[float('inf'),float('nan'),True,'100'])
def test_airport_rejects_nonfinite_or_nonnumeric_dimensions(system,value):
    _,service,_,_ = system
    with pytest.raises(ValueError):
        service.airports.create_airport('BAD',value,40,-73,['R'])
    assert service.airports.get_airport('BAD') is None


def test_legacy_manual_assignment_uses_persistent_reservations(system):
    db,service,_,_ = system
    result = assign_runway_to_aircraft(db,'A','R1')
    assert result['outcome'] == 'cleared'
    with pytest.raises(ValueError,match='No safe runway'):
        assign_runway_to_aircraft(db,'B','R1')
    assert len(service.snapshot()['requests']) == 1
    assert not service.airports.list_runways('JFK')[0]['available']
    service.complete_request(result['request_id'])
    assert service.airports.list_runways('JFK')[0]['available']


def test_offline_preview_cannot_override_operational_airports(system):
    db,_,_,_ = system
    with pytest.raises(ValueError,match='offline preview'):
        publish_runway_decision(db,{'id':'A','status':'Landing'},[{'id':'R1','available':True}])


def test_shared_transactions_nest_and_do_not_commit_outer_failure(system):
    db,_,_,_ = system
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.send_message(Team.RADAR,Team.TOWER,'must roll back')
            db.upsert_shared_state('rollback',{'a':1})
            raise RuntimeError('abort')
    assert db.receive_messages(Team.TOWER) == []
    assert db.get_shared_state('rollback') is None
    with db.transaction():
        db.upsert_shared_state('keep',1)
        try:
            with db.transaction():
                db.upsert_shared_state('discard',2)
                raise ValueError('inner only')
        except ValueError:
            pass
    assert db.get_shared_state('keep')['payload'] == 1
    assert db.get_shared_state('discard') is None


def test_generic_team_inbox_claims_are_serialized(system):
    db,_,_,path = system
    db.send_message(Team.RADAR,Team.TOWER,'one')
    barrier = threading.Barrier(2)
    def consume(_):
        connection = create_database(path)
        try:
            barrier.wait(timeout=5)
            return connection.receive_messages(Team.TOWER,unread_only=True,mark_as_read=True)
        finally:
            connection.close()
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(consume,[1,2]))
    assert sorted(map(len,results)) == [0,1]


def test_read_inbox_is_not_a_processing_acknowledgement(system):
    db,service,_,_ = system
    incoming(db)
    db.receive_messages(Team.TOWER,mark_as_read=True)
    assert db.receive_messages(Team.TOWER,unread_only=True) == []
    assert process_requests(db)[0]['outcome'] == 'cleared'
    assert service.get_decision('r1')['outcome'] == 'cleared'
    assert process_requests(db) == []


def test_cancellation_releases_and_promotes_without_erasing_history(system):
    db,service,_,_ = system
    request(service)
    request(service,'r2','B')
    request(service,'r3','C')
    assert service.cancel_request('r2')['outcome'] == 'rejected'
    assert service.list_queue('JFK')[0]['queue_position'] == 1
    assert service.cancel_request('r1')['outcome'] == 'rejected'
    assert service.get_decision('r3')['outcome'] == 'cleared'
    before = len(db.receive_messages(Team.RADAR))
    service.cancel_request('r1')
    assert len(db.receive_messages(Team.RADAR)) == before
    service.complete_request('r3')
    service.airports.delete_airport('JFK')
    assert service.airports.list_airports() == []


def test_projection_never_overrides_authoritative_reservation(system):
    db,service,_,_ = system
    request(service)
    key = next(r['key'] for r in db.list_shared_state(category='runway'))
    db.upsert_shared_state(key,{'id':'R1','available':True},category='runway')
    assert request(service,'r2','B')['outcome'] == 'queued'
    assert not service.snapshot()['airports'][0]['runways'][0]['available']


def test_legacy_rejection_does_not_seed_or_write_decisions(system):
    db,service,_,_ = system
    with pytest.raises(ValueError,match='not found'):
        assign_runway_to_aircraft(db,'A','missing')
    assert service.snapshot()['requests'] == []


def test_original_local_crud_schema_is_upgraded_without_data_loss(tmp_path):
    db = create_database(str(tmp_path/'old.db'))
    with db.connection:
        db.connection.execute('CREATE TABLE airports (airport_id TEXT PRIMARY KEY, perimeter REAL, latitude REAL, longitude REAL)')
        db.connection.execute('CREATE TABLE runways (airport_id TEXT, runway_id TEXT, available INTEGER, timestamp TEXT, PRIMARY KEY (airport_id,runway_id))')
        db.connection.execute("INSERT INTO airports VALUES ('OLD',1000,40,-73)")
        db.connection.execute("INSERT INTO runways VALUES ('OLD','R',0,'2026-01-01T00:00:00')")
    airports = AirportDatabase(db)
    assert airports.get_airport('OLD')['runways'][0]['mode'] == 'mixed'
    assert not airports.list_runways('OLD')[0]['available']
    db.close()


def test_incompatible_pr17_schema_is_rejected_without_changes():
    db = create_database(':memory:')
    with db.connection:
        db.connection.execute('CREATE TABLE airports (id INTEGER PRIMARY KEY, perimeter REAL)')
    with pytest.raises(ValueError,match='Incompatible'):
        AirportDatabase(db)
    assert [r['name'] for r in db.connection.execute('PRAGMA table_info(airports)')] == ['id','perimeter']
    db.close()


def test_batch_simulator_submits_excess_demand_to_real_tower_queue(system,monkeypatch):
    import airplane_demo
    db,service,store,_ = system
    # Request all three before completing the first; one runway means genuine queueing.
    monkeypatch.setattr(airplane_demo.time,'sleep',lambda _:None)
    assert airplane_demo.fabricate_landing_queue(store,db,['A','B','C'],landing_hold_seconds=0) == ['A','B','C']
    requests = service.snapshot()['requests']
    assert len(requests) == 3
    assert all(r['outcome'] == 'completed' for r in requests)
    assert any(m.metadata['outcome'] == 'queued' for m in db.receive_messages(Team.RADAR))
    assert all(store.get(a).status == 'Parked' for a in ['A','B','C'])
    assert service.airports.list_runways('JFK')[0]['available']


def test_configuring_tower_does_not_silently_adopt_legacy_runway_snapshots():
    db = create_database(':memory:')
    db.upsert_shared_state('runway:OLD',{'id':'OLD','available':False},category='runway')
    airports = AirportDatabase(db)
    with pytest.raises(ValueError,match='explicit migration'):
        airports.create_airport('JFK',1000,40,-73,['R'])
    assert airports.list_airports() == []
    assert not db.get_shared_state('runway:OLD')['payload']['available']
    db.close()


def test_demo_utc_timestamp_does_not_reinterpret_naive_utc_as_local():
    import airplane_demo
    # Far-future fixed value makes max(now, previous+1) deterministic in all zones.
    assert airplane_demo._next_timestamp('2099-07-01T12:00:00') == '2099-07-01T12:00:01'


def test_idle_runway_can_be_configured_without_changing_another_reservation(system):
    _,service,_,_ = system
    service.airports.add_runway('JFK','R2')
    request(service)
    service.airports.update_runway('JFK','R2',available=False,mode='departure')
    runways = {r['id']:r for r in service.airports.list_runways('JFK')}
    assert runways['R1']['request_id'] == 'r1'
    assert not runways['R1']['available']
    assert not runways['R2']['available']
    assert runways['R2']['mode'] == 'departure'


def test_simultaneous_duplicate_aircraft_requests_have_one_active_owner(system):
    _,_,_,path = system
    barrier = threading.Barrier(2)
    def submit(request_id):
        db = create_database(path)
        try:
            tower = TowerService(db)
            barrier.wait(timeout=5)
            return request(tower,request_id,'A')
        finally:
            db.close()
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit,['first','second']))
    assert sorted(r['outcome'] for r in results) == ['cleared','rejected']


def test_worker_module_entrypoint_consumes_the_persisted_contract(system):
    import os
    import subprocess
    import sys
    db,service,_,path = system
    incoming(db)
    subprocess.run([sys.executable,'-B','-m','src.tower.messaging','--database',path,'--once'],
                   check=True,capture_output=True,text=True,
                   env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    assert service.get_decision('r1')['outcome'] == 'cleared'
    assert len(db.receive_messages(Team.RADAR)) == 1
    assert db.connection.execute('SELECT COUNT(*) FROM tower_processed_messages').fetchone()[0] == 1
