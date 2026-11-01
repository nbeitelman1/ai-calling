"""Small deterministic runtime-control units; real media coverage is separate."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import call_controller
from closing_runtime import (ClosingRuntime, ClosingContextGate, ClosingOutputGate, closing_enabled)
from closing_state import ClosingPhase
from conversation_state import ConversationTracker
import live_pipeline as live
from pipecat.frames.frames import InterruptionFrame, LLMContextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame, TextFrame, TranscriptionFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection


def owner():
    brief = call_controller.CallBrief(persona_core='', hand_back_rules='', playbook=None,
                                     profile_text='', end_conditions=())
    ladder = live.BrainLadder(frontier_model='offline', tracker=ConversationTracker())
    state = ClosingRuntime(brief, ladder, live.CallActivity(),
                           SimpleNamespace(load=lambda _: None), LLMContext([]), 12)
    ladder.closing_runtime = state
    return state


class TestClosingRuntime(unittest.IsolatedAsyncioTestCase):
    def test_explicit_opt_in_and_invalid_mode(self):
        self.assertFalse(closing_enabled({}))
        self.assertTrue(closing_enabled({'AI_CALLING_CLOSING_STACK': 'controller'}))
        with self.assertRaises(ValueError):
            closing_enabled({'AI_CALLING_CLOSING_STACK': 'typo'})

    def test_existing_terminal_reason_vocabulary_is_preserved(self):
        for reason in ('test_budget_exceeded', 'soft_cap', 'monthly_cap',
                       'phone_tree_or_voicemail', 'tts_limit'):
            state = owner()
            state.request(reason)
            self.assertEqual(state.state.reason, reason)
            self.assertEqual(state.outcome, reason)

    async def test_clear_barrier_waits_for_transport_receipt(self):
        state = owner()
        completed = asyncio.Event()
        def acknowledge(frame):
            completed.set()
            frame.closing_ack.set()
        async def enqueue(frame):
            asyncio.get_running_loop().call_later(.04, acknowledge, frame)
        state.llm = SimpleNamespace(queue_frame=enqueue)
        await state.clear_output()
        self.assertTrue(completed.is_set(), 'clear returned before transport receipt')

    async def test_concurrent_hangup_claims_dispatch_once(self):
        state = owner()
        callback = AsyncMock(return_value=True)
        results = await asyncio.gather(*(state.hangup_once(callback, 'caller_goodbye') for _ in range(3)))
        self.assertEqual(results, [True, True, True])
        callback.assert_awaited_once()
        self.assertEqual(state.hangup_dispatches, 1)

    async def test_hangup_exception_cannot_reissue_effect(self):
        state = owner()
        callback = AsyncMock(side_effect=RuntimeError('offline failure'))
        with self.assertRaises(RuntimeError):
            await state.hangup_once(callback, 'caller_goodbye')
        self.assertFalse(await state.hangup_once(callback, 'error'))
        callback.assert_awaited_once()

    async def test_context_queued_before_close_is_blocked_before_llm(self):
        state = owner()
        gate = ClosingContextGate(state)
        gate.push_frame = AsyncMock()
        state.request('caller_goodbye')
        with patch.object(FrameProcessor, 'process_frame', new=AsyncMock()):
            await gate.process_frame(LLMContextFrame(LLMContext([])), FrameDirection.DOWNSTREAM)
        gate.push_frame.assert_not_awaited()
        self.assertEqual(state.suppressed_contexts, 1)

    async def test_ack_interruption_does_not_clear_output_processor_queue(self):
        state = owner()
        gate = ClosingOutputGate(state)
        gate.push_frame = AsyncMock()
        state.request('caller_goodbye')
        with patch.object(FrameProcessor, 'process_frame', new=AsyncMock()) as parent:
            await gate.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
            parent.assert_not_awaited()
        gate.push_frame.assert_not_awaited()

    async def test_old_llm_response_is_not_released_after_reopening(self):
        state = owner()
        gate = live.ResponseGate(state.ladder)
        gate.push_frame = AsyncMock()
        with patch.object(FrameProcessor, 'process_frame', new=AsyncMock()):
            await gate.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
            await gate.process_frame(TextFrame('Stale answer.'), FrameDirection.DOWNSTREAM)
            state.request('caller_goodbye')
            state.router.after_user_turn('Wait, what time?')
            state.epoch += 1
            await gate.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        self.assertFalse(any(getattr(call.args[0], 'text', '') == 'Stale answer.'
                             for call in gate.push_frame.call_args_list))
        self.assertEqual(state.ladder.transcript, [])

    async def test_model_goodbye_is_a_cached_close_request_not_spoken_text(self):
        state = owner()
        gate = live.ResponseGate(state.ladder)
        gate.push_frame = AsyncMock()
        with patch.object(FrameProcessor, 'process_frame', new=AsyncMock()):
            for frame in (LLMFullResponseStartFrame(), TextFrame('Goodbye.'), LLMFullResponseEndFrame()):
                await gate.process_frame(frame, FrameDirection.DOWNSTREAM)
        self.assertEqual(state.state.reason, 'assistant_goodbye')
        self.assertEqual(state.ladder.transcript, [])
        self.assertFalse(any(isinstance(call.args[0], TextFrame) for call in gate.push_frame.call_args_list))

    def test_short_stop_is_not_successful_cached_playback(self):
        state = owner()
        state.request('caller_goodbye')
        effect = state.state.take_line()
        item = {'token': effect.token, 'terminal': True, 'text': effect.text,
                'audio_seconds': 2.0, 'interrupted': False}
        state._active_playback = item
        with patch('closing_runtime.time.monotonic', side_effect=[100.0, 100.01]):
            state.playback_event(True)
            state.playback_event(False)
        self.assertTrue(item['interrupted'])
        self.assertFalse(state.state.summary()['playback_complete'])
        self.assertFalse(state.state.claim_hangup(effect.token))

    async def test_router_observes_each_user_once_and_suppresses_duplicates(self):
        state = owner()
        frame = TranscriptionFrame('Goodbye.', 'offline', '1', finalized=True)
        with patch.object(state.ladder.tracker, 'observe_user', wraps=state.ladder.tracker.observe_user) as spy:
            self.assertFalse(await state.user_text(frame))
            self.assertFalse(await state.user_text(frame))
            spy.assert_called_once_with('Goodbye.')
        self.assertEqual(len(state.ladder.transcript), 1)

    async def test_empty_turn_rearms_same_native_idle_controller(self):
        state = owner()
        native = SimpleNamespace(_start_idle_timer=AsyncMock())
        agg = SimpleNamespace(_user_idle_controller=native)
        await state.on_user_turn_stopped(agg, None, SimpleNamespace(content=''))
        native._start_idle_timer.assert_awaited_once()
        self.assertEqual(state.empty_rearms, 1)


if __name__ == '__main__':
    unittest.main()
