import base64
import unittest
from wire import dial_request, hangup_request, decode_state, parse_event
from fixtures_support import CONNECTION, FROM, TO, ATTEMPT, CONTROL, event, raw

class Wire(unittest.TestCase):
    def test_create_shape(self):
        r = dial_request(CONNECTION, FROM, TO, ATTEMPT)
        self.assertEqual((r['method'], r['path']), ('POST', '/v2/calls'))
        self.assertEqual(set(r['json']), {'connection_id','from','to','client_state','command_id'})
        self.assertEqual(decode_state(r['json']['client_state']), ATTEMPT)
        self.assertNotIn('Authorization', r)

    def test_array_destination_refused(self):
        with self.assertRaises(ValueError):
            dial_request(CONNECTION, FROM, [TO], ATTEMPT)

    def test_sip_outside_scope(self):
        with self.assertRaises(ValueError):
            dial_request(CONNECTION, FROM, 'sip:test@example.invalid', ATTEMPT)

    def test_hangup_shape_and_escaping(self):
        r = hangup_request('v3:opaque/id?x', ATTEMPT)
        self.assertEqual(r['path'], '/v2/calls/v3%3Aopaque%2Fid%3Fx/actions/hangup')
        self.assertEqual(r['json'], {'command_id': ATTEMPT})

    def test_metadata_rejects_extra_fields(self):
        value = base64.b64encode(b'{"attempt_id":"x","secret":"bad"}').decode()
        with self.assertRaises(ValueError):
            decode_state(value)

    def test_duplicate_json_keys(self):
        with self.assertRaises(ValueError):
            parse_event(b'{"data":{},"data":{}}')

    def test_naive_time_rejected(self):
        e = event(); e['data']['occurred_at'] = '2026-10-29T12:00:00'
        with self.assertRaises(ValueError):
            parse_event(raw(e))
