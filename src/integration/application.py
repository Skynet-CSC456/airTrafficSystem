"""Application coordination only: all domain decisions delegate to existing services."""
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from threading import RLock
import time
from src.integration.lifecycle import Lifecycle, validate_creation, runway_path

from src.db import create_database, Team
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.position_update import update_aircraft_position
from src.tower.occupancy import OccupancyTowerService as TowerService
from src.shared.transactions import atomic_radar
from src.integration.demo_reset import DEFAULT_RESET_ROOT, initialize_demo, generation, reset_demo, assert_safe_path
from src.command.dashboard import build_dashboard_snapshot
from src.shared.geometry import AIRPORT_ID, NAME, LATITUDE, LONGITUDE, GEOMETRY, classify, natural_key
from src.integration.consumers import initialize_receipts


class InputError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def identifier(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InputError(f'{label} must be nonempty without surrounding whitespace')
    return value


def next_timestamp(previous=None):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if previous:
        now = max(now, datetime.strptime(previous, '%Y-%m-%dT%H:%M:%S')+timedelta(seconds=1))
    return now.strftime('%Y-%m-%dT%H:%M:%S')


class Application:
    def __init__(self, database, *, reset_root=None):
        self.database = str(database)
        self.reset_root = reset_root if reset_root is not None else DEFAULT_RESET_ROOT
        self.runtime = None
        self.lock = RLock()
        with self.lock, closing(create_database(self.database)) as db:
            tower = TowerService(db)
            initialize_receipts(db)
            version = db.get_shared_state('demo:occupancy-version')
            if version is None and AircraftStore(db).list():
                raise ValueError('This session predates stationary runway occupancy. Start a fresh demo database; the old session is preserved.')
            db.upsert_shared_state('demo:occupancy-version',{'version':1},category='demo_settings')
            if tower.airports.get_airport(AIRPORT_ID) is None:
                tower.airports.create_airport(AIRPORT_ID, 22000, LATITUDE, LONGITUDE,
                                             [f'R{i}' for i in range(1, 11)])
            initialize_demo(db,self.database,self.reset_root)

    @contextmanager
    def mutation(self, expected_generation):
        """One process barrier for HTTP mutations and workers; reject old browsers."""
        with self.lock:
            with closing(create_database(self.database)) as db:
                current = generation(db)
            if ((expected_generation is None and current['resets']>0) or
                    (expected_generation is not None and expected_generation!=current['token'])):
                raise InputError('Demo was reset; refresh before performing this action',409)
            yield

    def reset(self):
        with self.lock:
            try:
                assert_safe_path(self.database,self.reset_root)
                with closing(create_database(self.database)) as db:
                    tower, store = TowerService(db), AircraftStore(db)
                    reset_demo(db,store,tower,self.database,self.reset_root)
            except PermissionError as exc:
                raise InputError(str(exc),403) from exc
            return self.snapshot()

    def create_aircraft(self, payload):
        identifier(payload.get('id'), 'Aircraft ID')
        expected = {'id','latitude','longitude','altitude','status'}
        if set(payload) != expected:
            raise InputError('Provide id, latitude, longitude, altitude and status')
        with self.lock, closing(create_database(self.database)) as db:
            store = AircraftStore(db)
            existing = store.get(payload['id'])
            if existing:
                if existing.status=='Landing' and all(getattr(existing,k)==v for k,v in payload.items()):
                    self.request_clearance({'aircraft_id':existing.id,'operation':'landing',
                                            'request_id':'request-'+existing.id})
                    return asdict(existing)
                raise InputError('Aircraft ID already exists', 409)
            state = AircraftState(**payload, timestamp=next_timestamp())
            validate_creation(state)
            tower = TowerService(db)
            with atomic_radar(db,store):
                store.create(state)
                if state.status == 'Parked':
                    occupied = tower.park_aircraft(state.id,AIRPORT_ID)
                    runway = runway_path(db,tower.airports.get_airport(AIRPORT_ID),occupied['runway_id'])
                    update_aircraft_position(store,{**asdict(state),**runway[-1],
                        'timestamp':next_timestamp(state.timestamp)})
                else:
                    self._send_request(db,store.get(state.id),tower,{'aircraft_id':state.id,
                        'operation':'landing','request_id':'request-'+state.id})
            return asdict(store.get(state.id))

    def update_aircraft(self, aircraft_id, payload):
        if not payload or set(payload)-{'latitude','longitude','altitude','status'}:
            raise InputError('Update latitude, longitude, altitude or status')
        with self.lock, closing(create_database(self.database)) as db:
            store = AircraftStore(db)
            state = store.get(aircraft_id)
            if state is None:
                raise InputError('Unknown aircraft', 404)
            flights = db.list_shared_state(category='flight')
            if any(r['payload']['aircraft_id']==aircraft_id and r['payload']['phase'] in ('running','failed') for r in flights):
                raise InputError('Finish the active simulation before editing flight data',409)
            merged = {**asdict(state), **payload, 'timestamp':next_timestamp(state.timestamp)}
            candidate = AircraftState(**merged)
            # Airborne Departing is an existing completed-flight record; direct
            # status transitions must use the lifecycle, not the edit control.
            if candidate.status != state.status:
                raise InputError('Use landing/departure operations to change flight status')
            if candidate.status != 'Departing': validate_creation(candidate)
            if candidate.status=='Parked' and any(getattr(candidate,k)!=getattr(state,k) for k in ('latitude','longitude')):
                raise InputError('Parked aircraft must remain on its occupied runway; use Depart')
            if not update_aircraft_position(store, merged):
                raise InputError('Aircraft update was stale', 409)
            return asdict(store.get(aircraft_id))

    def request_clearance(self, payload):
        if payload.get('operation') not in ('landing','departure'):
            raise InputError('Operation must be landing or departure')
        with self.lock, closing(create_database(self.database)) as db:
            store = AircraftStore(db)
            tower = TowerService(db)
            aircraft = store.get(payload.get('aircraft_id'))
            if aircraft is None:
                raise InputError('Unknown aircraft',404)
            return self._send_request(db,aircraft,tower,payload)

    def _send_request(self, db, aircraft, tower, payload):
        if set(payload)-{'request_id','aircraft_id','operation','runway_id'}:
            raise InputError('Unknown clearance request fields')
        aircraft_id = identifier(payload.get('aircraft_id'),'Aircraft ID')
        operation = payload.get('operation')
        if operation not in ('landing','departure'):
            raise InputError('Operation must be landing or departure')
        request_id = identifier(payload.get('request_id',str(uuid4())),'Request ID')
        runway_id = payload.get('runway_id')
        owners = tower.occupancy(aircraft_id=aircraft_id,airport_id=AIRPORT_ID)
        if operation=='departure' and owners:
            if runway_id is not None and runway_id!=owners[0]['runway_id']:
                raise InputError('Departure must use the occupied runway',409)
            runway_id = owners[0]['runway_id']
        metadata = {'schema_version':1,'request_id':request_id,'aircraft_id':aircraft_id,
                    'airport_id':AIRPORT_ID,'operation':operation,
                    'timestamp':datetime.now(timezone.utc).isoformat()}
        if runway_id is not None: metadata['runway_id'] = identifier(runway_id,'Runway ID')
        with db.transaction():
            for row in db.list_shared_state(category='runway_intent'):
                old = row['payload']
                same = old['request_id']==request_id
                active = tower.get_decision(old['request_id'])
                active = active is None or active['outcome'] in ('queued','cleared')
                if same or (active and old['aircraft_id']==aircraft_id and old['operation']==operation):
                    if same and (old['aircraft_id']!=aircraft_id or old['operation']!=operation or old.get('runway_id')!=runway_id):
                        raise InputError('Request ID belongs to a different request',409)
                    return {k:old[k] for k in ('request_id','message_id','delivery')}
            if operation=='departure' and classify(aircraft.latitude,aircraft.longitude)!='inside':
                raise InputError('Departure requires an aircraft inside the demo boundary')
            message_id = db.send_message(Team.RADAR,Team.TOWER,f'{aircraft_id} requests {operation}',
                                         message_type='runway_request',metadata=metadata)
            result = {**metadata,'message_id':message_id,'delivery':'sent'}
            db.upsert_shared_state('runway_intent:'+request_id,result,category='runway_intent')
            return {k:result[k] for k in ('request_id','message_id','delivery')}

    def add_runway(self, payload):
        if set(payload) != {'runway_id'}:
            raise InputError('Provide runway_id')
        runway_id = identifier(payload['runway_id'], 'Runway ID')
        with self.lock, closing(create_database(self.database)) as db:
            tower = TowerService(db)
            with db.transaction():
                tower.airports.add_runway(AIRPORT_ID, runway_id, mode='mixed')
                tower.promote_queue(AIRPORT_ID)
            return tower.airports.get_airport(AIRPORT_ID)

    def hold(self, payload):
        if set(payload) != {'enabled'} or type(payload['enabled']) is not bool:
            raise InputError('Provide enabled as a boolean')
        with self.lock, closing(create_database(self.database)) as db:
            with db.transaction():
                db.upsert_shared_state('demo:hold',payload,category='demo_settings')
                for row in db.list_shared_state(category='flight'):
                    plan = row['payload']
                    if plan['phase'] != 'running': continue
                    if payload['enabled'] and not plan.get('paused_at'):
                        plan['paused_at'] = time.time()
                    elif not payload['enabled'] and plan.get('paused_at'):
                        plan['started_at'] += time.time()-plan.pop('paused_at')
                    db.upsert_shared_state('flight:'+plan['request_id'],plan,category='flight')
        return payload

    def begin(self, request_id, *, automatic=False):
        with self.lock, closing(create_database(self.database)) as db:
            tower = TowerService(db)
            store = AircraftStore(db)
            with db.transaction():
                hold = db.get_shared_state('demo:hold')
                if automatic and hold and hold['payload']['enabled']:
                    raise InputError('Operations are held; use Begin explicitly',409)
                return Lifecycle(db,store,tower,next_timestamp).begin(request_id)

    def complete(self, request_id):
        with self.lock, closing(create_database(self.database)) as db:
            tower = TowerService(db)
            return Lifecycle(db,AircraftStore(db),tower,next_timestamp).finish(request_id)

    def snapshot(self):
        with self.lock, closing(create_database(self.database)) as db:
            tower = TowerService(db)
            # Existing Command builder is reused outside managed transactions;
            # labels/queues in the new frontend use operation-aware Tower data.
            command = build_dashboard_snapshot(db)
            store = AircraftStore(db)
            # One coherent view of actual flight records, Tower state and receipts.
            with db.transaction():
                domain = tower.snapshot()
                airport = next(a for a in domain['airports'] if a['airport_id'] == AIRPORT_ID)
                airport['runways'].sort(key=lambda r:natural_key(r['id']))
                occupancy = tower.occupancy(airport_id=AIRPORT_ID)
                owners = {r['runway_id']:r for r in occupancy}
                for runway in airport['runways']:
                    if runway['id'] in owners:
                        runway['stationary'] = owners[runway['id']]
                        runway['aircraft_id'] = owners[runway['id']]['aircraft_id']
                    runway['points'] = runway_path(db,airport,runway['id'])
                flights = {r['payload']['request_id']:r['payload'] for r in db.list_shared_state(category='flight')}
                hold = db.get_shared_state('demo:hold')
                decisions = {d['aircraft_id']:d for d in domain['requests']}
                # Keep an active clearance visible if a different request was rejected.
                for d in domain['requests']:
                    if d['outcome'] in ('cleared','queued'):
                        decisions[d['aircraft_id']] = d
                applied = {r['payload']['aircraft_id']:r['payload'] for r in db.list_shared_state(category='radar_clearance')}
                departures = {}
                for decision in domain['requests']:
                    if decision['operation']=='departure' and decision['outcome']=='completed':
                        departures[decision['aircraft_id']] = datetime.fromisoformat(decision['timestamp']).timestamp()
                aircraft = [{**a,'departure_completed_at':departures.get(a['id']),'classification':classify(a['latitude'],a['longitude']),
                             'occupancy':next((r for r in occupancy if r['aircraft_id']==a['id']),None),
                             'tower_decision':decisions.get(a['id']), 'flight':flights.get(decisions.get(a['id'],{}).get('request_id')),
                             'radar_clearance':applied.get(a['id'])} for a in (asdict(record) for record in store.list())]
                receipts = {(r['consumer'],r['message_id']):dict(r) for r in db.connection.execute('SELECT * FROM integration_receipts')}
                processed = {r[0] for r in db.connection.execute('SELECT message_id FROM tower_processed_messages')}
                messages = []
                for team in (Team.RADAR,Team.TOWER,Team.COMMAND):
                    for m in db.receive_messages(team, limit=10000):
                        receipt = receipts.get((team.value,m.id))
                        status = 'sent'
                        if m.id in processed: status = 'processed'
                        if receipt: status = 'failed' if receipt['error'] else ('applied' if receipt['applied_at'] else 'received')
                        messages.append({'id':m.id,'sender':m.sender.value,'recipient':m.recipient.value,
                            'message_type':m.message_type,'content':m.content,'metadata':m.metadata,
                            'sent_at':m.created_at.isoformat(),'status':status,'receipt':receipt})
                return {'generation':generation(db)['token'],'airport':{**airport,'name':NAME,'geometry':GEOMETRY},'aircraft':aircraft,
                        'requests':domain['requests'],'queue':tower.list_queue(AIRPORT_ID),
                        'events':sorted(messages,key=lambda m:m['id'])[-200:],
                        'hold_operations':hold['payload']['enabled'] if hold else False,
                        'system_status':command['system_status'],
                        'workers':self.runtime.health() if self.runtime else {}}
