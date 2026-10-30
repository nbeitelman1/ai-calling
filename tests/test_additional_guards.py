import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import live_pipeline as live
from telnyx_media import MediaAttachGate
from telnyx_adapter import TelnyxAdapter
from provider_config import configuration_fingerprint, load_preflight_receipt, tts_configuration
from provider_preflight import _gemini_model_catalog


class TestAdditionalGuards(unittest.TestCase):
    def test_media_attach_rejects_terminal_and_stop_intents(self):
        for state,stop in [('hangup',False),('answered',True),('unknown-state',False)]:
            row={'state':state,'stop':stop,'control_id':'control',
                 'payload_json':json.dumps({'connection_id':'conn','from':'from','to':'to'})}
            gate=MediaAttachGate(SimpleNamespace(get_attempt=lambda _:row))
            with self.subTest(state=state,stop=stop):
                self.assertFalse(gate.authorize(attempt_id='attempt',call_control_id='control',
                                                expected_route=('conn','from','to'))[0])

    def test_optout_is_terminal_and_negated_hangup_is_not(self):
        for phrase in ['Stop calling me.','Please hang up.','Do not call again.']:
            with self.subTest(phrase=phrase):
                ladder=live.BrainLadder(frontier_model='test')
                self.assertEqual(ladder.on_user_text(phrase)['action'],'end_call')
                self.assertEqual(ladder.terminal_reason,'callee_opt_out')
        ladder=live.BrainLadder(frontier_model='test')
        self.assertEqual(ladder.on_user_text("Please don't hang up.")['action'],'continue')

    def test_job_rejects_invalid_duration(self):
        for value in [0,-1,True,'10',float('nan'),61]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                live.LiveCallJob(to='+12025550101',business='Test',objective='Test',max_minutes=value)

    def test_quota_rejects_nonfinite_negative_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter=TelnyxAdapter(Path(tmp)/'state.db','unused')
            for i,value in enumerate([float('nan'),float('inf'),-1]):
                with self.subTest(value=value),self.assertRaises(ValueError):
                    adapter.register_media_job(f'attempt-{i}',{},time.time()+60,[('quota','month',value,10)])
            with self.assertRaises(ValueError):
                adapter.register_media_job('duplicates',{},time.time()+60,
                    [('quota','month',3,10),('quota','month',3,10)])

    def test_quota_records_real_overage_then_blocks_new_reservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter=TelnyxAdapter(Path(tmp)/'state.db','unused')
            adapter.register_media_job('first',{},time.time()+60,[('quota','month',3,10)])
            adapter.settle_media_job_quotas('first',{'quota':12})
            with adapter._tx() as conn:
                self.assertEqual(conn.execute('SELECT used FROM quota_ledger').fetchone()[0],12)
            with self.assertRaises(ValueError):
                adapter.register_media_job('second',{},time.time()+60,[('quota','month',1,10)])


class TestProviderConfiguration(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_follows_all_pages(self):
        pages=[{'models':[{'name':'models/first'}],'nextPageToken':'two'},
               {'models':[{'name':'models/second'}]}]
        with patch('provider_preflight._get_json',new=AsyncMock(side_effect=pages)) as get:
            result=await _gemini_model_catalog(object(),{})
        self.assertEqual(len(result['models']),2)
        self.assertIn('pageToken=two',get.await_args_list[1].args[1])

    async def test_catalog_repeated_token_is_failure(self):
        with patch('provider_preflight._get_json',new=AsyncMock(return_value={'nextPageToken':'same'})):
            with self.assertRaises(RuntimeError):
                await _gemini_model_catalog(object(),{})

    def test_resolved_fallback_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            env={'AI_CALLING_PREFLIGHT_RECEIPT':str(Path(tmp)/'preflight.json'),
                 'GEMINI_FAST_MODELS':'gone, working','GEMINI_FRONTIER_MODELS':'frontier',
                 'GROQ_STT_MODELS':'whisper','ELEVENLABS_TTS_MODELS':' tts ',
                 'ELEVENLABS_VOICE_ID':'voice','ELEVENLABS_SAMPLE_RATE':'16000'}
            with patch.dict(os.environ,env):
                now=time.time()
                receipt={'ready':True,'checked_at':now,
                         'models':{'fast':'working','frontier':'frontier','stt':'whisper'},
                         'configuration_fingerprint':configuration_fingerprint()}
                Path(env['AI_CALLING_PREFLIGHT_RECEIPT']).write_text(json.dumps(receipt))
                self.assertEqual(load_preflight_receipt(now=now)['models']['fast'],'working')
                self.assertEqual(tts_configuration()['model'],'tts')
                with self.assertRaises(ValueError):
                    load_preflight_receipt(now=now+3601)
                with self.assertRaises(ValueError):
                    load_preflight_receipt(now=now-1)
                with patch.dict(os.environ,{'ELEVENLABS_VOICE_ID':'changed'}),self.assertRaises(ValueError):
                    load_preflight_receipt(now=now)
