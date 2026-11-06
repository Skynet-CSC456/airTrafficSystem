"""Demo flight coordination; never allocates runways or changes domain models.

Radar's unchanged store joins the shared transaction through atomic_radar. Final
Radar state, Tower completion/occupancy transfer, events and flight phase commit
together. Failures preserve the reservation and can be retried.
"""
from dataclasses import asdict
import time
from src.shared.geometry import GEOMETRY, classify, natural_key
from src.shared.transactions import atomic_radar
from src.radar.position_update import update_aircraft_position


def runway_path(db, airport, runway_id):
    # Durable slots: adding R11 or an arbitrary ID never moves an active runway.
    key = 'demo:runway-layout:'+airport['airport_id']
    with db.transaction():
        saved = db.get_shared_state(key)
        slots = saved['payload'] if saved else {}
        original = len(slots)
        for name in sorted((r['id'] for r in airport['runways']),key=natural_key):
            if name not in slots: slots[name] = len(slots)
        if len(slots) != original:
            db.upsert_shared_state(key,slots,category='demo_geometry')
    slot = slots[runway_id]
    # Reserve room for R11+ so adding runways does not shift active coordinates.
    if slot < 20:
        lon = GEOMETRY['west']+.0005+(slot+.5)*.019/20
    else:
        # Additional identifiers retain distinct in-bound coordinates; the
        # classroom layout is optimized for the first 10/11 runways.
        value, fraction, weight = slot-19, 0., .5
        while value:
            fraction += (value % 2)*weight
            value //= 2
            weight /= 2
        lon = GEOMETRY['west']+.0191+fraction*.0008
    height = .013
    north = GEOMETRY['north']-.003
    return [{'latitude':north, 'longitude':lon},
            {'latitude':north-height, 'longitude':lon}]


def validate_creation(state):
    if state.status == 'Departing':
        raise ValueError('Create Parked at altitude 0 inside the airport, then click Depart')
    if state.status == 'Parked' and (state.altitude != 0 or classify(state.latitude,state.longitude) != 'inside'):
        raise ValueError('Parked requires altitude 0 inside the demo airport boundary')
    if state.status == 'Landing' and (state.altitude <= 0 or classify(state.latitude,state.longitude) != 'outside'):
        raise ValueError('Landing requires positive altitude outside the demo airport boundary')


class Lifecycle:
    duration = 8.0

    def __init__(self, db, store, tower, timestamp):
        self.db, self.store, self.tower, self.timestamp = db, store, tower, timestamp

    def begin(self, request_id):
        with self.db.transaction():
            previous = self.db.get_shared_state('flight:'+request_id)
            if previous:
                plan = previous['payload']
                if plan.get('paused_at'):
                    plan['started_at'] += time.time()-plan.pop('paused_at')
                    self.db.upsert_shared_state('flight:'+request_id,plan,category='flight')
                return plan
            decision = self.tower.get_decision(request_id)
            if decision is None:
                raise KeyError(request_id)
            applied = self.db.get_shared_state('radar_request:'+request_id)
            if decision['outcome'] != 'cleared' or not applied or applied['payload']['outcome'] != 'cleared':
                raise ValueError('Wait for a cleared Tower decision and confirmed Radar application')
            aircraft = self.store.get(decision['aircraft_id'])
            airport = self.tower.airports.get_airport(decision['airport_id'])
            runway = runway_path(self.db,airport,decision['runway_id'])
            if decision['operation']=='landing':
                target = runway[-1]
                points = [{'latitude':aircraft.latitude,'longitude':aircraft.longitude},
                    {'latitude':GEOMETRY['north']+.004,'longitude':runway[0]['longitude']},*runway]
            else:
                target = {'latitude':GEOMETRY['north']+.008,'longitude':runway[0]['longitude']}
                points = [{'latitude':aircraft.latitude,'longitude':aircraft.longitude},runway[0],target]
            plan = {'request_id':request_id,'aircraft_id':aircraft.id,
                    'operation':decision['operation'],'runway_id':decision['runway_id'],
                    'started_at':time.time(),'duration':self.duration,'phase':'running','error':None,
                    'points':points,'segment_weights':[.2,.2,.6] if decision['operation']=='landing' else [.75,.25],
                    'target':{**target,'altitude':0 if decision['operation']=='landing' else 500,
                              'status':'Parked' if decision['operation']=='landing' else 'Departing'}}
            self.db.upsert_shared_state('flight:'+request_id,plan,category='flight')
            return plan

    def finish(self, request_id):
        row = self.db.get_shared_state('flight:'+request_id)
        if not row:
            if self.tower.get_decision(request_id) is None: raise KeyError(request_id)
            raise ValueError('Begin the operation and finish its visualization before completing')
        plan = row['payload']
        if plan['phase']=='completed':
            return self.tower.get_decision(request_id)
        if plan.get('paused_at'):
            raise ValueError('Operation is paused; click Begin to resume')
        if time.time() < plan['started_at']+plan['duration']:
            raise ValueError('Operation visualization is still in progress')
        decision = self.tower.get_decision(request_id)
        if decision['outcome'] not in ('cleared','completed'):
            raise ValueError('Operation no longer owns a clearance')
        try:
            with atomic_radar(self.db,self.store):
                current = self.store.get(plan['aircraft_id'])
                target = plan['target']
                if any(getattr(current,k) != v for k,v in target.items()):
                    if not update_aircraft_position(self.store,{**asdict(current),**target,
                            'timestamp':self.timestamp(current.timestamp)}):
                        raise ValueError('Radar rejected the position update')
                result = self.tower.complete_request(request_id)
                plan.update(phase='completed',error=None,completed_at=time.time())
                self.db.upsert_shared_state('flight:'+request_id,plan,category='flight')
                return result
        except Exception as exc:
            plan.update(phase='failed',error=str(exc))
            self.db.upsert_shared_state('flight:'+request_id,plan,category='flight')
            raise
