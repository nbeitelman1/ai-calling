import asyncio
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import live_pipeline as live
from model_select import redact_record
from runtime_safety import cancel_tasks_bounded


class TestFinalGuards(unittest.IsolatedAsyncioTestCase):
    async def test_cache_miss_budget_exhaustion_never_calls_provider(self):
        class Base:
            def __init__(self,**kwargs):
                self.push_frame=AsyncMock()
                self.calls=0
            async def run_tts(self,text,context_id):
                self.calls+=1
                yield None
        with tempfile.TemporaryDirectory() as tmp:
            service=live.CachedElevenLabsTTSService.create(Base,
                cache=live.FixedPhraseCache(tmp,namespace='offline'),fixed_phrases=set(),
                character_budget=live.TTSCharacterBudget(0))
            frames=[frame async for frame in service.run_tts('Do not bill this','ctx')]
        self.assertEqual(frames,[])
        self.assertEqual(service.calls,0)
        self.assertTrue(service.push_frame.await_args.args[0].fatal)

    async def test_cleanup_reports_cancellation_resistant_task_without_hanging(self):
        started=asyncio.Event()
        release=asyncio.Event()
        async def resistant():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
        task=asyncio.create_task(resistant())
        await started.wait()
        try:
            self.assertEqual(await cancel_tasks_bounded([task],timeout=.01),1)
        finally:
            release.set()
            await task

    def test_redaction_covers_nested_error_diagnostics(self):
        result=redact_record({'outcome':'error: number 123-45-6789',
                              'nested':{'note':'123-45-6789'}})
        self.assertNotIn('123-45-6789',str(result))

    def test_both_dialers_refuse_not_ready_without_dial(self):
        root=Path(__file__).resolve().parents[1]
        for filename in ['dial_measure_20261029.py','dial_qualification_20261029.py']:
            with self.subTest(filename=filename):
                spec=importlib.util.spec_from_file_location('offline_'+filename.replace('.py',''),root/'tools'/filename)
                module=importlib.util.module_from_spec(spec)
                with patch.dict(os.environ,{'AI_CALLING_DIAL_APPROVED':'YES_THIS_TEST_CALL',
                                            'AI_CALLING_PUBLIC_HOST':'calls.example.test'}):
                    spec.loader.exec_module(module)
                    with patch.object(module,'http_json',return_value=(503,{'ready':False})) as http, \
                         patch.object(module,'telnyx_post') as dial:
                        self.assertEqual(module.main(),2)
                    dial.assert_not_called()
                    self.assertEqual(http.call_count,1)

    def test_readonly_feedback_report_does_not_modify_database(self):
        import hashlib
        from tools.muse_readiness_report import inspect_state
        from telnyx_adapter import TelnyxAdapter
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.db'
            TelnyxAdapter(path,'unused')
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            report=inspect_state(path)
            self.assertTrue(report['available'])
            self.assertEqual(report['unresolved_attempts'],[])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)

    def test_preparation_renders_override_without_moving_venv(self):
        from tools.prepare_muse_candidate import render_service_override
        text=render_service_override('/home/hatch/ai-calling','/home/hatch/venvs/candidate')
        self.assertIn('ExecStart=\nExecStart=/home/hatch/venvs/candidate/bin/python',text)
        self.assertIn('ExecStartPre=\nExecStartPre=/home/hatch/venvs/candidate/bin/python',text)
        with self.assertRaises(ValueError):
            render_service_override('/home/with space','/home/hatch/venvs/candidate')
