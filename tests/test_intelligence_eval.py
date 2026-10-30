"""Intelligence-pass evaluation: scripted synthetic conversations.

Runs the PRODUCTION media handler with fake providers (the pattern
from test_offline_media_session.py) and a script-queue fake LLM,
proving the behaviors the Oct 30 live call lacked:

(a) the repetition guard fires, substitutes progression, then wraps
    up and ends the call instead of asking one question forever;
(b) the hand-back line is spoken exactly once and no new inference
    starts after it — the call ends as loop_in_requested;
(c) memory continuity: a second call to the same counterparty is
    briefed with facts and the open commitment written back from
    the first call;
(d) post-call writeback files typed items (see test_control_plane
    for the typer itself; here the full record path is asserted).
"""
import asyncio
import base64
import json
import math
import os
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import call_controller as cc
import live_pipeline as live
import profile_store as ps
from pipecat.frames.frames import (InputAudioRawFrame, TranscriptionFrame,
    LLMContextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
    LLMTextFrame, TTSStartedFrame, TTSStoppedFrame, TTSAudioRawFrame,
    VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.services.groq.stt import GroqSTTService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from starlette.websockets import WebSocket


def tone(rate=16000, duration=.25):
    return b''.join(struct.pack('<h', int(5000 * math.sin(2 * math.pi * 440 * i / rate)))
                    for i in range(int(rate * duration)))


class OfflineSTT(FrameProcessor):
    Settings = GroqSTTService.Settings

    def __init__(self, **kwargs):
        super().__init__()
        self.audio_frames = 0

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame) \
                and direction == FrameDirection.DOWNSTREAM:
            self.audio_frames += 1
        await self.push_frame(frame, direction)


class ScriptedLLM(FrameProcessor):
    """Replies from a class-level script queue; captures prompts."""
    Settings = GoogleLLMService.Settings
    SCRIPT: list[str] = []
    INSTANCES: list = []

    def __init__(self, **kwargs):
        super().__init__()
        self.context_requests = 0
        self.prompts: list[str] = []
        ScriptedLLM.INSTANCES.append(self)

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame) \
                and direction == FrameDirection.DOWNSTREAM:
            self.context_requests += 1
            messages = frame.context.messages
            if messages:
                self.prompts.append(messages[0].get("content", ""))
            reply = ScriptedLLM.SCRIPT.pop(0) if ScriptedLLM.SCRIPT \
                else "Okay."
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame(reply))
            await self.push_frame(LLMFullResponseEndFrame())
        else:
            await self.push_frame(frame, direction)


class OfflineElevenLabs(ElevenLabsTTSService):
    async def _connect(self):
        pass

    async def _disconnect(self):
        pass

    async def run_tts(self, text, context_id):
        if not self.audio_context_available(context_id):
            await self.create_audio_context(context_id)
            yield TTSStartedFrame(context_id=context_id)
        yield TTSAudioRawFrame(tone(), self.sample_rate, 1,
                               context_id=context_id)
        yield TTSStoppedFrame(context_id=context_id)


class Adapter:
    def __init__(self):
        self.claimed = False
        self.settled = None
        self.stopped = []

    def get_attempt(self, _):
        return {"state": "answered", "control_id": "control-offline",
                "hangup_ack": False,
                "payload_json": json.dumps(
                    {"connection_id": "conn", "from": "+12025550100",
                     "to": "+12025550101"})}

    def claim_media_job(self, _, now):
        if self.claimed:
            return None
        self.claimed = True
        return {"registered": True}

    def settle_media_job_quotas(self, _, actual):
        self.settled = actual

    def reconcile_call_record(self, _):
        return {"answered_event": True, "hangup_event": False,
                "hangup_source": None}

    def request_stop(self, attempt_id=None, reason=None):
        self.stopped.append((attempt_id, reason))


async def run_session(test, tmp, job, script, user_turns, attempt="eval"):
    """One full synthetic call. Returns (record, captured dict)."""
    ScriptedLLM.SCRIPT = list(script)
    ScriptedLLM.INSTANCES = []
    incoming = asyncio.Queue()
    outputs = []
    audio_seen = asyncio.Event()

    async def send(message):
        outputs.append(message)
        if message["type"] == "websocket.send":
            payload = json.loads(message.get("text", "{}"))
            if payload.get("event") == "media":
                audio_seen.set()

    websocket = WebSocket(
        {"type": "websocket", "path": "/telnyx/media/offline",
         "headers": [], "query_string": b""}, incoming.get, send)
    await incoming.put({"type": "websocket.connect"})
    await websocket.accept()
    for message in [
            {"event": "connected", "version": "1.0.0"},
            {"event": "start", "sequence_number": "1",
             "stream_id": "stream-offline",
             "start": {"call_control_id": "control-offline",
                       "media_format": {"encoding": "PCMU",
                                        "sample_rate": 8000,
                                        "channels": 1}}}]:
        await incoming.put({"type": "websocket.receive",
                            "text": json.dumps(message)})
    env = {"GROQ_API_KEY": "offline", "GEMINI_API_KEY": "offline",
           "ELEVENLABS_API_KEY": "offline", "TELNYX_API_KEY": "offline",
           "AI_CALLING_PHRASE_CACHE": tmp, "ELEVENLABS_TTS_MODELS": "offline",
           "ELEVENLABS_SAMPLE_RATE": "16000",
           "AI_CALLING_START_TIMEOUT_SECONDS": "5",
           "AI_CALLING_SILENCE_SECONDS": "60",
           "AI_CALLING_PROFILE_DB": str(Path(tmp) / "profile.db")}
    refs = live.LiveCredentialRefs(elevenlabs_voice_id="offline")
    cache = live.FixedPhraseCache(tmp, namespace="offline/offline/pcm_16000")
    cache.store(job.opening, tone())
    adapter = Adapter()
    captured = {}
    original_build = live.build_live_pipeline

    def build(*args, **kwargs):
        result = original_build(*args, **kwargs)
        captured["worker"], captured["ladder"], captured["services"] = result
        return result

    with patch.dict(os.environ, env), \
            patch("pipecat.services.groq.stt.GroqSTTService", OfflineSTT), \
            patch("pipecat.services.google.llm.GoogleLLMService", ScriptedLLM), \
            patch("pipecat.services.elevenlabs.tts.ElevenLabsTTSService",
                  OfflineElevenLabs), \
            patch.object(live, "build_live_pipeline", side_effect=build), \
            patch.object(live, "_hangup_via_adapter",
                         new=AsyncMock(return_value=True)):
        running = asyncio.create_task(live.handle_media_websocket(
            websocket, attempt, job, refs, adapter,
            ("conn", "+12025550100", "+12025550101"),
            str(Path(tmp) / "session.jsonl"),
            models={"fast": "offline", "frontier": "offline",
                    "stt": "offline"}))
        try:
            await asyncio.wait_for(audio_seen.wait(), 5)
            llm = captured["services"]["llm"]
            captured["llm"] = llm
            for text in user_turns:
                if running.done():
                    break
                before = llm.context_requests
                tap = captured["services"]["stt"]
                await tap.push_frame(VADUserStartedSpeakingFrame())
                await tap.push_frame(TranscriptionFrame(
                    text, "offline-user", str(time.time()), finalized=True))
                await tap.push_frame(VADUserStoppedSpeakingFrame())
                deadline = time.monotonic() + 5
                while llm.context_requests <= before \
                        and time.monotonic() < deadline \
                        and not running.done():
                    await asyncio.sleep(0.02)
                # Let the gate flush the reply into the transcript.
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not running.done():
                    assistants = [t for t in captured["ladder"].transcript
                                  if t["role"] == "assistant"]
                    if len(assistants) >= llm.context_requests:
                        break
                    await asyncio.sleep(0.02)
                await asyncio.sleep(0.15)
            if not running.done():
                await incoming.put({"type": "websocket.disconnect",
                                    "code": 1000})
            record = await asyncio.wait_for(asyncio.shield(running), 10)
            return record, captured
        finally:
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)


class TestRepetitionGuardEval(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_question_progresses_then_wraps_up(self):
        repeat = "What kind of work do you do?"
        with tempfile.TemporaryDirectory() as tmp:
            job = live.LiveCallJob(
                to="+12025550101", business="Acme Dental",
                objective="Gather information about the practice",
                playbook="information_gathering",
                counterparty="Acme Dental", max_minutes=1)
            record, captured = await run_session(
                self, tmp, job,
                script=[repeat, repeat, repeat, repeat],
                user_turns=["Hello.", "I run a bakery.",
                            "We bake bread.", "Anything else?"])
            self.assertEqual(record["outcome"], "repetition_guard")
            spoken = [t["text"] for t in record["transcript"]
                      if t["role"] == "assistant"]
            # The repeated question was actually spoken ONCE; later
            # attempts were substituted (and carry the note).
            self.assertEqual(spoken.count(repeat), 1)
            substituted = [t for t in record["transcript"]
                           if t.get("substituted_for")]
            self.assertGreaterEqual(len(substituted), 2)
            self.assertTrue(any(
                live.WRAP_UP_LINE in t["text"] for t in substituted))
            state = record["conversation_state"]
            self.assertGreaterEqual(state["substitutions"], 2)
            self.assertNotIn(live.HAND_BACK_LINE, " ".join(spoken))
            print("REPETITION_EVAL " + json.dumps({
                "outcome": record["outcome"],
                "spoken": spoken,
                "substitutions": state["substitutions"]}))


class TestTerminalDisciplineEval(unittest.IsolatedAsyncioTestCase):
    async def test_hand_back_line_once_and_no_further_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = live.LiveCallJob(
                to="+12025550101", business="Acme Dental",
                objective="controlled live qualification",
                max_minutes=1)
            record, captured = await run_session(
                self, tmp, job,
                script=["Sure, I can help with that.",
                        live.HAND_BACK_LINE,
                        "This must never be spoken."],
                user_turns=["Hello, I have a question.",
                            "Let me talk to a human instead.",
                            "Are you still there?"])
            self.assertEqual(record["outcome"], "loop_in_requested")
            spoken = [t["text"] for t in record["transcript"]
                      if t["role"] == "assistant"]
            self.assertEqual(
                sum(live.HAND_BACK_LINE in t for t in spoken), 1)
            self.assertNotIn("This must never be spoken.",
                             " ".join(spoken))
            # Exactly two inferences happened (turns 1 and 2); the
            # post-terminal user turn started none.
            self.assertEqual(captured["llm"].context_requests, 2)
            print("TERMINAL_EVAL " + json.dumps({
                "outcome": record["outcome"],
                "assistant_turns": spoken,
                "llm_requests": captured["llm"].context_requests}))


class TestMemoryContinuityEval(unittest.IsolatedAsyncioTestCase):
    async def test_second_call_brief_uses_first_call_writeback(self):
        with tempfile.TemporaryDirectory() as tmp:
            job1 = live.LiveCallJob(
                to="+12025550101", business="Acme Dental",
                objective="Gather information about the practice",
                playbook="information_gathering",
                counterparty="Acme Dental", max_minutes=1)
            record1, _ = await run_session(
                self, tmp, job1,
                script=["Thanks for letting me know.",
                        "Perfect, I have that noted."],
                user_turns=[
                    "Our office hours are Monday to Friday, "
                    "nine to five.",
                    "We'll fax the referral by Friday."],
                attempt="eval-call-1")
            writeback = record1["memory_writeback"]
            self.assertNotIn("error", writeback)
            self.assertGreaterEqual(writeback["stored"]["facts"], 1)
            self.assertGreaterEqual(writeback["stored"]["commitments"], 1)

            job2 = live.LiveCallJob(
                to="+12025550101", business="Acme Dental",
                objective="Follow up on the status of the referral",
                playbook="follow_up_status",
                counterparty="Acme Dental", max_minutes=1)
            record2, captured2 = await run_session(
                self, tmp, job2,
                script=["Following up on the referral."],
                user_turns=["Hello, yes?"],
                attempt="eval-call-2")
            system_prompt = captured2["llm"].prompts[0]
            self.assertIn("office_hours: Monday to Friday, nine to five",
                          system_prompt)
            self.assertIn("fax the referral", system_prompt)
            print("CONTINUITY_EVAL " + json.dumps({
                "call1_writeback": writeback,
                "call2_brief_has_fact": True,
                "call2_brief_has_commitment": True}))


class TestWritebackUnit(unittest.TestCase):
    def test_writeback_files_episode_facts_and_commitments(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ps.ProfileStore(Path(tmp) / "profile.db")
            job = live.LiveCallJob(
                to="+12025550101", business="Acme Dental",
                objective="Gather information",
                counterparty="Acme Dental")
            record = {
                "outcome": "hangup", "attempt_id": "unit-1",
                "transcript": [
                    {"role": "user",
                     "text": "Our office hours are Monday to Friday, "
                             "nine to five."},
                    {"role": "user",
                     "text": "We'll fax the referral by Friday."},
                ]}
            summary = cc.writeback(job, record, store,
                                   episode_ref="unit-1")
            self.assertNotIn("error", summary)
            facts = {f["predicate"]: f["value"]
                     for f in store.current_facts("Acme Dental")}
            self.assertEqual(facts["office_hours"],
                             "Monday to Friday, nine to five")
            opens = store.open_commitments("Acme Dental")
            self.assertEqual(len(opens), 1)
            self.assertEqual(opens[0]["due_text"], "by Friday")
            store.close()


if __name__ == "__main__":
    unittest.main()
