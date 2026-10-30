"""Pure request/event shapes. No HTTP client, keys, or dialing capability."""
import base64
import json
import re
from datetime import datetime
from urllib.parse import quote
from uuid import UUID


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON member')
        value[key] = item
    return value


def load_json(body):
    return json.loads(body, object_pairs_hook=unique_object,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def identity(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError('Missing or oversized identifier')
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Control character in identifier')
    return value


def client_state(attempt):
    UUID(attempt)
    return base64.b64encode(json.dumps({'attempt_id': attempt}, separators=(',', ':')).encode()).decode()


def decode_state(value):
    if not isinstance(value, str) or len(value) > 1024:
        raise ValueError('Invalid client_state')
    obj = load_json(base64.b64decode(value, validate=True))
    if not isinstance(obj, dict) or set(obj) != {'attempt_id'}:
        raise ValueError('Correlation metadata only')
    UUID(obj['attempt_id'])
    return obj['attempt_id']


def dial_request(connection, caller, destination, attempt):
    identity(connection)
    for number in (caller, destination):
        if not isinstance(number, str) or not re.fullmatch(r'\+[1-9][0-9]{7,14}', number):
            raise ValueError('Single E.164 destination required')
    return {'method': 'POST', 'path': '/v2/calls', 'json': {
        'connection_id': connection, 'from': caller, 'to': destination,
        'client_state': client_state(attempt), 'command_id': attempt}}


def hangup_request(control, command):
    UUID(command)
    return {'method': 'POST',
            'path': '/v2/calls/' + quote(identity(control), safe='') + '/actions/hangup',
            'json': {'command_id': command}}


def parse_event(body):
    obj = load_json(body)
    data = obj['data']
    if data['record_type'] != 'event' or not isinstance(data['payload'], dict):
        raise ValueError('Voice v2 event required')
    for name in ('id', 'event_type', 'occurred_at'):
        identity(data[name])
    occurred = datetime.fromisoformat(data['occurred_at'].replace('Z', '+00:00'))
    if occurred.tzinfo is None:
        raise ValueError('Timezone required')
    return data, occurred.timestamp()


def response_ids(response):
    data = response['data']
    return tuple(identity(data[k]) for k in ('call_control_id', 'call_leg_id', 'call_session_id'))
