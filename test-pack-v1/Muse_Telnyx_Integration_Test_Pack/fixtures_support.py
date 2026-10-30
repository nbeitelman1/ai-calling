"""Public deterministic FIXTURE keys and synthetic events; never production."""
import base64
import json
from datetime import datetime, timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

NOW = 1793275200
PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUBLIC = base64.b64encode(PRIVATE.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
CONNECTION = '123456789012345678'
FROM = '+12025550149'
TO = '+12025550148'
ATTEMPT = '8c66c829-baf8-46d3-b3cb-05cb0768669e'
CONTROL = 'v3:fixture-control-id'
LEG = 'a288d1af-4997-4c37-a05a-a41b1c8112d4'
SESSION = 'd85f88c7-8a96-4894-a10e-754615fb72ca'


def state(attempt=ATTEMPT):
    return base64.b64encode(json.dumps({'attempt_id': attempt}).encode()).decode()


def event(kind='call.initiated', event_id='ev-1', at=NOW, delivery=1):
    return {'data': {'record_type': 'event', 'id': event_id, 'event_type': kind,
        'occurred_at': datetime.fromtimestamp(at, timezone.utc).isoformat(),
        'payload': {'call_control_id': CONTROL, 'call_leg_id': LEG,
                    'call_session_id': SESSION, 'connection_id': CONNECTION,
                    'from': FROM, 'to': TO, 'client_state': state()}},
        'meta': {'attempt': delivery}}


def raw(value):
    return json.dumps(value, separators=(',', ':')).encode()


def signed(body, stamp=NOW):
    stamp = str(stamp)
    signature = PRIVATE.sign(stamp.encode() + b'|' + body)
    return {'telnyx-timestamp': stamp,
            'telnyx-signature-ed25519': base64.b64encode(signature).decode()}


RAW = raw(event())
