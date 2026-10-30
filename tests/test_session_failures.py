import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import live_pipeline as live
from test_offline_media_session import Adapter


class TestSessionFailurePaths(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,mode):
        callbacks={}
        entered=asyncio.Event()
        class Worker:
            def event_handler(self,name):
                def register(fn):
                    callbacks[name]=fn
                    return fn
                return register
        class Runner:
            def __init__(self,**kwargs):
                pass
            async def add_workers(self,task):
                pass
            async def run(self):
                entered.set()
                if mode=='runner_error':
                    raise RuntimeError('synthetic failure')
                if mode!='start_timeout':
                    await callbacks['on_pipeline_started'](None,None)
                await asyncio.Event().wait()
            async def cancel(self,reason):
                pass
        with tempfile.TemporaryDirectory() as tmp:
            serializer=SimpleNamespace(output_audio_ready=asyncio.Event(),_input_payload_bytes=0,
                _output_payload_bytes=0,_output_media_count=0,_sequence_gaps=0,_duplicate_or_old=0)
            services={'tts':SimpleNamespace(queue_frame=AsyncMock()),
                      'activity':live.CallActivity(),'silence_policy':live.SilencePolicy(),
                      'character_budget':live.TTSCharacterBudget(100),
                      'phrase_cache':live.FixedPhraseCache(tmp,namespace='offline'),
                      'serializer':serializer,'call_control_id':'control-offline','stream_id':'stream-offline'}
            ladder=live.BrainLadder(frontier_model='offline')
            socket=SimpleNamespace(close=AsyncMock(),client_state=SimpleNamespace(name='CONNECTED'))
            path=Path(tmp)/'failure.jsonl'
            with patch.dict(os.environ,{'TELNYX_API_KEY':'offline',
                       'AI_CALLING_START_TIMEOUT_SECONDS':'.03','AI_CALLING_FIRST_AUDIO_TIMEOUT_SECONDS':'.03'}), \
                 patch('pipecat.runner.utils.parse_telephony_websocket',new=AsyncMock(return_value=(
                     'telnyx',{'call_id':'control-offline','stream_id':'stream-offline'}))), \
                 patch('pipecat.workers.runner.WorkerRunner',Runner), \
                 patch.object(live,'build_live_pipeline',return_value=(Worker(),ladder,services)), \
                 patch.object(live,'_hangup_via_adapter',new=AsyncMock(return_value=True)) as hangup:
                running=asyncio.create_task(live.handle_media_websocket(socket,'offline',
                    live.LiveCallJob(to='+12025550101',business='Offline',objective='Offline'),
                    live.LiveCredentialRefs(),Adapter(),('conn','+12025550100','+12025550101'),
                    str(path),models={'fast':'offline','frontier':'offline','stt':'offline'}))
                if mode=='cancelled':
                    await entered.wait()
                    running.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await running
                else:
                    await asyncio.wait_for(running,2)
                self.assertTrue(path.exists())
                record=json.loads(path.read_text())
                self.assertEqual(record['cleanup_pending_tasks'],0)
                self.assertTrue(record['hangup_acknowledged'])
                hangup.assert_awaited()
                if mode=='start_timeout':
                    self.assertFalse(record['pipeline_started'])
                    services['tts'].queue_frame.assert_not_awaited()
                if mode=='no_audio':
                    self.assertTrue(record['pipeline_started'])
                    self.assertEqual(record['outbound_payload_bytes'],0)
                if mode=='cancelled':
                    self.assertEqual(record['outcome'],'cancelled')
                else:
                    self.assertTrue(record['outcome'].startswith('error:'))

    async def test_start_timeout_fails_closed_and_records(self):
        await self.exercise('start_timeout')

    async def test_silent_pipeline_ends_at_first_audio_deadline(self):
        await self.exercise('no_audio')

    async def test_runner_failure_records_and_hangs_up(self):
        await self.exercise('runner_error')

    async def test_outer_cancellation_records_then_propagates(self):
        await self.exercise('cancelled')
