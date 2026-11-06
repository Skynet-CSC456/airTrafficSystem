"""Real integration consumers, with durable receipts independent of human reads.

Radar owns the applied clearance extension; AircraftStore remains unchanged and
continues to own flight data. Command acknowledges receipt without pretending to
apply Radar state. No clearance selection or queue logic lives here.
"""
from datetime import datetime, timezone

from src.db import Team

RESPONSE_TYPES = ('runway_response', 'runway_completed')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def initialize_receipts(db):
    with db.transaction():
        db.connection.execute('''CREATE TABLE IF NOT EXISTS integration_receipts (
            consumer TEXT NOT NULL, message_id INTEGER NOT NULL REFERENCES messages(id),
            received_at TEXT NOT NULL, applied_at TEXT, error TEXT,
            PRIMARY KEY(consumer,message_id))''')


def consume(db, team, *, limit=100):
    """Atomically apply/ack once per message, even if an inbox was read elsewhere."""
    if team not in (Team.RADAR, Team.COMMAND):
        raise ValueError('Only Radar and Command are integration consumers')
    count = 0
    with db.transaction():
        rows = db.connection.execute('''SELECT id FROM messages
            WHERE sender_team='tower' AND recipient_team=?
              AND message_type IN ('runway_response','runway_completed')
              AND NOT EXISTS (SELECT 1 FROM integration_receipts
                  WHERE consumer=? AND message_id=messages.id)
            ORDER BY id LIMIT ?''', (team.value, team.value, limit)).fetchall()
        for row in rows:
            message = db.receive_messages(team, message_id=row[0], limit=1)[0]
            received, applied, error = utc_now(), None, None
            try:
                payload = message.metadata
                if not isinstance(payload, dict) or type(payload.get('schema_version')) is not int or payload['schema_version'] != 1:
                    raise ValueError('Unsupported Tower response schema')
                for field in ('request_id','aircraft_id','airport_id','operation','outcome','timestamp'):
                    if not isinstance(payload.get(field), str) or not payload[field]:
                        raise ValueError(f'Missing response {field}')
                if payload['operation'] not in ('landing','departure'):
                    raise ValueError('Invalid response operation')
                if message.message_type == 'runway_completed':
                    if payload['outcome'] != 'completed':
                        raise ValueError('Invalid completion event')
                elif payload['outcome'] not in ('cleared','queued','rejected'):
                    raise ValueError('Invalid response outcome')
                if payload['outcome'] in ('cleared','completed') and not isinstance(payload.get('runway_id'), str):
                    raise ValueError('Missing assigned runway')
                if payload['outcome'] == 'queued' and (type(payload.get('queue_position')) is not int or payload['queue_position'] < 1):
                    raise ValueError('Invalid queue position')
                if team == Team.RADAR:
                    # This extension stores actual applied Radar assignment state;
                    # it never rewrites the production AircraftState model.
                    request = {**payload, 'source_message_id': message.id, 'applied_at': received}
                    db.upsert_shared_state('radar_request:'+payload['request_id'], request, category='radar_request')
                    key = 'radar_clearance:'+payload['aircraft_id']
                    previous = db.get_shared_state(key)
                    previous = previous['payload'] if previous else None
                    protects_active = (previous and previous['outcome'] in ('cleared','queued') and
                                       previous['request_id'] != payload['request_id'] and payload['outcome'] == 'rejected')
                    if not protects_active and (not previous or previous['source_message_id'] < message.id):
                        db.upsert_shared_state(key, request, category='radar_clearance')
                    applied = received
            except (ValueError, TypeError) as exc:
                error = str(exc)
            db.connection.execute('INSERT INTO integration_receipts VALUES (?,?,?,?,?)',
                                  (team.value, message.id, received, applied, error))
            db.connection.execute('UPDATE messages SET read_at=CURRENT_TIMESTAMP WHERE id=?', (message.id,))
            count += 1
    return count
