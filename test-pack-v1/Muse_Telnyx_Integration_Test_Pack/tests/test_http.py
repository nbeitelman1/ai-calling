import http.client
import tempfile
import unittest
from pathlib import Path
from store import Store
from http_lab import Lab
from fixtures_support import PUBLIC, NOW, CONNECTION, FROM, TO, ATTEMPT, event, raw, signed

class HTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name)/'lab.sqlite', PUBLIC, clock=lambda: NOW)
        self.s.prepare(CONNECTION, FROM, TO, ATTEMPT)

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_http_create_request_shape(self):
        request = self.s.claim_submission(ATTEMPT)
        with Lab(self.s) as lab:
            status, response = lab.request(request['path'], raw(request['json']),
                                           {'Authorization': 'Bearer FIXTURE_ONLY'})
            self.assertEqual(status, 200)
            self.s.response(ATTEMPT, response)
            self.assertEqual(lab.server.dial_count, 1)
        self.assertEqual(self.s.get(ATTEMPT)['state'], 'initiated')

    def test_real_http_loss_after_fake_carrier_acceptance(self):
        request = self.s.claim_submission(ATTEMPT)
        with Lab(self.s, drop_response=True) as lab:
            with self.assertRaises(http.client.RemoteDisconnected):
                lab.request(request['path'], raw(request['json']),
                            {'Authorization': 'Bearer FIXTURE_ONLY'})
            self.assertEqual(lab.server.dial_count, 1)
            with self.assertRaises(ValueError):
                self.s.claim_submission(ATTEMPT)
            body = raw(event())
            self.assertEqual(lab.request('/webhooks/telnyx', body, signed(body))[0], 200)
            self.assertEqual(self.s.process('ev-1'), 'applied')
            self.assertEqual(lab.server.dial_count, 1)

    def test_real_http_tampered_webhook_rejected(self):
        body = raw(event())
        with Lab(self.s) as lab:
            self.assertEqual(lab.request('/webhooks/telnyx', body+b' ', signed(body))[0], 400)
            self.assertIsNone(self.s.event_status('ev-1'))

    def test_real_http_duplicate_is_acknowledged(self):
        self.s.claim_submission(ATTEMPT)
        with Lab(self.s) as lab:
            for attempt in (1, 2):
                body = raw(event(delivery=attempt))
                status, receipt = lab.request('/webhooks/telnyx', body, signed(body))
                self.assertEqual(status, 200)
                self.assertEqual(receipt['duplicate'], attempt == 2)

    def test_real_http_hangup_ack_not_terminal(self):
        self.s.claim_submission(ATTEMPT)
        body = raw(event()); self.s.ingest(body, signed(body)); self.s.process('ev-1')
        self.s.request_stop(ATTEMPT)
        command = self.s.hangup_command(ATTEMPT)
        with Lab(self.s) as lab:
            status, response = lab.request(command['path'], raw(command['json']),
                                          {'Authorization': 'Bearer FIXTURE_ONLY'})
            self.assertEqual(status, 200)
            self.s.acknowledge_hangup(ATTEMPT, response)
            self.assertEqual(self.s.get(ATTEMPT)['state'], 'initiated')

    def test_fake_endpoint_requires_fixture_auth(self):
        request = self.s.claim_submission(ATTEMPT)
        with Lab(self.s) as lab:
            self.assertEqual(lab.request(request['path'], raw(request['json']))[0], 401)
            self.assertEqual(lab.server.dial_count, 0)
