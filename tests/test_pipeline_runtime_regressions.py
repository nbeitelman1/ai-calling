"""Real Pipecat lifecycle tests; no provider connections or credentials required."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import live_pipeline as live
from pipecat.frames.frames import (
    EndFrame, InputAudioRawFrame, InterruptionFrame, LLMFullResponseStartFrame,
    LLMFullResponseEndFrame, TextFrame, TTSAudioRawFrame, TTSStartedFrame,
    TTSStoppedFrame, TTSTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner


class Sink(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.seen = []
        self.data_seen = asyncio.Event()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            self.seen.append(frame)
            if isinstance(frame, TextFrame):
                self.data_seen.set()
        await self.push_frame(frame, direction)


class TestRealProcessorLifecycle(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, subject, frame):
        sink = Sink()
        worker = PipelineWorker(Pipeline([subject, sink]), idle_timeout_secs=None,
                                enable_rtvi=False, enable_turn_tracking=False,
                                cancel_timeout_secs=1)
        started = asyncio.Event()

        @worker.event_handler('on_pipeline_started')
        async def on_started(worker, frame):
            started.set()

        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
        await runner.add_workers(worker)
        running = asyncio.create_task(runner.run())
        try:
            await asyncio.wait_for(started.wait(), 3)
            await worker.queue_frame(frame)
            if not isinstance(frame, TextFrame):
                await worker.queue_frame(TextFrame('queued-data-probe'))
            try:
                await asyncio.wait_for(sink.data_seen.wait(), .5)
            except TimeoutError:
                self.fail(f'{type(subject).__name__}: StartFrame propagated but queued data never reached the sink')
        finally:
            # A queued EndFrame is itself stuck in the defect under test.
            # Cancel through the runner's system-frame path instead.
            await runner.cancel('offline test cleanup')
            done, pending = await asyncio.wait({running}, timeout=3)
            if pending:
                running.cancel()
            for task in done:
                if not task.cancelled():
                    task.exception()

    async def test_inbound_tap_drains_real_queue(self):
        await self.exercise(live.InboundDebugTap('frames'), InputAudioRawFrame(bytes(1024),16000,1))

    async def test_rechunker_drains_real_queue(self):
        await self.exercise(live.InputAudioRechunker(), InputAudioRawFrame(bytes(1024),16000,1))

    async def test_transcript_tap_drains_real_queue(self):
        await self.exercise(live.TranscriptTap(live.BrainLadder(frontier_model='test')), TextFrame('probe'))

    async def test_response_gate_drains_real_queue(self):
        await self.exercise(live.ResponseGate(live.BrainLadder(frontier_model='test')), TextFrame('probe'))

    async def test_bot_tap_drains_real_queue(self):
        await self.exercise(live.BotActivityTap(live.CallActivity()), TextFrame('probe'))


class TestInterruptedResponse(unittest.IsolatedAsyncioTestCase):
    async def test_interruption_discards_stale_llm_text(self):
        ladder=live.BrainLadder(frontier_model='test')
        gate=live.ResponseGate(ladder)
        gate.push_frame=AsyncMock()
        # Direct logic test: no lifecycle scheduler; real-queue coverage is above.
        with patch.object(FrameProcessor, 'process_frame', new=AsyncMock()):
            for frame in [LLMFullResponseStartFrame(),TextFrame('stale response'),
                          InterruptionFrame(), LLMFullResponseEndFrame()]:
                await gate.process_frame(frame,FrameDirection.DOWNSTREAM)
        forwarded=[call.args[0] for call in gate.push_frame.call_args_list]
        self.assertFalse(any(isinstance(f,TextFrame) and 'stale' in f.text for f in forwarded))
        self.assertEqual(ladder.transcript,[])


class TestCachedContext(unittest.IsolatedAsyncioTestCase):
    async def test_cached_audio_context_includes_late_text_before_closure(self):
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
        with tempfile.TemporaryDirectory() as tmp:
            cache=live.FixedPhraseCache(tmp,namespace='offline')
            phrase='Offline cached phrase.'
            pcm=bytes(6400)
            cache.store(phrase,pcm)
            with live._clean_proxy_env():
                service=live.CachedElevenLabsTTSService.create(
                    ElevenLabsTTSService,cache=cache,fixed_phrases={phrase},
                    api_key='not-a-credential', sample_rate=16000,
                    settings=ElevenLabsTTSService.Settings(voice='offline',model='offline'))
            # Exact installed audio-context implementation, without starting a provider.
            service._serialization_queue=asyncio.Queue()
            service._audio_contexts={}
            context='offline-cache-context'
            await service.tts_process_generator(context,service.run_tts(phrase,context))
            self.assertTrue(service.audio_context_available(context), 'Cache hit dropped all audio because no context was created')
            from pipecat.utils.text.base_text_aggregator import AggregationType
            await service.append_to_audio_context(context,TTSTextFrame(phrase,AggregationType.SENTENCE))
            await service.remove_audio_context(context)
            queue=service._audio_contexts[context]
            items=[]
            while not queue.empty():
                items.append(queue.get_nowait())
            self.assertIsInstance(items[0],TTSStartedFrame)
            self.assertEqual(b''.join(f.audio for f in items if isinstance(f,TTSAudioRawFrame)),pcm)
            self.assertIsInstance(items[-2],TTSTextFrame)
            self.assertIsNone(items[-1])
            self.assertNotIn(None,items[:-1], 'Context closed before caller appended spoken text')
            self.assertTrue(any(isinstance(f,TTSStoppedFrame) for f in items))


if __name__=='__main__':
    unittest.main()
