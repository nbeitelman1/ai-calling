"""Small conditional start strategy; native Pipecat owns all start mechanics.

VAD should start idle user turns early enough to preserve Smart Turn's audio
history. During bot speech it must defer to MinWords, not defeat that filter.
"""
from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame, VADUserStartedSpeakingFrame
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start import VADUserTurnStartStrategy


class IdleOnlyVADUserTurnStartStrategy(VADUserTurnStartStrategy):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._bot_speaking=False

    async def process_frame(self, frame):
        if isinstance(frame,BotStartedSpeakingFrame):
            self._bot_speaking=True
        elif isinstance(frame,BotStoppedSpeakingFrame):
            self._bot_speaking=False
        if self._bot_speaking and isinstance(frame,VADUserStartedSpeakingFrame):
            return ProcessFrameResult.CONTINUE
        return await super().process_frame(frame)
