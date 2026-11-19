"""Opt-in backend worker for Tower R2 on a classroom simulation database."""

import argparse
from contextlib import closing
from time import sleep

from src.db import Team, create_database
from src.tower._request_protocol import R2_INPUT_TYPES, parse_r2_event, send_r2_message
from src.tower.airport import current_timestamp
from src.tower.messaging import assign_runway_to_aircraft
from src.tower.occupancy import process_requests_with_occupancy
from src.tower.r2 import TowerR2Service


def _process_r2_messages(db, tower, limit):
    for _ in range(limit):
        with db.transaction():
            row = db.connection.execute("""SELECT id FROM messages AS m
                WHERE sender_team='radar' AND recipient_team='tower'
                AND message_type IN (?,?,?) AND NOT EXISTS (
                    SELECT 1 FROM tower_processed_messages WHERE message_id=m.id)
                ORDER BY id LIMIT 1""", R2_INPUT_TYPES).fetchone()
            if row is None:
                break
            message = db.receive_messages(Team.TOWER, message_id=row['id'])[0]
            try:
                tower.apply_event(parse_r2_event(message))
            except (ValueError, KeyError) as exc:
                send_r2_message(db, Team.RADAR, 'tower_r2_rejected', 'Rejected Tower R2 event',
                                {'source_message_id': message.id, 'reason': str(exc)})
            db.connection.execute('INSERT INTO tower_processed_messages VALUES (?,?)',
                                  (message.id, current_timestamp()))
            db.connection.execute('UPDATE messages SET read_at=CURRENT_TIMESTAMP WHERE id=?', (message.id,))


def run_cycle(db, tower, *, limit=100):
    """Use the same R2 service for ordinary requests, messages and timed releases."""
    process_requests_with_occupancy(db, limit=limit, service_factory=lambda _: tower)
    _process_r2_messages(db, tower, limit)
    completed = tower.release_due()
    tower.evaluate_separation()
    return completed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--max-track-age-seconds', type=float)
    parser.add_argument('--assign', nargs=2, metavar=('AIRCRAFT', 'RUNWAY'))
    args = parser.parse_args()
    with closing(create_database(args.database)) as db:
        tower = TowerR2Service(db, max_track_age_seconds=args.max_track_age_seconds)
        if args.assign:
            print(assign_runway_to_aircraft(db, *args.assign, service_factory=lambda _: tower))
            return
        try:
            while True:
                run_cycle(db, tower)
                if args.once:
                    return
                sleep(0.5)
        except KeyboardInterrupt:
            return


if __name__ == '__main__':
    main()
