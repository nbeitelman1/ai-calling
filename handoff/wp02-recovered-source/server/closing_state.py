"""Pure WP-2 closing controller; no I/O, timers, providers or framework imports.

The caller supplies generation-tagged playback events. A transport clear is NOT
successful playback. This module does not itself emit audio, EndFrame, or a
carrier command; runtime integration must preserve that distinction.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


class ClosingPhase(str, Enum):
    OPEN = 'OPEN'
    CLOSING = 'CLOSING'
    ENDED = 'ENDED'


GOODBYE_LINE = 'Thank you for your time. Goodbye.'
HAND_BACK_LINE = "Nathan will need to take this over. I'll end the call now."
SILENCE_LINE = "I haven't heard anything, so I'll end the call now. Goodbye."

# Only ordinary conversation endings can reopen. Hard limits, refusal/handoff,
# and faults must not be bypassed by a late question. Existing reason codes stay.
_REASON_RULES = {
    'caller_goodbye': (GOODBYE_LINE, True),
    'assistant_goodbye': (GOODBYE_LINE, True),
    'objective_complete': (GOODBYE_LINE, True),
    'loop_in_requested': (HAND_BACK_LINE, False),
    'repetition_guard': (GOODBYE_LINE, False),
    'silence_timeout': (SILENCE_LINE, False),
    'max_minutes': (GOODBYE_LINE, False),
    'test_budget_exceeded': (GOODBYE_LINE, False),
    'soft_cap': (GOODBYE_LINE, False),
    'monthly_cap': (GOODBYE_LINE, False),
    'callee_opt_out': (GOODBYE_LINE, False),
    'phone_tree_or_voicemail': (GOODBYE_LINE, False),
    'pipeline_error': (GOODBYE_LINE, False),
    'tts_limit': (GOODBYE_LINE, False),
}

# Whole-utterance matching is intentional: a quoted goodbye, a company name or
# 'goodbye, but what time ...' must not be mistaken for a terminal intent.
_ACK_UNIT = (r'thank you(?: very much| for your (?:time|help))?|thanks(?: a lot)?|'
             r'goodbye|good bye|bye(?: for now)?|okay|ok|all right|alright|'
             r'you too|and|have a (?:good|nice|great) day|take care|'
             r'talk to you (?:later|soon)|see you(?: later)?')
_ACK = re.compile(rf'(?:{_ACK_UNIT})(?:\s+(?:{_ACK_UNIT}))*\Z')
_END_WORD = re.compile(r'\b(?:goodbye|good bye|bye|take care|have a (?:good|nice|great) day|'
                       r'talk to you (?:later|soon)|see you(?: later)?)\b')


def _normalized(text: str) -> str:
    return ' '.join(re.sub(r'[^a-z0-9 ]', ' ', (text or '').lower()).split())


def is_acknowledgement(text: str) -> bool:
    normalized = _normalized(text)
    return not normalized or bool(_ACK.fullmatch(normalized))


def is_goodbye_intent(text: str) -> bool:
    normalized = _normalized(text)
    return bool(_ACK.fullmatch(normalized) and _END_WORD.search(normalized))


@dataclass(frozen=True)
class ClosingLine:
    token: int
    text: str
    reason: str


class ClosingState:
    """At-most-once effect claims per closing generation, one lifetime reopen.

    A synchronous caller can atomically claim a line/hangup before awaiting its
    side effect. Do not treat a claimed effect as a completed effect. In
    particular, claim_hangup is a dispatch claim, not carrier acknowledgment.
    """

    def __init__(self):
        self.phase = ClosingPhase.OPEN
        self.reason = ''
        self.generation = 0
        self.reopens = 0
        self._can_reopen = False
        self._line = ''
        self._line_claimed = False
        self._started = False
        self._stopped = False
        self._interrupted = False
        self._lines_claimed = 0
        self._hangup_claims = 0
        self.failure = ''

    def request_close(self, reason: str) -> bool:
        """Enter CLOSING once; a mandatory trigger can upgrade an ordinary close.

        An upgrade preserves an already-claimed line/generation rather than
        queuing a second goodbye. First mandatory reason wins when faults race.
        """
        if reason not in _REASON_RULES:
            raise ValueError('Unknown closing reason')
        if self.phase == ClosingPhase.ENDED:
            return False
        line, reversible = _REASON_RULES[reason]
        if self.phase == ClosingPhase.CLOSING:
            if _REASON_RULES[self.reason][1] and not reversible:
                self.reason = reason
                self._can_reopen = False
                if not self._line_claimed:
                    self._line = line
            return False
        self.phase = ClosingPhase.CLOSING
        self.reason = reason
        self.generation += 1
        self._can_reopen = reversible and self.reopens == 0
        self._line = line
        self._line_claimed = self._started = self._stopped = self._interrupted = False
        self.failure = ''
        return True

    def take_line(self) -> ClosingLine | None:
        if self.phase != ClosingPhase.CLOSING or self._line_claimed:
            return None
        self._line_claimed = True
        self._lines_claimed += 1
        return ClosingLine(self.generation, self._line, self.reason)

    def _current(self, token: int) -> bool:
        return self.phase == ClosingPhase.CLOSING and token == self.generation

    def playback_started(self, token: int) -> bool:
        if not self._current(token) or not self._line_claimed or self._started or self._interrupted:
            return False
        self._started = True
        return True

    def playback_stopped(self, token: int) -> bool:
        if not self._current(token) or not self._started or self._stopped or self._interrupted:
            return False
        self._stopped = True
        return True

    def playback_interrupted(self, token: int) -> bool:
        if not self._current(token) or self._interrupted:
            return False
        self._interrupted = True
        self._stopped = False
        return True

    def on_user_text(self, text: str) -> str:
        if self.phase == ClosingPhase.OPEN:
            return 'continue'
        if self.phase == ClosingPhase.ENDED or not self._can_reopen or is_acknowledgement(text):
            return 'suppress'
        self.reopens += 1
        self.phase = ClosingPhase.OPEN
        self._can_reopen = False
        self._interrupted = True
        self._stopped = False
        return 'reopen'

    def claim_hangup(self, token: int) -> bool:
        if not self._current(token) or not self._stopped or self._interrupted:
            return False
        self.phase = ClosingPhase.ENDED
        self._hangup_claims += 1
        return True

    def fail_closed(self, token: int, failure: str) -> bool:
        """Bounded failure escape, never relabeled as successful audio playback."""
        if not self._current(token) or not failure:
            return False
        self.failure = failure
        self.phase = ClosingPhase.ENDED
        self._hangup_claims += 1
        return True

    def summary(self) -> dict:
        return {'phase': self.phase.value, 'reason': self.reason,
                'generation': self.generation, 'reopens': self.reopens,
                'lines_claimed': self._lines_claimed, 'hangup_claims': self._hangup_claims,
                'playback_started': self._started, 'playback_complete': self._stopped and not self._interrupted,
                'interrupted': self._interrupted, 'failure': self.failure,
                'scope': 'controller_effect_claims_not_carrier_receipts'}
