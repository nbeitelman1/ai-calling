"""Live voice pipeline: Telnyx media -> Groq STT -> Gemini -> ElevenLabs TTS.

Built Oct 29, 2026. Groq Whisper provides request-based transcription. No call
is dialled by this module: dialling is the parent's job through the
canonical adapter, and this module only answers the media websocket
Telnyx connects after streaming_start. No credential VALUE is read
here — refs are environment-variable names resolved at call-build time,
and a missing ref fails closed before any pipeline starts.

Chain (Pipecat 1.8.0, verified against the installed package):
  FastAPIWebsocketTransport (TelnyxFrameSerializer, PCMU 8 kHz)
    -> VADProcessor (Silero, pipeline-owned 16 kHz rate; user speech interrupts TTS = barge-in)
    -> GroqSTTService (one request per local-VAD speech segment)
    -> TranscriptTap (user side)
    -> LLMFullResponseAggregator (LLMContext with the fixed system prompt)
    -> GoogleLLMService (model resolved dynamically, never hard-pinned)
    -> TranscriptTap (assistant side) + outgoing-text guard
    -> ElevenLabsTTSService (Jessica, voice id cgSgspJ2msm6clMCkdW9)
    -> transport.output()

Brain ladder (Nathan, Oct 29): fast model -> frontier model (automatic)
-> Nathan loop-in (last). The frontier try is bounded IN CODE by the
EscalationState machine from voice_pipeline.py: max 3 frontier turns
AND max 120 seconds, whichever first. Model swap at runtime uses
Pipecat's LLMUpdateSettingsFrame — the supported settings-update path.
Loop-in bridging (a second Telnyx dial + bridge to Nathan) is NOT
implemented here: on the nathan stage the pipeline speaks the loop-in
line, ends gracefully, and records loop_in_requested for the parent to
act on. That is a named gap for business calls, not a silent fallback.

Hard rules (from call_job.py / server.py, not overridable by a job):
- Disclosure opening spoken verbatim first, before any other agent speech:
  "Hi, this is Nathan's assistant calling on his behalf."
- Allowed decisions on the test call: NONE except ending the call.
- Forbidden: SSN, card/bank numbers, one-time codes, passwords,
  background-check consent, legal-attestation signatures, payments
  without Nathan's explicit per-call approval. Outgoing assistant text
  is scanned before TTS (see model_select.sanitize_outgoing).
- Cost gates: $1.00 hard budget (test call), $5.00/call soft cap,
  $20.00/month ledger cap. Projected spend over a gate ends the call
  gracefully instead of spending on.
"""
from __future__ import annotations

import asyncio
import json
import hashlib
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from model_select import (
    PER_CALL_SOFT_CAP_CENTS,
    TEST_CALL_BUDGET_CENTS,
    CallCost,
    CostLedger,
    resolve_models_from_env,
    sanitize_outgoing,
)
from telnyx_media import MediaAttachGate
from voice_pipeline import (
    AI_IDENTITY_ANSWER,
    DISCLOSURE_PREFIX,
    EscalationState,
    FRONTIER_TRIGGERS,
    validate_job_script,
)

DISCLOSURE_OPENING = "Hi, this is Nathan's assistant calling on his behalf."

FORBIDDEN_TOPICS = (
    "Social Security numbers",
    "card or bank numbers",
    "one-time codes",
    "passwords",
    "background-check consent",
    "legal-attestation signatures",
    "payment authorisation without explicit per-call approval",
)

# Heuristic text signals -> frontier triggers. These are triggers for
# escalation, NOT proof of intent: the frontier model (and the system
# prompt) still decides what to say, and sensitive-data requests are
# refused by the prompt regardless of stage.
_SENSITIVE_WORDS = ("social security", "ssn", "card number", "bank account",
                    "password", "one-time code", "verification code")
_HUMAN_WORDS = ("human", "real person", "representative", "someone else",
                "let me talk to nathan", "bring nathan in")
_COMMIT_WORDS = ("just decide", "decide for me", "go ahead and pay",
                 "book it", "authorize", "authorise", "charge my")


@dataclass(frozen=True)
class LiveCredentialRefs:
    """Environment-variable names ONLY — never values."""
    groq_api_key_ref: str = "GROQ_API_KEY"
    gemini_api_key_ref: str = "GEMINI_API_KEY"
    elevenlabs_api_key_ref: str = "ELEVENLABS_API_KEY"
    telnyx_api_key_ref: str = "TELNYX_API_KEY"
    elevenlabs_voice_id: str = field(default_factory=lambda: os.environ.get(
        "ELEVENLABS_VOICE_ID", "cgSgspJ2msm6clMCkdW9").strip())  # Jessica (Nathan's pick Oct 29, 2026; Natasha id does not exist on this account)


@dataclass
class LiveCallJob:
    to: str
    business: str
    objective: str
    allowed_decisions: list[str] = field(default_factory=list)
    max_minutes: int = 10
    test_call: bool = True
    opening: str = DISCLOSURE_OPENING
    pronunciation_hints: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        from telnyx_adapter.wire import check_e164
        check_e164(self.to)
        if isinstance(self.max_minutes, bool) or not isinstance(self.max_minutes, int) or not 1 <= self.max_minutes <= 60:
            raise ValueError('max_minutes must be an integer in 1..60')
        if not isinstance(self.test_call, bool):
            raise ValueError('test_call must be boolean')
        if not all(isinstance(v, str) and 0 < len(v) <= 4096 for v in (self.business, self.objective, self.opening)):
            raise ValueError('business, objective and opening must be bounded nonempty text')
        if (not isinstance(self.allowed_decisions, list) or len(self.allowed_decisions) > 32
                or not all(isinstance(v, str) and len(v) <= 512 for v in self.allowed_decisions)):
            raise ValueError('invalid allowed_decisions')
        if (not isinstance(self.pronunciation_hints, dict) or len(self.pronunciation_hints) > 64
                or not all(isinstance(k, str) and isinstance(v, str) and len(k) <= 128 and len(v) <= 128
                           for k, v in self.pronunciation_hints.items())):
            raise ValueError('invalid pronunciation_hints')
        validate_job_script(self.opening)
        if not self.allowed_decisions:
            self.allowed_decisions = ["ending the call"]


def build_system_prompt(job: LiveCallJob) -> str:
    pronunciations = ""
    if job.pronunciation_hints:
        pronunciations = "\nPronunciations: " + "; ".join(
            f"{word}: {spoken}" for word, spoken in job.pronunciation_hints.items())
    return (
        "You are Nathan's assistant, making a phone call on his behalf.\n"
        f"Business: {job.business}\n"
        f"Objective: {job.objective}\n"
        "Allowed decisions (nothing else is binding): "
        + ", ".join(job.allowed_decisions)
        + "\n"
        "NEVER ask for, repeat, or act on: " + ", ".join(FORBIDDEN_TOPICS) + ".\n"
        "If any of those is requested, or if the caller asks for a human, "
        "asks if you are AI, or refuses to talk to an assistant, say "
        "\"Nathan will need to take this over. I'll end the call now.\" and end your turn. "
        "No live transfer is implemented; never promise to connect Nathan.\n"
        f"If asked directly whether you are a person or AI, answer exactly: {AI_IDENTITY_ANSWER}\n"
        "Style: short spoken sentences, natural pacing, feminine voice, "
        "warm, confident. Do not claim to be Nathan or any real person. "
        "Do not claim to be human. Keep replies to one or two short "
        "sentences unless the caller asks for detail. If you reach "
        "voicemail or an unresolvable phone tree, end the call without "
        "leaving details and report back.\n"
        "Loop-in triggers: " + ", ".join(FRONTIER_TRIGGERS) + "."
        + pronunciations
    )


class FixedPhraseCache:
    def __init__(self, directory, *, namespace: str, sample_rate: int = 16000):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.namespace = namespace
        self.sample_rate = sample_rate

    def _path(self, text: str) -> Path:
        digest = hashlib.sha256(
            (self.namespace + "\0" + text).encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.pcm"

    def load(self, text: str) -> bytes | None:
        path = self._path(text)
        metadata_path = path.with_suffix(".json")
        if not path.is_file() or not metadata_path.is_file():
            return None
        try:
            audio = path.read_bytes()
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            duration = len(audio) / (self.sample_rate * 2)
            if (metadata != {
                    "content_type": "audio/pcm", "encoding": "pcm_s16le",
                    "sample_rate": self.sample_rate,
                    "channels": 1, "bytes": len(audio),
                    "duration_seconds": round(duration, 6),
                    "sha256": hashlib.sha256(audio).hexdigest(),
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                } or not 0.1 <= duration <= 30.0):
                return None
            return audio
        except Exception:
            return None

    def store(self, text: str, audio: bytes) -> None:
        if not audio or len(audio) % 2:
            raise ValueError("cached PCM must be nonempty 16-bit audio")
        path = self._path(text)
        duration = len(audio) / (self.sample_rate * 2)
        if not 0.1 <= duration <= 30.0:
            raise ValueError("cached PCM duration is implausible")
        token = uuid.uuid4().hex
        temporary = path.with_name(path.name + f".{token}.tmp")
        metadata_path = path.with_suffix(".json")
        metadata_temporary = metadata_path.with_name(
            metadata_path.name + f".{token}.tmp")
        temporary.write_bytes(audio)
        metadata_temporary.write_text(json.dumps({
            "content_type": "audio/pcm", "encoding": "pcm_s16le",
            "sample_rate": self.sample_rate,
            "channels": 1, "bytes": len(audio),
            "duration_seconds": round(duration, 6),
            "sha256": hashlib.sha256(audio).hexdigest(),
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
        metadata_temporary.replace(metadata_path)


@dataclass
class CallActivity:
    clock: object = time.monotonic
    user_speaking: bool = False
    bot_speaking: bool = False
    last_activity: float = field(init=False)
    bot_cycles: int = 0

    def __post_init__(self):
        self.last_activity = self.clock()

    def user_started(self):
        self.user_speaking = True
        self.last_activity = self.clock()

    def user_stopped(self):
        self.user_speaking = False
        self.last_activity = self.clock()

    def bot_started(self):
        self.bot_speaking = True
        self.last_activity = self.clock()

    def bot_stopped(self):
        self.bot_speaking = False
        self.bot_cycles += 1
        self.last_activity = self.clock()


class SilencePolicy:
    def __init__(self):
        self.timeouts = 0

    def on_speech(self):
        self.timeouts = 0

    def on_timeout(self) -> str:
        self.timeouts += 1
        return "reprompt" if self.timeouts == 1 else "end"


@dataclass
class TTSCharacterBudget:
    limit: int
    used: int = 0

    def reserve(self, text: str) -> bool:
        amount = len(text)
        if self.used + amount > self.limit:
            return False
        self.used += amount
        return True


def limit_spoken_response(text: str, stage: str) -> str:
    import re

    sentences = [part.strip() for part in re.findall(r".*?[.!?](?:\s+|$)|.+$", text)
                 if part.strip()]
    limit = 1 if stage == "fast" else 2
    return " ".join(sentences[:limit])


def _resolve_ref(ref_name: str) -> str:
    value = os.environ.get(ref_name, "")
    if not value:
        raise RuntimeError(f"credential reference not provisioned: {ref_name}")
    return value


class _clean_proxy_env:
    """Work around a VM env quirk: NO_PROXY here contains bracketed
    IPv6 entries (e.g. [::1]) that the provider HTTP clients
    ElevenLabs SDKs cannot parse at client construction (InvalidURL).
    Temporarily strip bracketed entries while provider clients are
    constructed, then restore the original values. Localhost entries
    are preserved, so loopback traffic still bypasses the proxy."""

    def __enter__(self):
        self._saved = {key: os.environ.get(key)
                       for key in ("NO_PROXY", "no_proxy")}
        for key in ("NO_PROXY", "no_proxy"):
            value = self._saved[key]
            if value is None:
                continue
            cleaned = ",".join(
                part for part in value.split(",")
                if "[" not in part and "]" not in part)
            os.environ[key] = cleaned
        return self

    def __exit__(self, *exc):
        for key, value in reversed(tuple(self._saved.items())):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


# ---------------------------------------------------------------------------
# Brain ladder — pure logic, unit-testable without Pipecat or providers.
# ---------------------------------------------------------------------------

class BrainLadder:
    """Fast -> frontier -> Nathan, with the bound enforced in code."""

    def __init__(self, *, frontier_model: str, clock=time.time):
        self.state = EscalationState(clock=clock)
        self.frontier_model = frontier_model
        self.transcript: list[dict] = []
        self.loop_in_reason: str | None = None
        self.terminal_reason: str | None = None
        self._last_user_text = ""

    def on_user_text(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            return {"action": "continue"}
        self.transcript.append({"role": "user", "text": text})
        lowered = text.lower()
        import re
        if re.search(r"^(?:no[, ]+)?(?:please\s+)?(?:stop calling|do not call|don't call|do not contact|don't contact|remove me from (?:your|the) list|end (?:this|the) call|hang up)\b", lowered):
            self.terminal_reason = "callee_opt_out"
            return {"action": "end_call", "reason": self.terminal_reason}
        ivr_patterns = (
            "please press ", "to continue press ", "for sales press ",
            "main menu", "say billing", "say sales", "enter your extension",
            "choose from the following", "listen carefully to the following",
            "after the tone please leave", "please leave a message after the tone",
        )
        if not self.transcript[:-1] and any(pattern in lowered for pattern in ivr_patterns):
            self.terminal_reason = "phone_tree_or_voicemail"
            return {"action": "end_call", "reason": "phone_tree_or_voicemail"}
        trigger = None
        if any(word in lowered for word in _HUMAN_WORDS):
            trigger = "refused to talk to assistant"
        elif any(word in lowered for word in _SENSITIVE_WORDS):
            trigger = "sensitive data requested"
        elif any(word in lowered for word in _COMMIT_WORDS):
            trigger = "decision outside job"
        elif text == self._last_user_text and self.state.stage == "fast":
            trigger = "repeated misunderstanding"
        self._last_user_text = text
        if trigger is None:
            return {"action": "continue"}
        if self.state.stage == "fast":
            self.state.escalate_to_frontier(trigger)
            return {"action": "switch_to_frontier", "model": self.frontier_model,
                    "trigger": trigger}
        if self.state.stage == "frontier":
            return {"action": "continue_frontier", "trigger": trigger}
        return {"action": "continue"}

    def on_assistant_text(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            return {"action": "continue"}
        safe = sanitize_outgoing(text)
        self.transcript.append({"role": "assistant", "text": safe})
        if self.state.stage == "frontier":
            stage = self.state.frontier_reply()
            if stage == "nathan":
                self.loop_in_reason = "frontier bound exhausted (3 turns / 120s)"
                return {"action": "loop_in_nathan", "reason": self.loop_in_reason}
        return {"action": "continue"}

    def check_time_bound(self) -> dict:
        """Call periodically (e.g. on every turn): the 120s bound must
        fire even if the frontier stops replying."""
        if self.state.frontier_exhausted() and self.state.stage == "frontier":
            self.state.stage = "nathan"
            self.loop_in_reason = "frontier time bound exhausted (120s)"
            return {"action": "loop_in_nathan", "reason": self.loop_in_reason}
        return {"action": "continue"}

    def stage(self) -> str:
        return self.state.stage


# ---------------------------------------------------------------------------
# Pipecat layer (guarded: importing this module never dials or connects).
# ---------------------------------------------------------------------------
try:
    from pipecat.frames.frames import (
        EndFrame,
        InputAudioRawFrame,
        TextFrame,
        TranscriptionFrame,
        TTSTextFrame,
        LLMUpdateSettingsFrame,
        VADUserStartedSpeakingFrame,
        VADUserStoppedSpeakingFrame,
        TTSAudioRawFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.serializers.telnyx import TelnyxFrameSerializer
    from pipecat.audio.vad.silero import SileroVADAnalyzer

    _PIPECAT_FRAMES = True
except Exception:
    _PIPECAT_FRAMES = False


class TranscriptTap(FrameProcessor):
    """Records the user side of the conversation and feeds the ladder.

    Placed after STT; records finalized TranscriptionFrames. The
    assistant side is handled by ResponseGate below, which must see
    whole responses (for the outgoing-text guard and for counting
    frontier *replies*, not fragments).
    """

    def __init__(self, ladder: BrainLadder, side: str = "user",
                 silence_policy: SilencePolicy | None = None):
        super().__init__()
        self._ladder = ladder
        self._side = side
        self._seen: set[tuple[str, str, str]] = set()
        self._silence_policy = silence_policy

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if _PIPECAT_FRAMES and direction == FrameDirection.DOWNSTREAM:
            if self._side == "user" and isinstance(frame, TranscriptionFrame):
                if not getattr(frame, "finalized", False):
                    await self.push_frame(frame, direction)
                    return
                key = (getattr(frame, "user_id", ""),
                       getattr(frame, "timestamp", ""), frame.text)
                if key in self._seen:
                    await self.push_frame(frame, direction)
                    return
                self._seen.add(key)
                if self._silence_policy:
                    self._silence_policy.on_speech()
                action = self._ladder.on_user_text(frame.text)
                if action["action"] == "end_call":
                    # Do not trigger a new inference after the callee ended it.
                    await self.push_frame(EndFrame(), direction)
                    return
                if action["action"] == "switch_to_frontier":
                    from pipecat.services.settings import LLMSettings
                    await self.push_frame(
                        LLMUpdateSettingsFrame(
                            delta=LLMSettings(model=action["model"])),
                        direction)
                # Switch settings before a transcription can trigger inference.
                await self.push_frame(frame, direction)
                return
        await self.push_frame(frame, direction)


class ResponseGate(FrameProcessor):
    """Buffers one assistant response, guards it, then releases it.

    Why buffering: a forbidden shape (SSN, card number) can span
    tokens, so it cannot be caught reliably on streaming fragments.
    The gate holds LLM text frames between LLMFullResponseStartFrame
    and LLMFullResponseEndFrame, sanitizes the complete response, and
    only then releases it to TTS — or releases the refusal line if the
    response tripped a forbidden pattern. The ladder counts one
    flushed response as one reply, so the 3-turn frontier bound counts
    replies, not fragments.

    Deliberate trade-off, stated plainly: TTS starts after the full
    response instead of streaming token-by-token, so first-audio
    latency is higher than a pure streaming design. Safety and a
    correct bound were chosen over shaving that latency for the test.
    """

    def __init__(self, ladder: BrainLadder,
                 character_budget: TTSCharacterBudget | None = None):
        super().__init__()
        self._ladder = ladder
        self._buffer: list[str] = []
        self._active = False
        self._character_budget = character_budget

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        from pipecat.frames.frames import CancelFrame, InterruptionFrame
        if isinstance(frame, (CancelFrame, InterruptionFrame, EndFrame)):
            self._buffer = []
            self._active = False
        if not _PIPECAT_FRAMES or direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        from pipecat.frames.frames import (
            LLMFullResponseEndFrame,
            LLMFullResponseStartFrame,
            AggregatedTextFrame,
        )
        from pipecat.utils.text.base_text_aggregator import AggregationType
        if isinstance(frame, LLMFullResponseStartFrame):
            self._active = True
            self._buffer = []
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, LLMFullResponseEndFrame):
            full = "".join(self._buffer)
            self._buffer = []
            self._active = False
            action = self._ladder.on_assistant_text(full)
            if action["action"] == "loop_in_nathan":
                # The outer frontier watcher speaks and drains the cached
                # loop-in phrase before ending the call.
                await self.push_frame(frame, direction)
                return
            safe = limit_spoken_response(sanitize_outgoing(full), self._ladder.stage())
            if (safe and self._character_budget
                    and not self._character_budget.reserve(safe)):
                safe = "I've reached the call's speaking limit, so I'll end here. Goodbye."
                await self.push_frame(TextFrame(text=safe), direction)
                await self.push_frame(EndFrame(), direction)
                return
            if safe:
                # Already a complete, guarded response; do not re-tokenize it
                # through NLTK or wait for sentence lookahead a second time.
                await self.push_frame(AggregatedTextFrame(
                    safe, AggregationType.SENTENCE, raw_text=safe), direction)
            await self.push_frame(frame, direction)
            return
        if self._active and isinstance(frame, TextFrame):
            self._buffer.append(frame.text)
            return
        await self.push_frame(frame, direction)


class InboundDebugTap(FrameProcessor):
    """Logging-only tap: separates "no media arrived" from "VAD miss".

    Two pass-through instances (no frame is altered, dropped, or
    delayed):
    - mode="frames", between transport.input() and VADProcessor:
      counts downstream InputAudioRawFrames; logs the first one
      (sample rate + byte size) and every 500th one.
    - mode="vad", between VADProcessor and STT: logs every
      VADUserStartedSpeakingFrame / VADUserStoppedSpeakingFrame.
    Logging goes through loguru (the same channel Pipecat uses, which
    reaches the service journal). An earlier version logged through
    stdlib logging at INFO, which the live server silently dropped:
    uvicorn runs at warning level with no root-logger config, so the
    tap passed frames through but its lines never reached the journal
    — and "zero tap lines" was wrongly read as "no frames arrived".
    """

    def __init__(self, mode: str, activity: CallActivity | None = None):
        super().__init__()
        self._mode = mode
        self._count = 0
        self._activity = activity

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if _PIPECAT_FRAMES and direction == FrameDirection.DOWNSTREAM:
            if self._mode == "frames" and isinstance(frame, InputAudioRawFrame):
                self._count += 1
                if self._count == 1 or self._count % 500 == 0:
                    logger.info("inbound-audio frame #{} sample_rate={} bytes={}",
                                self._count, getattr(frame, "sample_rate", "?"),
                                len(getattr(frame, "audio", b"") or b""))
            elif self._mode == "vad" and isinstance(
                    frame, (VADUserStartedSpeakingFrame,
                            VADUserStoppedSpeakingFrame)):
                logger.info("vad-transition {}", type(frame).__name__)
                if self._activity:
                    if isinstance(frame, VADUserStartedSpeakingFrame):
                        self._activity.user_started()
                    else:
                        self._activity.user_stopped()
        await self.push_frame(frame, direction)


class BotActivityTap(FrameProcessor):
    def __init__(self, activity: CallActivity):
        super().__init__()
        self._activity = activity

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame
        # Transport playout state, not synthesis completion. This tap sits
        # BEFORE transport.output(), so only the upstream broadcast counts.
        if direction == FrameDirection.UPSTREAM:
            if isinstance(frame, BotStartedSpeakingFrame):
                self._activity.bot_started()
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self._activity.bot_stopped()
        await self.push_frame(frame, direction)


class InputAudioRechunker(FrameProcessor):
    """Emit exact 32 ms PCM16 frames so VAD confirmation is buffer-invariant."""

    def __init__(self, sample_rate: int = 16000, channels: int = 1):
        super().__init__()
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_bytes = sample_rate * 32 // 1000 * channels * 2
        self._buffer = bytearray()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if (direction == FrameDirection.DOWNSTREAM
                and isinstance(frame, InputAudioRawFrame)):
            if frame.sample_rate != self.sample_rate or frame.num_channels != self.channels:
                raise ValueError("unexpected audio format before VAD")
            self._buffer.extend(frame.audio)
            while len(self._buffer) >= self.frame_bytes:
                audio = bytes(self._buffer[:self.frame_bytes])
                del self._buffer[:self.frame_bytes]
                await self.push_frame(
                    InputAudioRawFrame(audio, self.sample_rate, self.channels),
                    direction)
            return
        if isinstance(frame, EndFrame):
            self._buffer.clear()
        await self.push_frame(frame, direction)


class CachedElevenLabsTTSService:
    """Mixin factory implemented below after importing ElevenLabs at build time."""

    @staticmethod
    def create(base_class, *, cache: FixedPhraseCache, fixed_phrases: set[str],
               character_budget: TTSCharacterBudget | None = None, **kwargs):
        from pipecat.services.websocket_service import _BoundedCloseConnection

        class _CloseHeaderTolerantConnection(_BoundedCloseConnection):
            """Pipecat connection class tolerating `Connection: close` on a 101.

            Root cause (Oct 30, 2026): the VM egress proxy TLS-intercepts
            provider traffic and re-originates the ElevenLabs handshake
            response, which reaches the client as a valid 101
            (`Upgrade: websocket`, correct Sec-WebSocket-Accept) but with
            `Connection: close` instead of `Connection: upgrade`.
            websockets 17.3 strictly rejects that (InvalidUpgrade:
            invalid Connection header: close) before the socket opens,
            so this TTS service never connected and live calls were
            silent. The loopback CONNECT shim cannot help here: the
            header sits inside the TLS tunnel, on the 101 response
            itself, past the CONNECT exchange the shim normalizes
            (verified empirically: the handshake fails identically via
            the shim). So, scoped to this service's connections only,
            wrap the sans-IO protocol's process_response and normalize
            the header (drop `close`, ensure `upgrade`) just before the
            library validates it. The library itself is untouched, and
            deleting this class restores stock behavior.
            """

            def __init__(self, protocol, **kwargs):
                super().__init__(protocol, **kwargs)
                original_process_response = protocol.process_response

                def process_response(response,
                                     _original=original_process_response):
                    from websocket_compat import normalize_close_upgrade
                    if normalize_close_upgrade(response):
                        logger.warning('applied explicitly enabled close-header proxy workaround')
                    return _original(response)

                protocol.process_response = process_response

        class Service(base_class):
            async def _websocket_connect(self, uri, **kwargs):
                # WebsocketService setdefaults create_connection to its
                # own _BoundedCloseConnection; an explicit value here
                # wins, routing this service's handshakes through the
                # tolerant subclass above.
                if os.environ.get('AI_CALLING_ALLOW_CLOSE_HEADER_WORKAROUND') == '1':
                    kwargs.setdefault("create_connection", _CloseHeaderTolerantConnection)
                return await super()._websocket_connect(uri, **kwargs)

            async def run_tts(self, text, context_id):
                cached = cache.load(text) if text in fixed_phrases else None
                if cached is not None:
                    # The pinned 1.8.0 generator contract requires a context
                    # BEFORE yielding audio. Cached audio is synchronous;
                    # on_turn_context_completed owns closure AFTER all text
                    # has been queued. Closing here drops late context frames.
                    from pipecat.frames.frames import (
                        TTSStartedFrame,
                        TTSStoppedFrame,
                    )
                    if not self.audio_context_available(context_id):
                        await self.create_audio_context(context_id)
                        yield TTSStartedFrame(context_id=context_id)
                    for offset in range(0, len(cached), 6400):
                        yield TTSAudioRawFrame(
                            cached[offset:offset + 6400], self.sample_rate, 1,
                            context_id=context_id)
                    # No provider alignment arrives on a cache hit. Pipecat
                    # 1.8's sequencer supplies the remaining text at context
                    # completion; appending TTSTextFrame here duplicates it.
                    yield TTSStoppedFrame(context_id=context_id)
                    return
                if character_budget is not None and not character_budget.reserve(text):
                    from pipecat.frames.frames import ErrorFrame
                    await self.push_frame(ErrorFrame(
                        error='TTS character budget exhausted', fatal=True), FrameDirection.UPSTREAM)
                    return
                captured = bytearray()
                async for frame in super().run_tts(text, context_id):
                    if isinstance(frame, TTSAudioRawFrame):
                        captured.extend(frame.audio)
                    yield frame
                if text in fixed_phrases and captured:
                    cache.store(text, bytes(captured))
        return Service(**kwargs)


class _CountingTelnyxSerializer(TelnyxFrameSerializer):
    """Counting + inbound-only wrapper around the real Telnyx serializer.

    Two jobs, both visible in the journal via loguru:
    - Counting: loguru-logs the first inbound media event (with its
      track value) and every 500th one. On a live call this
      distinguishes "Telnyx sent nothing over the websocket" from "the
      serializer dropped it" even if the pipeline stalls downstream.
    - Inbound-only filter: DROP media events whose track is not None,
      "inbound", or "inbound_track" before delegating to the real
      deserializer. On a both-tracks stream Telnyx sends outbound-track
      packets too, and the base serializer ignores the track field, so
      outbound packets (silence / bot echo) were concatenated into the
      SAME resampler stream as inbound audio, chopping VAD input into
      alternating slices. Events with no track (older/unspecified
      shape) still pass. Dropped events are counted and logged (first
      + every 500th, with the track value) so a surprising track shape
      on the wire is visible, not silent. No passing frame is altered
      or delayed.
      WHY BOTH "inbound" AND "inbound_track": the two Telnyx fields
      use different values. The Call Control REQUEST parameter
      stream_track uses inbound_track / outbound_track / both_tracks,
      but the WebSocket media EVENT field media.track uses inbound /
      outbound (Twilio-compatible event shape). The project's earlier
      filter accepted only "inbound_track" — a value that came from
      self-invented fixtures, never from an observed wire event — so
      it would have dropped every real caller-audio event whose track
      is "inbound". Accepting both is defensive and matches
      independent Telnyx integrations, which accept both forms.
    """

    def __init__(self, *args, adapter=None, attempt_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._adapter = adapter
        self._attempt_id = attempt_id
        self._media_count = 0
        self._dropped_count = 0
        self._last_sequence = None
        self._sequence_gaps = 0
        self._duplicate_or_old = 0
        self._input_payload_bytes = 0
        self._output_payload_bytes = 0
        self._output_media_count = 0
        self.output_audio_ready = asyncio.Event()

    async def serialize(self, frame):
        serialized = await super().serialize(frame)
        if serialized:
            message = json.loads(serialized)
            if message.get('event') == 'media':
                import base64
                self._output_payload_bytes += len(base64.b64decode(message['media']['payload']))
                self._output_media_count += 1
                self.output_audio_ready.set()
                if self._output_media_count == 1:
                    logger.info('media-stage first_outbound_payload attempt={}', self._attempt_id)
        return serialized

    async def deserialize(self, data):
        try:
            message = json.loads(data)
        except Exception:
            message = None
        if isinstance(message, dict) and message.get("event") == "media":
            sequence = message.get("sequence_number")
            if isinstance(sequence, str) and sequence.isdigit():
                sequence = int(sequence)
            if isinstance(sequence, int):
                if self._last_sequence is not None:
                    if sequence <= self._last_sequence:
                        self._duplicate_or_old += 1
                    elif sequence > self._last_sequence + 1:
                        self._sequence_gaps += sequence - self._last_sequence - 1
                self._last_sequence = max(sequence, self._last_sequence or sequence)
            media = message.get("media")
            if not isinstance(media, dict) or not isinstance(media.get("payload"), str):
                self._dropped_count += 1
                logger.warning("ws-media dropped malformed event #{}",
                               self._dropped_count)
                return None
            track = media.get("track")
            if track not in (None, "inbound", "inbound_track"):
                self._dropped_count += 1
                if self._dropped_count == 1 or self._dropped_count % 500 == 0:
                    logger.info("ws-media dropped non-inbound track={} #{}",
                                track, self._dropped_count)
                return None
            try:
                import base64
                self._input_payload_bytes += len(
                    base64.b64decode(media["payload"], validate=True))
            except Exception:
                self._dropped_count += 1
                return None
            self._media_count += 1
            if self._media_count == 1 or self._media_count % 500 == 0:
                logger.info("ws-media event #{} track={}",
                            self._media_count, track)
        return await super().deserialize(data)

    async def _hang_up_call(self):
        """Use the deployment proxy environment for Telnyx REST hangup."""
        if self._adapter is None or not self._attempt_id:
            logger.error("canonical Telnyx hangup unavailable")
            return
        await _hangup_via_adapter(
            self._adapter, self._attempt_id, self._api_key,
            "pipeline end")


def _as_float(value) -> float:
    """Coerce a VAD numeric to a plain float.

    Silero's voice_confidence returns a shape-(1,) ndarray, not a
    float; float() rejects ndim > 0 arrays under this NumPy version,
    while ndarray.item() works for any size-1 array. Plain floats
    and ints pass through unchanged.
    """
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return float(item())
        except ValueError:
            pass
    return float(value)


class _LoggingSileroVADAnalyzer(SileroVADAnalyzer):
    """Measurement-only Silero wrapper: logs per-chunk VAD signals.

    The base analyzer's per-chunk confidence and smoothed volume live
    inside _run_analyzer and are never exposed, so this subclass
    captures them at the two points they are computed:
    voice_confidence() stores the chunk's confidence, and
    _get_smoothed_volume() stores the chunk's volume and logs the
    sampled line (every 10th chunk, i.e. ~320 ms at 16 kHz) once both
    values for that chunk are known. analyze_audio() logs any
    internal state transition (QUIET/STARTING/SPEAKING/STOPPING) —
    STARTING blips are otherwise invisible, since only SPEAKING
    transitions become VADUserStarted/Stopped frames downstream.
    Gate params are untouched and no VAD behavior changes; this only
    logs, via loguru, so the next live call distinguishes
    confidence-bound vs volume-bound vs state-machine-bound failure
    from real data. Constructed with no sample_rate arg on purpose,
    like the plain analyzer in the pipeline: the pipeline setup owns
    the rate (16 kHz).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._vad_chunk_count = 0
        self._vad_last_confidence = None
        self._vad_last_volume = None

    def voice_confidence(self, buffer):
        # Silero returns a 1-element ndarray, not a float; coerce so
        # downstream comparisons and log formatting behave.
        conf = _as_float(super().voice_confidence(buffer))
        self._vad_chunk_count += 1
        self._vad_last_confidence = conf
        return conf

    def _get_smoothed_volume(self, audio):
        vol = _as_float(super()._get_smoothed_volume(audio))
        self._vad_last_volume = vol
        if self._vad_chunk_count % 10 == 0:
            logger.info("vad-chunk #{} confidence={:.3f} volume={:.3f}",
                        self._vad_chunk_count,
                        self._vad_last_confidence
                        if self._vad_last_confidence is not None else -1.0,
                        vol)
        return vol

    async def analyze_audio(self, buffer):
        before = getattr(self, "_vad_state", None)
        state = await super().analyze_audio(buffer)
        after = getattr(self, "_vad_state", None)
        if before is not None and after is not None and before != after:
            logger.info("vad-state {} -> {} chunk #{} confidence={:.3f} volume={:.3f}",
                        before.name, after.name, self._vad_chunk_count,
                        self._vad_last_confidence
                        if self._vad_last_confidence is not None else -1.0,
                        self._vad_last_volume
                        if self._vad_last_volume is not None else -1.0)
        return state


def build_live_pipeline(websocket, call_data: dict, job: LiveCallJob,
                        refs: LiveCredentialRefs, models: dict, adapter=None,
                        attempt_id: str | None = None):
    """Build (not yet run) the Pipecat pipeline for one media websocket.

    call_data comes from a single parse_telephony_websocket() call made
    by handle_media_websocket() — it is parsed exactly once, gated, and
    then handed here. Returns (task, ladder, services). Raises on
    missing credential refs — fail closed, never a half pipeline.
    """
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
    )
    from pipecat.processors.audio.vad_processor import VADProcessor
    from pipecat.serializers.telnyx import TelnyxFrameSerializer
    from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
    from pipecat.services.google.llm import GoogleLLMService
    from pipecat.services.groq.stt import GroqSTTService
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketParams,
        FastAPIWebsocketTransport,
    )

    stream_id = call_data["stream_id"]
    call_control_id = call_data["call_id"]
    outbound_encoding = call_data.get("outbound_encoding") or "PCMU"

    serializer = _CountingTelnyxSerializer(
        stream_id=stream_id,
        outbound_encoding=outbound_encoding,
        inbound_encoding="PCMU",
        call_control_id=call_control_id,
        api_key=_resolve_ref(refs.telnyx_api_key_ref),
        adapter=adapter,
        attempt_id=attempt_id,
    )
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
        ),
    )

    ladder = BrainLadder(frontier_model=models["frontier"])
    activity = CallActivity()
    silence_policy = SilencePolicy()
    character_budget = TTSCharacterBudget(
        int(os.environ.get("AI_CALLING_TTS_CHAR_LIMIT", "4000")))
    from provider_config import tts_configuration
    tts_config = tts_configuration()
    tts_model, tts_sample_rate = tts_config['model'], tts_config['sample_rate']
    phrase_cache = FixedPhraseCache(
        os.environ.get(
            "AI_CALLING_PHRASE_CACHE",
            str(Path(__file__).resolve().parent.parent / "logs" / "phrase-cache")),
        namespace=(f"{refs.elevenlabs_voice_id}/{tts_model}/"
                   f"pcm_{tts_sample_rate}"), sample_rate=tts_sample_rate,
    )
    fixed_phrases = {
        job.opening,
        "Are you still there?",
        "I haven't heard anything, so I'll end the call now. Goodbye.",
        "Nathan will need to take this over. I'll end the call now.",
        "I can't share or repeat that kind of information on a call.",
    }
    with _clean_proxy_env():
        stt = GroqSTTService(
            api_key=_resolve_ref(refs.groq_api_key_ref),
            settings=GroqSTTService.Settings(model=models["stt"]),
        )
        llm = GoogleLLMService(
            api_key=_resolve_ref(refs.gemini_api_key_ref),
            settings=GoogleLLMService.Settings(model=models["fast"]),
        )
        tts = CachedElevenLabsTTSService.create(
            ElevenLabsTTSService,
            cache=phrase_cache,
            fixed_phrases=fixed_phrases,
            character_budget=character_budget,
            # Flush streaming resampler latency before ending an utterance.
            # This uses Pipecat's supported pre-stop silence insertion.
            push_silence_after_stop=True,
            silence_time_s=0.2,
            api_key=_resolve_ref(refs.elevenlabs_api_key_ref),
            sample_rate=tts_sample_rate,
            settings=ElevenLabsTTSService.Settings(
                voice=refs.elevenlabs_voice_id, model=tts_model),
        )
    context = LLMContext([{"role": "system", "content": build_system_prompt(job)}])
    aggregators = LLMContextAggregatorPair(context)

    pipeline = Pipeline([
        transport.input(),
        InboundDebugTap("frames"),
        InputAudioRechunker(),
        # No sample_rate arg on purpose: the pipeline sets the analyzer
        # rate from the transport (16 kHz) at setup. Pinning 8000 here
        # kept the analyzer at 8 kHz (VADAnalyzer.set_sample_rate()
        # keeps a truthy init rate), so it consumed 16 kHz audio at
        # half speed, Silero confidence never cleared the 0.7 gate, and
        # local-VAD STT never committed — the Oct 29 Jessica-call
        # inbound silence. VAD confidence/start/stop/min_volume params
        # are untouched (defaults). The analyzer is the logging
        # subclass above: per-chunk confidence/volume lines + state
        # transitions, measurement only.
        VADProcessor(vad_analyzer=_LoggingSileroVADAnalyzer()),
        InboundDebugTap("vad", activity),
        stt,
        TranscriptTap(ladder, "user", silence_policy),
        aggregators.user(),
        llm,
        ResponseGate(ladder),  # Monetary/provider characters are reserved at run_tts.
        tts,
        BotActivityTap(activity),
        transport.output(),
        # Capture actual TTS text after sanitization and playout, not raw LLM output.
        aggregators.assistant(),
    ])
    task = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True,
                              audio_in_sample_rate=16000,
                              audio_out_sample_rate=tts_sample_rate),
        enable_rtvi=False,
        idle_timeout_secs=None,
    )
    return task, ladder, {"stt": stt, "llm": llm, "tts": tts,
                          "context": context,
                          "serializer": serializer,
                          "activity": activity,
                          "silence_policy": silence_policy,
                          "character_budget": character_budget,
                          "phrase_cache": phrase_cache,
                          "call_control_id": call_control_id,
                          "stream_id": stream_id}


def _ws_disconnected(websocket) -> bool:
    """True once the peer media websocket is closed.

    Starlette flips ``client_state`` to DISCONNECTED when its reader
    observes the peer close, even though FastAPIWebsocketTransport
    pushes no EndFrame downstream on disconnect (which is why the
    pipeline runner alone never notices a callee hangup).
    """
    state = getattr(websocket, "client_state", None)
    if state is None:
        return False
    name = getattr(state, "name", None)
    if name is None:
        name = str(state)
    return name.endswith("DISCONNECTED")


async def _wait_for_disconnect(websocket, adapter, attempt_id,
                               interval: float = 0.25) -> str:
    """Return a reason string as soon as the call ends; else run forever.

    Two signals, checked in order: transport disconnect (the peer
    media websocket closing) and adapter hangup state (the call.hangup
    webhook processed by this same process). Adapter access is
    best-effort: a test double or a missing attempt must not break
    the wait — the websocket signal alone is sufficient.
    """
    import asyncio as _asyncio

    ticks = 0
    while True:
        if _ws_disconnected(websocket):
            return "websocket_disconnected"
        # Adapter reads take a SQLite write lock briefly, so check
        # them less often than the cheap websocket-state check.
        if adapter is not None and ticks % 4 == 0:
            try:
                attempt = adapter.get_attempt(attempt_id)
            except Exception:
                attempt = None
            if isinstance(attempt, dict) and attempt.get("state") == "hangup":
                return "adapter_hangup"
        ticks += 1
        await _asyncio.sleep(interval)


async def _wait_for_call_limit(max_minutes: int, sleep=None) -> str:
    import asyncio

    await (sleep or asyncio.sleep)(max_minutes * 60)
    return "max_minutes"


async def _wait_for_frontier_limit(ladder: BrainLadder, sleep=None) -> dict:
    import asyncio

    sleeper = sleep or asyncio.sleep
    while True:
        if ladder.stage() == "nathan":
            return {"action": "loop_in_nathan",
                    "reason": ladder.loop_in_reason or "frontier bound exhausted"}
        action = ladder.check_time_bound()
        if action["action"] == "loop_in_nathan":
            return action
        await sleeper(0.25)


async def _wait_for_terminal_reason(ladder: BrainLadder, sleep=None) -> str:
    import asyncio

    sleeper = sleep or asyncio.sleep
    while not ladder.terminal_reason:
        await sleeper(0.05)
    return ladder.terminal_reason


async def _speak_and_wait(tts, text_frame, activity: CallActivity,
                          cache: FixedPhraseCache, text: str,
                          sample_rate: int = 16000) -> None:
    import asyncio

    initial_cycles = activity.bot_cycles
    cached = cache.load(text)
    sample_rate = cache.sample_rate
    expected = (len(cached) / (sample_rate * 2)) if cached else max(1.0, len(text) / 15)
    await tts.queue_frame(text_frame)
    deadline = time.monotonic() + expected + 5.0
    while time.monotonic() < deadline:
        if activity.bot_cycles > initial_cycles:
            # Local transport completion is not a carrier playback receipt.
            # Conservative duration fallback; a future mark/clear-aware ack
            # tracker should replace this estimate after controlled live tests.
            await asyncio.sleep(expected + 0.25)
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("terminal phrase did not finish playing")


async def _wait_for_cost_gate(ladder: BrainLadder, ledger: CostLedger, started: float,
                              sleep=None, clock=time.time) -> str:
    import asyncio

    sleeper = sleep or asyncio.sleep
    while True:
        minutes = max(0.0, (clock() - started) / 60.0)
        cost = CallCost(
            minutes=minutes,
            stt_minutes=minutes,
            tts_chars=sum(len(turn["text"]) for turn in ladder.transcript
                          if turn["role"] == "assistant"),
        )
        outcome = ledger.project(cost)
        if outcome in ("test_budget_exceeded", "soft_cap", "monthly_cap"):
            return outcome
        await sleeper(0.25)


async def _wait_for_silence(activity: CallActivity, policy: SilencePolicy,
                            timeout_secs: float, on_reprompt, sleep=None,
                            clock=time.monotonic) -> str:
    import asyncio

    sleeper = sleep or asyncio.sleep
    while True:
        await sleeper(0.25)
        if activity.user_speaking or activity.bot_speaking:
            continue
        if clock() - activity.last_activity < timeout_secs:
            continue
        action = policy.on_timeout()
        activity.last_activity = clock()
        if action == "reprompt":
            await on_reprompt()
        else:
            return "silence_timeout"


async def _hangup_via_adapter(adapter, attempt_id: str, api_key: str,
                              reason: str) -> bool:
    import aiohttp

    adapter.request_stop(attempt_id=attempt_id, reason=reason)
    command = adapter.hangup_command(attempt_id)
    if command is None:
        return False
    endpoint = "https://api.telnyx.com" + command["path"]
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {api_key}"}
    timeout = aiohttp.ClientTimeout(total=8, connect=3, sock_read=5)
    async with aiohttp.ClientSession(trust_env=True, timeout=timeout) as session:
        async with session.post(endpoint, json=command["json"],
                                headers=headers) as response:
            if response.status != 200:
                logger.error("canonical Telnyx hangup failed status={}",
                             response.status)
                return False
            adapter.acknowledge_hangup(attempt_id, await response.json())
            return True


async def _finish_and_hangup(task, runner_task, adapter, attempt_id: str,
                             api_key: str, reason: str) -> bool:
    import asyncio

    adapter.request_stop(attempt_id=attempt_id, reason=reason)
    await task.queue_frame(EndFrame())
    try:
        await asyncio.wait_for(asyncio.shield(runner_task), timeout=10.0)
    except (asyncio.TimeoutError, Exception):
        pass
    attempt = adapter.get_attempt(attempt_id)
    if attempt.get("hangup_ack"):
        return True
    return await _hangup_via_adapter(adapter, attempt_id, api_key, reason)


async def handle_media_websocket(websocket, media_token: str, job: LiveCallJob,
                                 refs: LiveCredentialRefs, adapter,
                                 expected_route: tuple[str, str, str],
                                 log_path: str, models: dict | None = None,
                                 ledger: CostLedger | None = None):
    """Full media-side entry point for one Telnyx media websocket.

    Order is the safety property: parse handshake ONCE -> attach gate ->
    (only then) build pipeline -> speak disclosure -> run until the
    pipeline completes OR the callee hangs up -> write the
    transcript+cost record promptly. A rejected stream gets its
    websocket closed and no audio flows, no provider is called.
    Returns a record dict; never raises for a rejected stream.
    """
    from pipecat.runner.utils import parse_telephony_websocket
    from pipecat.workers.runner import WorkerRunner

    started = time.time()
    if models is None:
        from provider_config import load_preflight_receipt
        models = load_preflight_receipt()['models']
    if ledger is None:
        ledger = CostLedger(test_call=job.test_call)
    import asyncio
    from runtime_safety import timeout_setting
    transport_type, call_data = await asyncio.wait_for(
        parse_telephony_websocket(websocket),
        timeout=timeout_setting("AI_CALLING_HANDSHAKE_TIMEOUT_SECONDS", 5))
    if transport_type != "telnyx":
        await websocket.close(code=1011, reason="not a Telnyx media stream")
        return {"outcome": "rejected", "reason": f"transport_{transport_type}"}
    gate = MediaAttachGate(adapter)
    ok, reason = gate.authorize(
        attempt_id=media_token,
        call_control_id=call_data["call_id"],
        expected_route=expected_route)
    if not ok:
        await websocket.close(code=1011, reason=f"attach rejected: {reason}")
        return {"outcome": "rejected", "reason": reason,
                "call_control_id": call_data["call_id"]}
    if adapter.claim_media_job(media_token, now=time.time()) is None:
        await websocket.close(code=1011, reason="media job already claimed or expired")
        return {"outcome": "rejected", "reason": "media_job_unavailable",
                "call_control_id": call_data["call_id"]}
    try:
        task, ladder, services = build_live_pipeline(
            websocket, call_data, job, refs, models, adapter, media_token)
    except Exception as exc:
        outcome = f"error:{type(exc).__name__}"
        try:
            hangup_acknowledged = await _hangup_via_adapter(
                adapter, media_token,
                _resolve_ref(refs.telnyx_api_key_ref), outcome)
        except Exception:
            hangup_acknowledged = False
        minutes = max(0.0, (time.time() - started) / 60.0)
        cost = CallCost(minutes=minutes, stt_minutes=0.0)
        try:
            adapter.settle_media_job_quotas(media_token, {})
        except Exception:
            logger.exception("quota release failed for attempt {}", media_token)
        record = {
            "outcome": outcome,
            "call_control_id": call_data["call_id"],
            "stream_id": call_data["stream_id"],
            "models": models,
            "ladder_stage": "fast",
            "loop_in_reason": None,
            "transcript": [],
            "minutes": round(minutes, 3),
            "estimated_cost_cents": round(cost.total_cents(), 2),
            "estimated_cost_breakdown_cents": {
                name: round(value, 2)
                for name, value in cost.breakdown_cents().items()
            },
            "cost_gate": ledger.project(cost),
            "hangup_acknowledged": hangup_acknowledged,
            "hangup_required": not hangup_acknowledged,
        }
        ledger.record(log_path, record)
        return record
    # Disclosure first, before any other agent speech. Queued directly
    # at the TTS service (not through the context aggregators) so the
    # verbatim line is spoken exactly, not paraphrased by the LLM.
    # It MUST go as an AggregatedTextFrame (SENTENCE), not a plain
    # TextFrame: the TTS service routes plain text through
    # SimpleTextAggregator, which in SENTENCE mode buffers a sentence
    # ending in punctuation until non-whitespace lookahead arrives
    # (then NLTK) — a lone disclosure ending in "." therefore never
    # releases and the call stays silent with zero errors. An
    # AggregatedTextFrame bypasses the aggregator and goes straight
    # to synthesis. Text is byte-identical to job.opening.
    # Confirm on the first live run that the line is the first audio
    # the caller hears — it is test-list success criterion #1.
    import asyncio
    from pipecat.frames.frames import TTSSpeakFrame
    from runtime_safety import (
        cancel_tasks_bounded, timeout_setting, wait_pipeline_started,
    )
    outcome = "completed"
    end_time = None
    cancelled = False
    hangup_acknowledged = False
    cleanup_pending = 0
    background_tasks = []
    pipeline_started = asyncio.Event()
    runner_task = watcher_task = limit_task = frontier_task = None
    terminal_task = cost_task = silence_task = None

    def spawn(coro, name):
        owned = asyncio.create_task(coro, name=f"media:{media_token}:{name}")
        background_tasks.append(owned)
        return owned

    async def reprompt():
        text = "Are you still there?"
        await _speak_and_wait(
            services["tts"], TTSSpeakFrame(text),
            services["activity"], services["phrase_cache"], text)

    try:
        # All task creation and startup waits are inside the cleanup boundary.
        start_timeout = timeout_setting("AI_CALLING_START_TIMEOUT_SECONDS", 15)
        silence_seconds = timeout_setting("AI_CALLING_SILENCE_SECONDS", 12)

        @task.event_handler("on_pipeline_started")
        async def on_started(_worker, _frame):
            pipeline_started.set()

        @task.event_handler("on_pipeline_error")
        async def on_error(_worker, frame):
            if getattr(frame, 'fatal', False):
                ladder.terminal_reason = 'pipeline_error'

        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
        await runner.add_workers(task)
        runner_task = spawn(runner.run(), "runner")
        watcher_task = spawn(_wait_for_disconnect(websocket, adapter, media_token), "disconnect")
        limit_task = spawn(_wait_for_call_limit(job.max_minutes), "duration")
        frontier_task = spawn(_wait_for_frontier_limit(ladder), "frontier")
        terminal_task = spawn(_wait_for_terminal_reason(ladder), "terminal")
        cost_task = spawn(_wait_for_cost_gate(ladder, ledger, started), "cost")
        await wait_pipeline_started(pipeline_started, runner_task, watcher_task,
                                    timeout=start_timeout)
        logger.info("media-stage pipeline_started attempt={}", media_token)
        # Native standalone speech owns context creation AND turn completion.
        # A raw AggregatedTextFrame alone has no corresponding turn-end signal.
        await services["tts"].queue_frame(TTSSpeakFrame(job.opening))
        logger.info("media-stage disclosure_queued attempt={}", media_token)
        try:
            await wait_pipeline_started(
                services['serializer'].output_audio_ready, runner_task, watcher_task,
                timeout=timeout_setting('AI_CALLING_FIRST_AUDIO_TIMEOUT_SECONDS', 8))
        except TimeoutError as exc:
            raise TimeoutError('no outbound audio serialized before deadline') from exc
        silence_task = spawn(_wait_for_silence(
            services["activity"], services["silence_policy"],
            silence_seconds, reprompt), "silence")
        done, _pending = await asyncio.wait(
            {runner_task, watcher_task, limit_task, frontier_task, terminal_task,
             cost_task, silence_task},
            return_when=asyncio.FIRST_COMPLETED)
        if watcher_task in done and not watcher_task.cancelled():
            # Callee hung up. The runner never completes on its own:
            # FastAPIWebsocketTransport pushes no EndFrame on
            # disconnect, so without this branch the record would only
            # appear ~5 minutes later at idle-timeout teardown, priced
            # on pipeline lifetime instead of the call. Stop the
            # pipeline now and record promptly.
            disconnect_reason = watcher_task.result()
            outcome = "hangup" if disconnect_reason == "adapter_hangup" else "media_disconnected"
            end_time = time.time()
            if disconnect_reason != "adapter_hangup":
                # A media socket closing is NOT proof the billable call ended.
                hangup_acknowledged = await _hangup_via_adapter(
                    adapter, media_token, _resolve_ref(refs.telnyx_api_key_ref), outcome)
            await runner.cancel(outcome)
        elif limit_task in done:
            hangup_acknowledged = await _finish_and_hangup(
                task, runner_task, adapter, media_token,
                _resolve_ref(refs.telnyx_api_key_ref), "max minutes")
            outcome = "max_minutes"
            end_time = time.time()
        elif frontier_task in done:
            frontier_task.result()
            text = "Nathan will need to take this over. I'll end the call now."
            await _speak_and_wait(
                services["tts"], TTSSpeakFrame(text),
                services["activity"], services["phrase_cache"], text)
            hangup_acknowledged = await _finish_and_hangup(
                task, runner_task, adapter, media_token,
                _resolve_ref(refs.telnyx_api_key_ref), "frontier bound")
            outcome = "loop_in_requested"
            end_time = time.time()
        elif terminal_task in done:
            outcome = terminal_task.result()
            hangup_acknowledged = await _finish_and_hangup(
                task, runner_task, adapter, media_token,
                _resolve_ref(refs.telnyx_api_key_ref), outcome)
            end_time = time.time()
        elif cost_task in done:
            gate = cost_task.result()
            hangup_acknowledged = await _finish_and_hangup(
                task, runner_task, adapter, media_token,
                _resolve_ref(refs.telnyx_api_key_ref), gate)
            outcome = gate
            end_time = time.time()
        elif silence_task in done:
            silence_task.result()
            text = "I haven't heard anything, so I'll end the call now. Goodbye."
            await _speak_and_wait(
                services["tts"], TTSSpeakFrame(text),
                services["activity"], services["phrase_cache"], text)
            hangup_acknowledged = await _finish_and_hangup(
                task, runner_task, adapter, media_token,
                _resolve_ref(refs.telnyx_api_key_ref), "silence timeout")
            outcome = "silence_timeout"
            end_time = time.time()
        elif runner_task in done:
            if runner_task.cancelled():
                outcome = "cancelled"
                hangup_acknowledged = await _hangup_via_adapter(
                    adapter, media_token,
                    _resolve_ref(refs.telnyx_api_key_ref), outcome)
            elif runner_task.exception() is not None:
                exc = runner_task.exception()
                outcome = f"error:{type(exc).__name__}"
                hangup_acknowledged = await _hangup_via_adapter(
                    adapter, media_token,
                    _resolve_ref(refs.telnyx_api_key_ref), outcome)
            else:
                if ladder.terminal_reason:
                    outcome = ladder.terminal_reason
                elif ladder.stage() == "nathan" and ladder.loop_in_reason:
                    outcome = "loop_in_requested"
                else:
                    outcome = "completed"
            end_time = time.time()
        else:  # pragma: no cover - wait always returns a done task
            end_time = time.time()
    except asyncio.CancelledError:
        cancelled = True
        outcome = "cancelled"
        end_time = time.time()
        try:
            hangup_acknowledged = await asyncio.shield(
                _hangup_via_adapter(
                    adapter, media_token,
                    _resolve_ref(refs.telnyx_api_key_ref), outcome))
        except Exception:
            hangup_acknowledged = False
    except Exception as exc:  # noqa: BLE001 - record, don't swallow silently
        outcome = f"error:{type(exc).__name__}"
        end_time = time.time()
        try:
            hangup_acknowledged = await _hangup_via_adapter(
                adapter, media_token,
                _resolve_ref(refs.telnyx_api_key_ref), outcome)
        except Exception:
            hangup_acknowledged = False
    finally:
        try:
            cleanup_pending = await cancel_tasks_bounded(background_tasks, timeout=3.0)
        except asyncio.CancelledError:
            cancelled = True
            cleanup_pending = sum(not t.done() for t in background_tasks)
        except Exception:
            cleanup_pending = sum(not t.done() for t in background_tasks)
        try:
            await asyncio.wait_for(websocket.close(), timeout=1.0)
        except (asyncio.CancelledError, Exception):
            pass
        if end_time is None:
            end_time = time.time()
        # Price from actual call timing: end_time is the first observed
        # hangup/disconnect (watcher poll interval 0.25s), NOT pipeline
        # lifetime. The adapter attempt row carries no callee-hangup
        # timestamp (hangup_at records only parent-issued hangup
        # commands), so watcher time is the available call-end timing.
        minutes = max(0.0, (end_time - started) / 60.0)
        cost = CallCost(minutes=minutes, stt_minutes=minutes,
                        tts_chars=services['character_budget'].used)
        try:
            hangup_acknowledged = bool(
                hangup_acknowledged
                or adapter.get_attempt(media_token).get("hangup_ack"))
        except Exception:
            pass
        try:
            carrier_reconciliation = adapter.reconcile_call_record(media_token)
        except Exception:
            carrier_reconciliation = {"answered_event": False,
                                      "hangup_event": False}
        try:
            adapter.settle_media_job_quotas(
                media_token,
                {"groq_audio_seconds": minutes * 60,
                 "gemini_requests": sum(
                     1 for turn in ladder.transcript if turn["role"] == "assistant"),
                 "elevenlabs_characters": services["character_budget"].used})
        except Exception:
            logger.exception("quota settlement failed for attempt {}", media_token)
        record = {
            "outcome": outcome,
            "call_control_id": services["call_control_id"],
            "stream_id": services["stream_id"],
            "models": models,
            "ladder_stage": ladder.stage(),
            "loop_in_reason": ladder.loop_in_reason,
            "transcript": ladder.transcript,
            "transcript_scope": "user_transcripts_and_planned_guarded_replies_not_playback_confirmation",
            "billing_reconciled": False,
            "cost_scope": "partial_client_estimate_not_provider_invoice",
            "minutes": round(minutes, 3),
            "estimated_cost_cents": round(cost.total_cents(), 2),
            "estimated_cost_breakdown_cents": {
                name: round(value, 2)
                for name, value in cost.breakdown_cents().items()
            },
            "cost_gate": ledger.project(cost),
            "hangup_acknowledged": hangup_acknowledged,
            "hangup_required": not (hangup_acknowledged or carrier_reconciliation.get("hangup_event")),
            "pipeline_started": pipeline_started.is_set(),
            "cleanup_pending_tasks": cleanup_pending,
            "tts_characters": services["character_budget"].used,
            "media_payload_bytes": services["serializer"]._input_payload_bytes,
            "outbound_payload_bytes": services["serializer"]._output_payload_bytes,
            "outbound_media_frames": services["serializer"]._output_media_count,
            "audio_evidence_scope": "serialized_locally_not_carrier_playback",
            "media_sequence_gaps": services["serializer"]._sequence_gaps,
            "media_duplicate_or_old": services["serializer"]._duplicate_or_old,
            "carrier_reconciliation": carrier_reconciliation,
        }
        ledger.record(log_path, record)
    if cancelled:
        raise asyncio.CancelledError
    return record
