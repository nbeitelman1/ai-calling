"""Pinned 1.8.0 contracts, not live-provider or complete media-call acceptance."""
import asyncio
import importlib.metadata
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from pipecat.frames.frames import (
    BotStartedSpeakingFrame, BotStoppedSpeakingFrame, CancelFrame, EndFrame,
    UserStartedSpeakingFrame, UserStoppedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams
from pipecat.serializers.telnyx import TelnyxFrameSerializer
from pipecat.turns.user_idle_controller import UserIdleController
from pipecat.utils.asyncio.task_manager import TaskManager


class TestClosingNativeContracts(unittest.IsolatedAsyncioTestCase):
    def test_target_version_and_idle_default(self):
        self.assertEqual(importlib.metadata.version('pipecat-ai'), '1.8.0')
        self.assertEqual(LLMUserAggregatorParams().user_idle_timeout, 0)

    async def test_serializer_auto_hangup_true_claims_once_for_duplicate_end_cancel(self):
        serializer = TelnyxFrameSerializer(stream_id='offline', outbound_encoding='PCMU',
            inbound_encoding='PCMU', call_control_id='offline', api_key='offline')
        serializer._hang_up_call = AsyncMock()
        for frame in (EndFrame(), EndFrame(), CancelFrame()):
            await serializer.serialize(frame)
        serializer._hang_up_call.assert_awaited_once()

    async def test_serializer_can_delegate_all_hangup_to_one_external_owner(self):
        serializer = TelnyxFrameSerializer(stream_id='offline', outbound_encoding='PCMU',
            inbound_encoding='PCMU', params=TelnyxFrameSerializer.InputParams(auto_hang_up=False))
        serializer._hang_up_call = AsyncMock()
        for frame in (EndFrame(), EndFrame(), CancelFrame()):
            await serializer.serialize(frame)
        serializer._hang_up_call.assert_not_awaited()

    async def exercise_idle(self, frames, expect_event):
        manager = TaskManager()
        controller = UserIdleController(user_idle_timeout=.04)
        event = asyncio.Event()
        callback_args = []
        @controller.event_handler('on_user_turn_idle')
        async def on_idle(sender):
            callback_args.append(sender)
            event.set()
        await controller.setup(SimpleNamespace(task_manager=manager))
        try:
            for frame in frames:
                await controller.process_frame(frame)
            if expect_event:
                await asyncio.wait_for(event.wait(), .5)
                self.assertEqual(callback_args, [controller])
            else:
                await asyncio.sleep(.10)
                self.assertFalse(event.is_set())
        finally:
            await controller.cleanup()
            self.assertEqual([task for task in manager.current_tasks() if not task.done()], [])

    async def test_native_idle_event_after_bot_stops(self):
        await self.exercise_idle([BotStoppedSpeakingFrame()], True)

    async def test_native_idle_timer_cancels_when_bot_resumes(self):
        await self.exercise_idle([BotStoppedSpeakingFrame(), BotStartedSpeakingFrame()], False)

    async def test_native_idle_timer_suppressed_mid_user_turn(self):
        await self.exercise_idle([UserStartedSpeakingFrame(), BotStoppedSpeakingFrame()], False)

    async def test_pinned_idle_controller_does_not_rearm_on_empty_user_stop_alone(self):
        # Observed version limitation, NOT desired application behavior. A native
        # idle-only migration must test/cover the cough/empty-transcript path.
        await self.exercise_idle([BotStoppedSpeakingFrame(), UserStartedSpeakingFrame(),
                                  UserStoppedSpeakingFrame()], False)


if __name__ == '__main__':
    unittest.main()
