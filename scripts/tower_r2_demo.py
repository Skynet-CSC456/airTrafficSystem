"""Exercise Tower R2 with a temporary database and simulated Radar events."""

from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from src.db import Team, create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.tower.r2 import TowerR2Service
from src.tower.r2_worker import run_cycle


def main():
    now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc).timestamp()

    def timestamp():
        return datetime.fromtimestamp(now, timezone.utc)

    with TemporaryDirectory(prefix='tower-r2-demo-') as directory, closing(
        create_database(str(Path(directory) / 'demo.db'))
    ) as db:
        tower = TowerR2Service(db, clock=lambda: now, max_track_age_seconds=120)
        tower.airports.create_airport('DEMO', 22000, 40, -73, ['R1'])
        radar = AircraftStore(db)

        def add(name, latitude, longitude, status='Landing', altitude=500):
            radar.create(AircraftState(name, latitude, longitude, status, altitude,
                                       timestamp().strftime('%Y-%m-%dT%H:%M:%S')))

        def radar_event(kind, event_id, **fields):
            db.send_message(Team.RADAR, Team.TOWER, kind, message_type=kind, metadata={
                'schema_version': 1, 'event_id': event_id, 'timestamp': timestamp().isoformat(), **fields,
            })
            run_cycle(db, tower)

        def show_queue(label):
            print(label, ' '.join(item['aircraft_id'] for item in tower.list_queue('DEMO')))

        add('P', 40, -73, 'Parked', 0)
        tower.park_aircraft('P', 'DEMO')
        for name, longitude in zip('ABCDE', (-120, -100, -80, -60, -40)):
            add(name, 40, longitude)
            tower.request_runway(name, name, 'DEMO', 'landing')
        show_queue('FIFO')
        radar_event('conflict', 'delay-D', request_id='D', aircraft_id='D',
                    reason='delay_2h', emergency=False)
        show_queue('After D is two hours late')
        radar_event('conflict', 'fuel-E', request_id='E', aircraft_id='E',
                    reason='low_fuel', emergency=False)
        show_queue('After E reports low fuel')

        add('X', 0, 179)
        add('Y', 1, -179)
        run_cycle(db, tower)
        alert, = [message for message in db.receive_messages(Team.COMMAND)
                  if message.message_type == 'unsafe_separation']
        print('Command unsafe-separation alert', alert.metadata['aircraft_ids'],
              'angular differences', alert.metadata['latitude_delta_degrees'],
              alert.metadata['longitude_delta_degrees'])
        now += 1
        radar.update(replace(radar.get('Y'), latitude=10, timestamp=timestamp().strftime('%Y-%m-%dT%H:%M:%S')))
        radar_event('unsafe_separation_resolved', 'separation-cleared', conflict_id=alert.metadata['conflict_id'])
        print('Command resolution correlated', any(
            message.message_type == 'unsafe_separation_resolved'
            and message.metadata['conflict_id'] == alert.metadata['conflict_id']
            for message in db.receive_messages(Team.COMMAND)
        ))

        departure = tower.request_runway('depart-P', 'P', 'DEMO', 'departure')
        assert departure['runway_id'] == 'R1'
        now += 29
        radar.update(replace(radar.get('P'), status='Departing', altitude=500, latitude=55,
                             timestamp=timestamp().strftime('%Y-%m-%dT%H:%M:%S')))
        radar_event('operation_finished', 'departed-P', request_id='depart-P',
                    aircraft_id='P', operation='departure')
        print('29 seconds', tower.occupancy(aircraft_id='P')[0]['phase'])
        assert tower.get_decision('depart-P')['outcome'] == 'cleared'
        now += 1
        completed, = run_cycle(db, tower)
        promoted = tower.get_decision('A')
        assert completed['outcome'] == 'completed' and promoted['runway_id'] == 'R1'
        print('30 seconds', completed['outcome'], 'A assigned', promoted['runway_id'])
        for team in (Team.RADAR, Team.COMMAND):
            print(team.value, [message.message_type for message in db.receive_messages(team, limit=1000)
                               if message.message_type in (
                                   'conflict', 'conflict_resolved', 'unsafe_separation',
                                   'unsafe_separation_resolved', 'runway_completed')])


if __name__ == '__main__':
    main()
