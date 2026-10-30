"""Durable OFFLINE correlation oracle. No provider transport or billing release."""
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from uuid import uuid5, NAMESPACE_URL
from wire import dial_request, hangup_request, decode_state, parse_event, response_ids
from signatures import verify

SCHEMA = '''
CREATE TABLE IF NOT EXISTS attempts(
 id TEXT PRIMARY KEY, body TEXT NOT NULL, state TEXT NOT NULL,
 control TEXT UNIQUE, leg TEXT, session TEXT, stop INTEGER NOT NULL DEFAULT 0,
 hangup_command TEXT, hangup_at REAL, hangup_ack INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(
 id TEXT PRIMARY KEY, digest TEXT NOT NULL, data TEXT NOT NULL, occurred REAL NOT NULL,
 processed INTEGER NOT NULL DEFAULT 0, outcome TEXT);
'''


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)

class Store:
    def __init__(self, path, key, clock=time.time):
        self.path, self.key, self.clock = str(path), key, clock
        with self.tx() as conn:
            for statement in SCHEMA.split(';'):
                if statement.strip():
                    conn.execute(statement)

    @contextmanager
    def tx(self):
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def prepare(self, connection, caller, destination, attempt):
        request = dial_request(connection, caller, destination, attempt)
        body = canonical(request['json'])
        with self.tx() as conn:
            old = conn.execute('SELECT body FROM attempts WHERE id=?', (attempt,)).fetchone()
            if old and old['body'] != body:
                raise ValueError('Conflicting attempt identity')
            conn.execute('INSERT OR IGNORE INTO attempts(id,body,state) VALUES(?,?,?)',
                         (attempt, body, 'prepared'))
        return request

    def get(self, attempt):
        with self.tx() as conn:
            row = conn.execute('SELECT * FROM attempts WHERE id=?', (attempt,)).fetchone()
            if not row:
                raise ValueError('Unknown attempt')
            return dict(row)

    def claim_submission(self, attempt):
        with self.tx() as conn:
            row = conn.execute('SELECT * FROM attempts WHERE id=?', (attempt,)).fetchone()
            if not row or row['state'] != 'prepared' or row['stop']:
                raise ValueError('No redial: reconcile existing attempt')
            conn.execute('UPDATE attempts SET state=? WHERE id=?', ('submit_unknown', attempt))
            return {'method': 'POST', 'path': '/v2/calls', 'json': json.loads(row['body'])}

    @staticmethod
    def bind(conn, row, ids):
        if row['control'] and (row['control'], row['leg'], row['session']) != ids:
            raise ValueError('Conflicting provider identities')
        conn.execute('UPDATE attempts SET control=?,leg=?,session=? WHERE id=?', (*ids, row['id']))

    def response(self, attempt, payload):
        ids = response_ids(payload)
        with self.tx() as conn:
            row = conn.execute('SELECT * FROM attempts WHERE id=?', (attempt,)).fetchone()
            if not row or row['state'] in ('prepared', 'cancelled_before_submit'):
                raise ValueError('No submission claimed')
            self.bind(conn, row, ids)
            if row['state'] == 'submit_unknown':
                conn.execute('UPDATE attempts SET state=? WHERE id=?', ('initiated', attempt))

    def ingest(self, body, headers):
        verify(body, headers, self.key, now=self.clock())
        data, occurred = parse_event(body)
        encoded = canonical(data)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.tx() as conn:
            old = conn.execute('SELECT digest FROM events WHERE id=?', (data['id'],)).fetchone()
            if old:
                if old['digest'] != digest:
                    raise ValueError('Event ID collision')
                return {'accepted': True, 'duplicate': True}
            conn.execute('INSERT INTO events(id,digest,data,occurred) VALUES(?,?,?,?)',
                         (data['id'], digest, encoded, occurred))
        return {'accepted': True, 'duplicate': False}

    def event_status(self, event_id):
        with self.tx() as conn:
            row = conn.execute('SELECT processed,outcome FROM events WHERE id=?', (event_id,)).fetchone()
            return dict(row) if row else None

    def process(self, event_id):
        with self.tx() as conn:
            event = conn.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
            if not event:
                raise ValueError('Unknown event')
            if event['processed']:
                return event['outcome']
            data = json.loads(event['data'])
            outcome = self.apply(conn, data)
            conn.execute('UPDATE events SET processed=1,outcome=? WHERE id=?', (outcome, event_id))
            return outcome

    @staticmethod
    def apply(conn, data):
        ranks = {'call.initiated': 1, 'call.answered': 2, 'call.hangup': 3}
        if data['event_type'] not in ranks:
            return 'unsupported_event_stored'
        p = data['payload']
        try:
            ids = response_ids({'data': p})
            row = conn.execute('SELECT * FROM attempts WHERE control=?', (ids[0],)).fetchone()
            hint = decode_state(p['client_state']) if p.get('client_state') else None
            if not row and hint:
                row = conn.execute('SELECT * FROM attempts WHERE id=?', (hint,)).fetchone()
            if not row or row['state'] in ('prepared', 'cancelled_before_submit'):
                return 'quarantined_unknown_or_unclaimed'
            if hint and hint != row['id']:
                return 'quarantined_correlation_mismatch'
            expected = json.loads(row['body'])
            if any(p.get(k) != expected[k] for k in ('connection_id', 'from', 'to')):
                return 'quarantined_route_mismatch'
            if row['control'] and (row['control'], row['leg'], row['session']) != ids:
                return 'quarantined_leg_mismatch'
            Store.bind(conn, row, ids)
        except (ValueError, TypeError, KeyError, sqlite3.IntegrityError):
            return 'quarantined_invalid_correlation'
        states = {'submit_unknown': 0, 'initiated': 1, 'answered': 2, 'hangup': 3}
        rank = ranks[data['event_type']]
        if rank > states[row['state']]:
            conn.execute('UPDATE attempts SET state=? WHERE id=?',
                         (data['event_type'].split('.')[1], row['id']))
            return 'applied'
        return 'nonregressing_event_stored'

    def request_stop(self, attempt):
        with self.tx() as conn:
            row = conn.execute('SELECT state FROM attempts WHERE id=?', (attempt,)).fetchone()
            if not row:
                raise ValueError('Unknown attempt')
            state = 'cancelled_before_submit' if row['state'] == 'prepared' else row['state']
            conn.execute('UPDATE attempts SET stop=1,state=? WHERE id=?', (state, attempt))

    def hangup_command(self, attempt):
        with self.tx() as conn:
            row = conn.execute('SELECT * FROM attempts WHERE id=?', (attempt,)).fetchone()
            if not row or not row['stop'] or not row['control'] or row['state'] == 'hangup':
                return None
            command = row['hangup_command']
            if command:
                if self.clock() - row['hangup_at'] > 60:
                    raise ValueError('Retry window elapsed: reconcile, do not generate new command')
            else:
                command = str(uuid5(NAMESPACE_URL, attempt + ':hangup:' + row['control']))
                conn.execute('UPDATE attempts SET hangup_command=?,hangup_at=? WHERE id=?',
                             (command, self.clock(), attempt))
            return hangup_request(row['control'], command)

    def acknowledge_hangup(self, attempt, response):
        if response != {'data': {'result': 'ok'}}:
            raise ValueError('Unexpected command response')
        with self.tx() as conn:
            row = conn.execute('SELECT hangup_command FROM attempts WHERE id=?', (attempt,)).fetchone()
            if not row or not row['hangup_command']:
                raise ValueError('No command was prepared')
            conn.execute('UPDATE attempts SET hangup_ack=1 WHERE id=?', (attempt,))
