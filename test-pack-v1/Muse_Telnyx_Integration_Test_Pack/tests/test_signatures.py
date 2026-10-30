import unittest
from signatures import verify, WebhookRejected
from fixtures_support import signed, PUBLIC, RAW, NOW, PRIVATE

class Signatures(unittest.TestCase):
    def test_valid_raw_body(self):
        verify(RAW, signed(RAW), PUBLIC, now=NOW)

    def test_mixed_case_headers(self):
        h = [(k.upper(), v) for k, v in signed(RAW).items()]
        verify(RAW, h, PUBLIC, now=NOW)

    def test_modified_body(self):
        with self.assertRaises(WebhookRejected):
            verify(RAW + b' ', signed(RAW), PUBLIC, now=NOW)

    def test_missing_signature(self):
        h = signed(RAW)
        h.pop('telnyx-signature-ed25519')
        with self.assertRaises(WebhookRejected):
            verify(RAW, h, PUBLIC, now=NOW)

    def test_old_timestamp(self):
        with self.assertRaises(WebhookRejected):
            verify(RAW, signed(RAW, NOW-301), PUBLIC, now=NOW)

    def test_future_timestamp(self):
        with self.assertRaises(WebhookRejected):
            verify(RAW, signed(RAW, NOW+301), PUBLIC, now=NOW)

    def test_boundary_accepted(self):
        verify(RAW, signed(RAW, NOW-300), PUBLIC, now=NOW)

    def test_duplicate_header_rejected(self):
        h = list(signed(RAW).items()) + [('TELNYX-TIMESTAMP', str(NOW))]
        with self.assertRaises(WebhookRejected):
            verify(RAW, h, PUBLIC, now=NOW)

    def test_missing_key(self):
        with self.assertRaises(WebhookRejected):
            verify(RAW, signed(RAW), '', now=NOW)

    def test_wrong_key(self):
        import base64
        with self.assertRaises(WebhookRejected):
            verify(RAW, signed(RAW), base64.b64encode(b'x'*32).decode(), now=NOW)

    def test_invalid_base64(self):
        h = signed(RAW)
        h['telnyx-signature-ed25519'] = '%%%%'
        with self.assertRaises(WebhookRejected):
            verify(RAW, h, PUBLIC, now=NOW)

    def test_short_signature(self):
        import base64
        h = signed(RAW)
        h['telnyx-signature-ed25519'] = base64.b64encode(b'x'*63).decode()
        with self.assertRaises(WebhookRejected):
            verify(RAW, h, PUBLIC, now=NOW)

    def test_timestamp_change(self):
        h = signed(RAW)
        h['telnyx-timestamp'] = str(NOW+1)
        with self.assertRaises(WebhookRejected):
            verify(RAW, h, PUBLIC, now=NOW)

    def test_body_limit(self):
        body = b'x' * 65537
        with self.assertRaises(WebhookRejected):
            verify(body, signed(body), PUBLIC, now=NOW)

    def test_text_body_refused(self):
        with self.assertRaises(WebhookRejected):
            verify(RAW.decode(), signed(RAW), PUBLIC, now=NOW)
