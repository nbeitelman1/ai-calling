import base64
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from media_auth import canonical_stream_url, verify_media_upgrade


class TestMediaUpgradeAuth(unittest.TestCase):
    def setUp(self):
        self.private=Ed25519PrivateKey.generate()
        self.public=base64.b64encode(self.private.public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
        self.now=1800000000
        self.host='calls.example.test:443'
        self.attempt='synthetic-attempt'

    def signed(self,query=b''):
        url=canonical_stream_url(self.host,self.attempt,query)
        message=f'{self.now}|{url}'.encode()
        return [('Telnyx-Timestamp',str(self.now)),('Telnyx-Signature-Ed25519',
                base64.b64encode(self.private.sign(message)).decode())]

    def test_valid_exact_url_with_query(self):
        self.assertTrue(verify_media_upgrade(self.signed(b'k=one%20two'),self.public,
            self.host,self.attempt,query=b'k=one%20two',now=self.now))

    def test_wrong_host_path_query_are_rejected(self):
        for host,attempt,query in [('evil.example',self.attempt,b''),
                                    (self.host,'other-attempt',b''),
                                    (self.host,self.attempt,b'changed=1')]:
            with self.subTest(host=host,attempt=attempt,query=query):
                with self.assertRaises(ValueError):
                    verify_media_upgrade(self.signed(),self.public,host,attempt,query=query,now=self.now)

    def test_missing_duplicate_stale_and_future_headers_rejected(self):
        cases=[([],self.now),(self.signed()+[('TELNYX-TIMESTAMP',str(self.now))],self.now),
               (self.signed(),self.now+301),(self.signed(),self.now-301)]
        for headers,now in cases:
            with self.subTest(now=now,headers=len(headers)),self.assertRaises(ValueError):
                verify_media_upgrade(headers,self.public,self.host,self.attempt,now=now)

    def test_host_must_be_configuration_not_arbitrary_url(self):
        for host in ['', 'wss://calls.test','calls.test/path','user:pass@calls.test',
                     'calls.test#fragment','calls.test?query=1','calls.test invalid']:
            with self.subTest(host=host), self.assertRaises(ValueError):
                canonical_stream_url(host,self.attempt)
