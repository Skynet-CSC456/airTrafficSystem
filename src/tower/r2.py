"""Tower R2 service for the classroom simulation."""

import json
import math
from dataclasses import replace
from itertools import combinations
from time import time
from uuid import uuid4

from src.db import Team
from src.tower._request_protocol import send_r2_message
from src.tower._r2_policy import (
    DELAY_ADVANCES, RUNWAY_DELAY_SECONDS, advance_in_queue, current_airborne,
    corrective_target, separation, unsafe_separation,
)
from src.tower._r2_store import R2RequestStore
from src.tower.occupancy import OccupancyTowerService


class TowerR2Service(OccupancyTowerService):
    """Enable priority and timed completion on the existing allocator."""

    _request_store_class = R2RequestStore

    def __init__(self, db, *, clock=time, max_track_age_seconds=None):
        if max_track_age_seconds is not None and (
            not math.isfinite(max_track_age_seconds) or max_track_age_seconds <= 0
        ):
            raise ValueError('Maximum track age must be positive and finite')
        super().__init__(db)
        self._clock = clock
        self._max_track_age = max_track_age_seconds
        with db.transaction():
            self._requests.initialize()

    def _save(self, row, outcome, *, runway_id=None, reason=None):
        result = super()._save(row, outcome, runway_id=runway_id, reason=reason)
        if outcome == 'cleared':
            self._requests.set_release_deadline(row['request_id'], self._clock() + RUNWAY_DELAY_SECONDS)
        return result

    def release_eligible_at(self, request_id):
        """Unix timestamp of the earliest eligible completion for an R2 assignment."""
        return self._requests.release_deadline(request_id)

    def _matches_completion(self, decision):
        aircraft = self._aircraft.get(decision['aircraft_id'])
        if aircraft is None:
            return False
        if decision['operation'] == 'landing':
            return aircraft.status == 'Parked' and aircraft.altitude == 0
        return aircraft.status != 'Parked' and aircraft.altitude > 0

    def complete_request(self, request_id):
        """REQ-TWR-012: record confirmation and retain the reservation until eligible."""
        with self._db.transaction():
            decision = self.get_decision(request_id)
            if decision is None or decision['outcome'] != 'cleared':
                return super().complete_request(request_id)
            deadline = self.release_eligible_at(request_id)
            if deadline is None:
                # An assignment made before enabling R2 follows its original contract.
                return super().complete_request(request_id)
            if not self._matches_completion(decision):
                raise ValueError('Radar state must confirm the operation finished')
            self._requests.record_completion(request_id)
            if self._clock() < deadline:
                return decision
            return super().complete_request(request_id)

    def cancel_request(self, request_id, *, reason='Cancelled by operator'):
        with self._db.transaction():
            decision = self.get_decision(request_id)
            deadline = self.release_eligible_at(request_id)
            if decision and decision['outcome'] == 'cleared' and deadline is not None and self._clock() < deadline:
                raise ValueError('Cannot cancel an assigned runway before its release deadline')
            return super().cancel_request(request_id, reason=reason)

    def release_due(self):
        """Complete due, confirmed operations atomically with occupancy and promotion."""
        with self._db.transaction():
            completed = []
            for row in self._requests.releasable(self._clock()):
                request_id = row['request_id']
                if self._matches_completion(self.get_decision(request_id)):
                    completed.append(super().complete_request(request_id))
            return completed

    def _prioritize(self, decision, event):
        """REQ-TWR-010: apply one nonemergency priority increase to the real queue."""
        if event['reason'] not in DELAY_ADVANCES:
            raise ValueError('Unknown nonemergency conflict reason')
        if decision['outcome'] != 'queued' or decision['operation'] != 'landing':
            raise ValueError('Conflict priority requires a currently queued request')
        advance = DELAY_ADVANCES[event['reason']]
        previous = self._requests.advance_for(event['request_id'])
        response = {key: event[key] for key in ('event_id', 'request_id', 'aircraft_id', 'reason', 'timestamp')}
        change = advance - previous
        if change > 0:
            request_ids = [row['request_id'] for row in self._requests.queued_rows(decision['airport_id'])]
            order = advance_in_queue(request_ids, event['request_id'], change)
            send_r2_message(self._db, Team.COMMAND, 'conflict', 'Conflict', {**response, 'severity': 2})
            self._requests.save_order(order, event['request_id'], advance)
            self._promote(decision['airport_id'])
            send_r2_message(self._db, Team.COMMAND, 'conflict_resolved', 'Conflict Resolved',
                            {**response, 'severity': 2})
        send_r2_message(self._db, Team.RADAR, 'conflict_resolved', 'Conflict Resolved', response)

    def apply_event(self, event):
        """Apply a decoded event once, even across different SQLite message IDs."""
        with self._db.transaction():
            if not self._requests.record_event(event):
                return
            if event['type'] == 'unsafe_separation_resolved':
                self.evaluate_separation(conflict_id=event['conflict_id'])
                return
            decision = self.get_decision(event['request_id'])
            if decision is None or decision['aircraft_id'] != event['aircraft_id']:
                raise ValueError('R2 request does not belong to this aircraft')
            if event['type'] == 'conflict':
                self._prioritize(decision, event)
            elif decision['operation'] != event['operation']:
                raise ValueError('Completion operation does not match the assigned request')
            else:
                self.complete_request(event['request_id'])

    def evaluate_separation(self, *, conflict_id=None):
        """REQ-TWR-011: evaluate Radar tracks and publish state transitions."""
        with self._db.transaction():
            now = self._clock()
            aircraft = sorted((state for state in self._aircraft.list()
                               if current_airborne(state, now, self._max_track_age)),
                              key=lambda state: state.id)
            by_id = {state.id: state for state in aircraft}
            pending = {}
            for row in self._db.list_shared_state(category='tower_r2_separation'):
                previous = row['payload']
                order = previous.get('instruction')
                if previous['active'] and order and order['aircraft_id'] in by_id:
                    moving = by_id[order['aircraft_id']]
                    if moving.timestamp == order['observed_at']:
                        pending[moving.id] = replace(moving, latitude=order['latitude'],
                                                     longitude=order['longitude'])

            for left, right in combinations(aircraft, 2):
                key = 'tower_r2_separation:' + json.dumps([left.id, right.id])
                existing = self._db.get_shared_state(key)
                alert = existing['payload'] if existing else {}
                if conflict_id is not None and alert.get('conflict_id') != conflict_id:
                    continue
                unsafe = unsafe_separation(left, right)
                if unsafe == alert.get('active', False):
                    continue
                latitude, longitude = separation(left, right)
                payload = {
                    'conflict_id': str(uuid4()) if unsafe else alert['conflict_id'],
                    'aircraft_ids': [left.id, right.id], 'latitude_delta_degrees': latitude,
                    'longitude_delta_degrees': longitude,
                    'observation_timestamps': [left.timestamp, right.timestamp], 'severity': 1,
                    'active': unsafe,
                }
                message_type = 'unsafe_separation' if unsafe else 'unsafe_separation_resolved'
                content = 'Unsafe Separation' if unsafe else 'Unsafe Separation Resolved'
                if unsafe:
                    for moving in (right, left):
                        if moving.id in pending:
                            continue
                        target = corrective_target(moving, [*aircraft, *pending.values()])
                        if target is None:
                            continue
                        instruction = {**target, 'aircraft_id': moving.id,
                                       'observed_at': moving.timestamp,
                                       'conflict_id': payload['conflict_id']}
                        send_r2_message(self._db, Team.RADAR, 'change_position',
                                        'Unsafe Separation', instruction)
                        payload['instruction'] = instruction
                        pending[moving.id] = replace(moving, latitude=target['latitude'],
                                                     longitude=target['longitude'])
                        break
                send_r2_message(self._db, Team.COMMAND, message_type, content, payload)
                self._db.upsert_shared_state(key, payload, category='tower_r2_separation')
