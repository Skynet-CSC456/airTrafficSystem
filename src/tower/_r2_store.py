"""Optional R2 state using the same connection and transaction as Tower requests."""

import json

from src.tower._request_store import RequestStore


class R2RequestStore(RequestStore):
    def initialize(self):
        self._connection.execute("""CREATE TABLE IF NOT EXISTS tower_r2_queue (
            request_id TEXT PRIMARY KEY REFERENCES tower_requests(request_id) ON DELETE CASCADE,
            rank INTEGER NOT NULL, advance INTEGER NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS tower_r2_release (
            request_id TEXT PRIMARY KEY REFERENCES tower_requests(request_id) ON DELETE CASCADE,
            eligible_at REAL NOT NULL, completion_reported INTEGER NOT NULL DEFAULT 0)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS tower_r2_event (
            event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)""")

    def queued_rows(self, airport_id):
        return self._connection.execute("""SELECT r.* FROM tower_requests AS r
            LEFT JOIN tower_r2_queue AS q USING (request_id)
            WHERE r.airport_id=? AND r.outcome='queued'
            ORDER BY q.rank IS NULL, q.rank, r.sequence""", (airport_id,)).fetchall()

    def queue_position(self, airport_id, sequence):
        return next(index for index, row in enumerate(self.queued_rows(airport_id), 1)
                    if row['sequence'] == sequence)

    def queued_decisions(self, airport_id):
        return [json.loads(row['response']) for row in self.queued_rows(airport_id)]

    def advance_for(self, request_id):
        row = self._connection.execute(
            'SELECT advance FROM tower_r2_queue WHERE request_id=?', (request_id,)
        ).fetchone()
        return row['advance'] if row else 0

    def save_order(self, request_ids, changed_request_id, advance):
        self._connection.executemany("""INSERT INTO tower_r2_queue VALUES (?,?,?)
            ON CONFLICT(request_id) DO UPDATE SET rank=excluded.rank,
            advance=MAX(tower_r2_queue.advance,excluded.advance)""",
            [(request_id, rank, advance if request_id == changed_request_id else 0)
             for rank, request_id in enumerate(request_ids)])

    def set_release_deadline(self, request_id, eligible_at):
        self._connection.execute('INSERT OR IGNORE INTO tower_r2_release VALUES (?,?,0)',
                                 (request_id, eligible_at))

    def release_deadline(self, request_id):
        row = self._connection.execute('SELECT eligible_at FROM tower_r2_release WHERE request_id=?',
                                       (request_id,)).fetchone()
        return row['eligible_at'] if row else None

    def record_completion(self, request_id):
        self._connection.execute('UPDATE tower_r2_release SET completion_reported=1 WHERE request_id=?',
                                 (request_id,))

    def releasable(self, now):
        return self._connection.execute("""SELECT r.request_id FROM tower_r2_release AS t
            JOIN tower_requests AS r USING (request_id)
            WHERE t.completion_reported=1 AND t.eligible_at<=? AND r.outcome='cleared'
            ORDER BY t.eligible_at, r.sequence""", (now,)).fetchall()

    def record_event(self, event):
        # Recurring signals may have a new transport timestamp.
        payload = json.dumps({key: value for key, value in event.items() if key != 'timestamp'},
                             sort_keys=True)
        previous = self._connection.execute('SELECT payload FROM tower_r2_event WHERE event_id=?',
                                            (event['event_id'],)).fetchone()
        if previous:
            if previous['payload'] != payload:
                raise ValueError('R2 event ID already used for a different payload')
            return False
        self._connection.execute('INSERT INTO tower_r2_event VALUES (?,?)', (event['event_id'], payload))
        return True
