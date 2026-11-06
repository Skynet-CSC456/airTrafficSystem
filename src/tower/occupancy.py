"""Prototype Tower extension: stationary ownership, no second allocator/protocol.

Public additions: park_aircraft, occupancy, complete_request (retains landing
occupancy), process_requests_with_occupancy. Uses AirportDatabase's public closure
operation, Tower's existing selector, and inherited reservation/message writes.
The protected _promote/_save hooks need a supported upstream extension point before
integration into main. No direct writes to Tower reservation tables occur here.
"""
import json
from datetime import datetime, timezone
from src.db import Team
from src.tower.service import TowerService
from src.tower.airport import AirportDatabase
from src.shared.geometry import natural_key
from src.tower.clearance import find_safe_runway
from src.tower.messaging import process_requests


class _NaturalAirportDatabase(AirportDatabase):
    def list_runways(self, airport_id):
        return sorted(super().list_runways(airport_id),key=lambda runway:natural_key(runway['id']))


class OccupancyTowerService(TowerService):
    def __init__(self, db):
        super().__init__(db)
        self.airports = _NaturalAirportDatabase(db)
        self._transition = None
        self._departure = None

    def occupancy(self, *, aircraft_id=None, airport_id=None):
        rows = [r['payload'] for r in self._db.list_shared_state(category='tower_stationary')]
        return [r for r in rows if r['phase'] != 'released'
                and (aircraft_id is None or r['aircraft_id']==aircraft_id)
                and (airport_id is None or r['airport_id']==airport_id)]

    def _record(self, airport_id, runway_id, aircraft_id, phase, request_id=None):
        record = dict(airport_id=airport_id,runway_id=runway_id,aircraft_id=aircraft_id,
                      phase=phase,request_id=request_id)
        self._db.upsert_shared_state('stationary:'+json.dumps([airport_id,runway_id]),
                                     record,category='tower_stationary')
        return record

    def park_aircraft(self, aircraft_id, airport_id):
        """Claim one real compatible runway; caller atomically creates Radar row."""
        with self._db.transaction():
            previous = self.occupancy(aircraft_id=aircraft_id)
            if previous:
                return previous[0]
            aircraft = self._aircraft.get(aircraft_id)
            if aircraft is None or aircraft.status!='Parked' or aircraft.altitude!=0:
                raise ValueError('Stationary occupancy requires a grounded Parked aircraft')
            # Existing FIFO gets available capacity before a new stationary claim.
            self.promote_queue(airport_id)
            runway = find_safe_runway(self.airports.list_runways(airport_id),{'status':'Parked'})
            if runway is None:
                raise ValueError('Airport full: no compatible runway for a Parked aircraft')
            self.airports.update_runway_status(airport_id,runway['id'],False)
            return self._record(airport_id,runway['id'],aircraft_id,'parked')

    def _promote(self, airport_id):
        # Called inside the inherited Tower transaction. Closing the now-completed
        # landing runway precedes ordinary FIFO promotion, with no visible gap.
        if self._transition:
            d = self._transition
            if d.get('retain',d['operation']=='landing'):
                self.airports.update_runway_status(airport_id,d['runway_id'],False)
                self._record(airport_id,d['runway_id'],d['aircraft_id'],'parked',d['request_id'])
            else:
                self._record(airport_id,d['runway_id'],d['aircraft_id'],'released',d['request_id'])
        # Ownership transfer is not a claim on free capacity: a parked departure
        # may use its OWN blocked runway even if arrivals already wait in FIFO.
        if self._departure and self._departure['airport_id']==airport_id:
            decision = self._departure
            owner = next((r for r in self.occupancy(aircraft_id=decision['aircraft_id'],airport_id=airport_id)
                          if r['phase']=='parked'),None)
            if owner and self._validation_error(decision['aircraft_id'],airport_id,'departure',owner['runway_id']) is None:
                runway = owner['runway_id']
                self.airports.update_runway_status(airport_id,runway,True)
                self._save({**decision,'legacy':False},'cleared',runway_id=runway)
                self._record(airport_id,runway,decision['aircraft_id'],'departing',decision['request_id'])
        super()._promote(airport_id)

    def _validation_error(self, aircraft_id, airport_id, operation, runway_id):
        reason = super()._validation_error(aircraft_id,airport_id,operation,runway_id)
        owners = self.occupancy(aircraft_id=aircraft_id,airport_id=airport_id)
        if not reason and operation=='departure' and owners and runway_id not in (None,owners[0]['runway_id']):
            return 'Departure must use the aircraft-owned runway'
        return reason

    def request_runway(self, request_id, aircraft_id, airport_id, operation, *, runway_id=None):
        owner = self.occupancy(aircraft_id=aircraft_id,airport_id=airport_id)
        if operation=='departure' and owner:
            self._departure = dict(request_id=request_id,aircraft_id=aircraft_id,airport_id=airport_id,
                                   operation=operation)
        try:
            with self._db.transaction():
                result = super().request_runway(request_id,aircraft_id,airport_id,operation,runway_id=runway_id)
                if self._departure and result['outcome']=='queued':
                    # The unchanged processor may observe a newly committed input
                    # just after the preparation scan. Recover its queued decision
                    # on the next pass, after inherited replay/signature validation.
                    self._promote(airport_id)
                    result = self.get_decision(request_id)
                return result
        finally:
            self._departure = None

    def complete_request(self, request_id):
        decision = self.get_decision(request_id)
        if decision and decision['outcome']=='cleared':
            aircraft = self._aircraft.get(decision['aircraft_id'])
            if decision['operation']=='landing' and (aircraft.status!='Parked' or aircraft.altitude!=0):
                raise ValueError('Radar must record the landed Parked state before retaining occupancy')
            self._transition = {**decision,'retain':decision['operation']=='landing'}
        try:
            return super().complete_request(request_id)
        finally:
            self._transition = None

    def cancel_request(self, request_id, *, reason='Cancelled by operator'):
        decision = self.get_decision(request_id)
        if decision and decision['outcome']=='cleared' and decision['operation']=='departure' and self.occupancy(aircraft_id=decision['aircraft_id']):
            self._transition = {**decision,'retain':True}
        try:
            return super().cancel_request(request_id,reason=reason)
        finally:
            self._transition = None


def process_requests_with_occupancy(db, *, limit=100):
    """Extend owned departures, then run the unchanged real message processor.

    Same version-1 protocol, including independent Radar producers without API
    intent records. The existing processor still owns validation, acknowledgement
    and replay. Minimal envelope checks prevent extension side effects on malformed
    messages before that processor emits its standard rejection. Upstream should
    inject the service factory to remove this boundary-validation repetition.
    """
    if isinstance(limit,bool) or not isinstance(limit,int) or limit<1:
        return process_requests(db,limit=limit)  # delegate the original error
    tower = OccupancyTowerService(db)
    messages = db.receive_messages(Team.TOWER,sender=Team.RADAR,message_type='runway_request',limit=10000)
    processed = {row[0] for row in db.connection.execute('SELECT message_id FROM tower_processed_messages')}
    selected = {m.id for m in messages if m.id not in processed}
    selected = set(sorted(selected)[:limit])
    for message in messages:
        metadata = message.metadata
        if not isinstance(metadata,dict) or type(metadata.get('schema_version')) is not int or metadata['schema_version']!=1:
            continue
        if metadata.get('operation') not in ('landing','departure'): continue
        try:
            moment = datetime.fromisoformat(metadata['timestamp'].replace('Z','+00:00'))
            if moment.tzinfo is None or moment.utcoffset()!=timezone.utc.utcoffset(moment): continue
            if not all(isinstance(metadata.get(k),str) and metadata[k].strip() and metadata[k]==metadata[k].strip()
                       for k in ('request_id','aircraft_id','airport_id')): continue
            if metadata.get('runway_id') is not None and (not isinstance(metadata['runway_id'],str)
                    or not metadata['runway_id'].strip() or metadata['runway_id']!=metadata['runway_id'].strip()): continue
        except (ValueError,TypeError,KeyError,AttributeError):
            continue
        decision = tower.get_decision(metadata['request_id'])
        if message.id not in selected and not (decision and decision['outcome']=='queued' and metadata['operation']=='departure'): continue
        if decision and (decision['outcome']!='queued' or metadata['operation']!='departure'): continue
        try:
            tower.request_runway(metadata['request_id'],metadata['aircraft_id'],metadata['airport_id'],
                                 metadata['operation'],runway_id=metadata.get('runway_id'))
        except (ValueError,TypeError):
            # Immutable request-ID/signature collisions are rejected by the
            # original processor, without overwriting the existing operation.
            continue
    # A producer can append after preparation. Acknowledge only this bounded
    # snapshot, so the unchanged allocator never sees an unprepared new request.
    return process_requests(db,limit=len(selected)) if selected else []
