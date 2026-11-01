"""Version-scoped turn policy for the Pipecat 1.8.0 candidate.

The explicit stack is opt-in until WP-1 acceptance and operator review finish.
No provider I/O, model invocation, package upgrade or controller decision lives
in this module. The framework retains ownership of turn and mute mechanics.
"""
from dataclasses import asdict, dataclass
import os
from typing import Mapping


@dataclass(frozen=True)
class TurnPolicy:
    mode: str = 'legacy'
    min_interrupt_words: int = 2
    filter_incomplete: bool = False
    empty_turn_policy: str = 'existing_silence_policy_reprompt'

    @property
    def explicit(self):
        return self.mode == 'explicit'

    @classmethod
    def from_env(cls, playbook: str, environ: Mapping[str,str] | None = None):
        env=os.environ if environ is None else environ
        mode=env.get('AI_CALLING_TURN_STACK','legacy').strip().lower()
        if mode not in ('legacy','explicit'):
            raise ValueError('AI_CALLING_TURN_STACK must be legacy or explicit')
        try:
            words=int(env.get('AI_CALLING_MIN_INTERRUPT_WORDS','2'))
        except (ValueError,TypeError) as exc:
            raise ValueError('AI_CALLING_MIN_INTERRUPT_WORDS must be 2 or 3') from exc
        if words not in (2,3):
            raise ValueError('AI_CALLING_MIN_INTERRUPT_WORDS must be 2 or 3')
        flag=env.get('AI_CALLING_FILTER_INCOMPLETE_TURNS','0').strip()
        if flag not in ('0','1'):
            raise ValueError('AI_CALLING_FILTER_INCOMPLETE_TURNS must be 0 or 1')
        enabled=mode=='explicit' and flag=='1' and playbook=='appointment_scheduling'
        return cls(mode=mode,min_interrupt_words=words,filter_incomplete=enabled)

    def receipt(self):
        return {**asdict(self),'use_interim':False,
                'target_pipecat':'1.8.0','output_transport_sample_rate':8000 if self.explicit else None}


def native_user_params(policy: TurnPolicy, vad_analyzer):
    """Construct only APIs verified in the pinned package; no local framework clone."""
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
    from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams
    from pipecat.turns.user_start import MinWordsUserTurnStartStrategy
    from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
    from pipecat.turns.user_mute import MuteUntilFirstBotCompleteUserMuteStrategy
    from pipecat.turns.user_turn_strategies import UserTurnStrategies, FilterIncompleteUserTurnStrategies

    if not policy.explicit:
        raise ValueError('native_user_params is only used by the explicit stack')
    # One acoustic stop detector. The optional completeness gate wraps that
    # detector; it is not a second VAD/silence timer competing to stop the turn.
    from turn_start import IdleOnlyVADUserTurnStartStrategy
    start=[IdleOnlyVADUserTurnStartStrategy(),
           MinWordsUserTurnStartStrategy(min_words=policy.min_interrupt_words,use_interim=False)]
    stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())]
    strategy_type=FilterIncompleteUserTurnStrategies if policy.filter_incomplete else UserTurnStrategies
    strategies=strategy_type(start=start,stop=stop)
    return LLMUserAggregatorParams(
        vad_analyzer=vad_analyzer,
        user_turn_strategies=strategies,
        user_mute_strategies=[MuteUntilFirstBotCompleteUserMuteStrategy()],
        user_idle_timeout=0,  # The existing app-level SilencePolicy owns prompts.
        user_turn_stop_timeout=5.0,
        audio_idle_timeout=1.0,
    )
