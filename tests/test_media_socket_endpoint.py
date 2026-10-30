import base64
import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from media_auth import canonical_stream_url


class TestMediaSocketEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.private=Ed25519PrivateKey.generate()
        cls.public=base64.b64encode(cls.private.public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
        cls.env=patch.dict(os.environ,{
            'TELNYX_WEBHOOK_PUBLIC_KEY':cls.public,
            'TELNYX_WEBHOOK_DB':str(Path(cls.tmp.name)/'webhook.db'),
            'AI_CALLING_LOG_DIR':str(Path(cls.tmp.name)/'logs'),
            'AI_CALLING_PUBLIC_HOST':'calls.example.test','TELNYX_API_KEY':'offline'})
        cls.env.start()
        sys.modules.pop('live_server',None)
        cls.server=importlib.import_module('live_server')
        cls.client=TestClient(cls.server.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.env.stop()
        cls.tmp.cleanup()

    def headers(self,attempt):
        stamp=str(int(time.time()))
        url=canonical_stream_url('calls.example.test',attempt)
        return {'telnyx-timestamp':stamp,'telnyx-signature-ed25519':
                base64.b64encode(self.private.sign((stamp+'|'+url).encode())).decode()}

    def register(self,attempt):
        from live_pipeline import LiveCallJob,LiveCredentialRefs
        job=LiveCallJob(to='+12025550101',business='Offline',objective='Offline')
        self.server.register_media_job(attempt,job,LiveCredentialRefs(),
            ('conn','+12025550100','+12025550101'),attempt+'.jsonl')

    def test_unsigned_upgrade_does_not_claim_job_or_build_pipeline(self):
        attempt='unsigned-test'
        self.register(attempt)
        with patch('live_pipeline.handle_media_websocket',new=AsyncMock()) as handler:
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect('/telnyx/media/'+attempt):
                    pass
            handler.assert_not_awaited()
        self.assertIsNotNone(self.server.peek_media_job(attempt))

    def test_valid_signature_reaches_handler(self):
        attempt='signed-test'; self.register(attempt)
        with patch('live_pipeline.handle_media_websocket',new=AsyncMock(return_value={})) as handler:
            with self.client.websocket_connect('/telnyx/media/'+attempt,headers=self.headers(attempt)):
                pass
            handler.assert_awaited_once()

    def test_prebuild_exception_gets_hangup_and_durable_record(self):
        attempt='failed-build-test'; self.register(attempt)
        with patch('live_pipeline.handle_media_websocket',new=AsyncMock(side_effect=TimeoutError())), \
             patch('live_pipeline._hangup_via_adapter',new=AsyncMock(return_value=False)) as hangup:
            with self.client.websocket_connect('/telnyx/media/'+attempt,headers=self.headers(attempt)) as ws:
                with self.assertRaises(WebSocketDisconnect):
                    ws.receive_text()
            hangup.assert_awaited_once()
        record=json.loads((self.server.LOG_DIR/(attempt+'.jsonl')).read_text())
        self.assertEqual(record['outcome'],'handler_error:TimeoutError')
        self.assertTrue(record['hangup_required'])

    def test_internal_registration_is_fail_closed(self):
        with patch.dict(os.environ,{'INTERNAL_TOKEN':''}):
            self.assertEqual(self.client.post('/internal/register-media-job',json={}).status_code,404)

    def test_duplicate_webhook_retries_stored_processing(self):
        # Envelope parsing and signature tests live in the canonical adapter tests.
        adapter=self.server.adapter
        with patch.object(adapter,'ingest_webhook',return_value={'accepted':True,'duplicate':True}), \
             patch('live_server.adapter_event_data',return_value='{"id":"stored-event"}'), \
             patch.object(adapter,'process_event',return_value='applied') as process:
            response=self.server._ingest(type('Request',(),{'headers':{}})(),b'{}')
        self.assertEqual(response['outcome'],'applied')
        process.assert_called_once_with('stored-event')
