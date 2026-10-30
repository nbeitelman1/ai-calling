"""LLM failover regression tests — the Oct 30, 2026 retest-call
failure class.

The intelligence eval suite faked the LLM by replacing the whole
GoogleLLMService with a script-queue processor. That seam bypassed
the layer that actually failed live: pipecat's real service making
a real provider request that returns an error. These tests fake
ONE layer lower — google.genai.Client, the exact object pipecat's
create_client() builds — so the real GoogleLLMService, its real
error path, the real aggregators, ResponseGate and the failover
composition in build_live_pipeline all execute. The fake client
raises the same ServerError the provider returned live
(errors.py: "503 ... high demand") for the primary model and
answers for the fallback.

Covered:
- primary 503 -> the turn is answered by the fallback model, the
  failover is recorded on the call record, and the next turn goes
  straight to the fallback (sticky);
- every model 503 -> the error surfaces as it did before the fix
  (no reply), the session still ends with a clean record;
- a 400 client error (the request is at fault) -> NO failover;
- classification + mixin units, incl. never failing over after
  text has already streamed.
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import llm_failover as lf
import live_pipeline as live
from google.genai import errors, types
from pipecat.frames.frames import (TranscriptionFrame,
    VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame)
from starlette.websockets import WebSocket

from test_intelligence_eval import OfflineSTT, OfflineElevenLabs, tone

PRIMARY = "fake-primary"
FALLBACK = "fake-fallback"
FRONTIER = "fake-frontier"
FALLBACK_REPLY = "I can hear you just fine."


def _error_body(code, message, status):
    return {"error": {"code": code, "message": message, "status": status}}


def _chunk(text):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(
            content=types.Content(
                role="model", parts=[types.Part(text=text)]),
            finish_reason=types.FinishReason.STOP)],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=3, candidates_token_count=2,
            total_token_count=5))


class FakeModels:
    async def generate_content_stream(self, model=None, contents=None,
                                      config=None):
        FakeGenaiClient.calls.append(model)
        behavior = FakeGenaiClient.BEHAVIOR.get(model, "ok")
        if behavior == "503":
            raise errors.ServerError(
                503, _error_body(503, "This model is currently "
                                 "experiencing high demand.",
                                 "UNAVAILABLE"))
        if behavior == "400":
            raise errors.ClientError(
                400, _error_body(400, "Invalid request.",
                                 "INVALID_ARGUMENT"))

        async def gen():
            yield _chunk(FakeGenaiClient.REPLIES.get(
                model, "Fallback reply."))
        return gen()


class FakeGenaiClient:
    """Stands in for google.genai.Client at pipecat's seam."""
    BEHAVIOR: dict = {}
    REPLIES: dict = {}
    calls: list = []

    def __init__(self, **kwargs):
        self.aio = SimpleNamespace(models=FakeModels())


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


async def run_fake_session(tmp, job, user_turns):
    """Production media handler; real GoogleLLMService over a fake
    genai.Client; STT/TTS fakes as in the intelligence eval."""
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
           "AI_CALLING_PROFILE_DB": str(Path(tmp) / "profile.db"),
           "GEMINI_FAST_MODELS": f"{PRIMARY},{FALLBACK}",
           "GEMINI_FRONTIER_MODELS": FRONTIER}
    refs = live.LiveCredentialRefs(elevenlabs_voice_id="offline")
    cache = live.FixedPhraseCache(tmp, namespace="offline/offline/pcm_16000")
    cache.store(job.opening, tone())
    captured = {}
    original_build = live.build_live_pipeline

    def build(*args, **kwargs):
        result = original_build(*args, **kwargs)
        captured["worker"], captured["ladder"], captured["services"] = result
        return result

    with patch.dict(os.environ, env), \
            patch("pipecat.services.groq.stt.GroqSTTService", OfflineSTT), \
            patch("pipecat.services.elevenlabs.tts.ElevenLabsTTSService",
                  OfflineElevenLabs), \
            patch("google.genai.Client", FakeGenaiClient), \
            patch.object(live, "build_live_pipeline", side_effect=build), \
            patch.object(live, "_hangup_via_adapter",
                         new=AsyncMock(return_value=True)):
        running = asyncio.create_task(live.handle_media_websocket(
            websocket, "failover-eval", job, refs, Adapter(),
            ("conn", "+12025550100", "+12025550101"),
            str(Path(tmp) / "session.jsonl"),
            models={"fast": PRIMARY, "frontier": FRONTIER,
                    "stt": "offline"}))
        try:
            await asyncio.wait_for(audio_seen.wait(), 5)
            tap = captured["services"]["stt"]
            ladder = captured["ladder"]
            for text in user_turns:
                if running.done():
                    break
                before = len([t for t in ladder.transcript
                              if t["role"] == "assistant"])
                await tap.push_frame(VADUserStartedSpeakingFrame())
                await tap.push_frame(TranscriptionFrame(
                    text, "offline-user", str(time.time()), finalized=True))
                await tap.push_frame(VADUserStoppedSpeakingFrame())
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not running.done():
                    assistants = [t for t in ladder.transcript
                                  if t["role"] == "assistant"]
                    if len(assistants) > before:
                        break
                    await asyncio.sleep(0.02)
                await asyncio.sleep(0.15)
            if not running.done():
                await incoming.put({"type": "websocket.disconnect",
                                    "code": 1000})
            return await asyncio.wait_for(asyncio.shield(running), 10)
        finally:
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)


def make_job():
    return live.LiveCallJob(
        to="+12025550101", business="Nathan (test call)",
        objective="Have a short friendly conversation",
        max_minutes=1)


class TestFailoverIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeGenaiClient.calls = []
        FakeGenaiClient.BEHAVIOR = {}
        FakeGenaiClient.REPLIES = {FALLBACK: FALLBACK_REPLY}

    async def test_primary_503_fails_over_and_sticks(self):
        FakeGenaiClient.BEHAVIOR = {PRIMARY: "503"}
        with tempfile.TemporaryDirectory() as tmp:
            record = await run_fake_session(
                tmp, make_job(),
                ["Hi, how's it going?", "I'm still here. Can you hear me?"])
            spoken = [t["text"] for t in record["transcript"]
                      if t["role"] == "assistant"]
            self.assertEqual(spoken, [FALLBACK_REPLY, FALLBACK_REPLY])
            # Turn 1 walked primary -> fallback; turn 2 went straight
            # to the fallback (sticky), never re-asking the dead model.
            self.assertEqual(FakeGenaiClient.calls,
                             [PRIMARY, FALLBACK, FALLBACK])
            failover = record["llm_failover"]
            self.assertEqual(failover["events"], 1)
            self.assertEqual(failover["model_used"], FALLBACK)
            self.assertEqual(failover["chain"],
                             [PRIMARY, FALLBACK, FRONTIER])
            self.assertEqual(failover["switches"][0]["from"], PRIMARY)
            print("FAILOVER_EVAL " + json.dumps({
                "calls": FakeGenaiClient.calls, "spoken": spoken,
                "failover": failover}))

    async def test_all_models_503_surfaces_error_no_reply(self):
        FakeGenaiClient.BEHAVIOR = {PRIMARY: "503", FALLBACK: "503",
                                    FRONTIER: "503"}
        with tempfile.TemporaryDirectory() as tmp:
            record = await run_fake_session(
                tmp, make_job(), ["Hi, how's it going?"])
            spoken = [t for t in record["transcript"]
                      if t["role"] == "assistant"]
            self.assertEqual(spoken, [])
            self.assertEqual(FakeGenaiClient.calls,
                             [PRIMARY, FALLBACK, FRONTIER])
            # Two switches happened; the third failure surfaced.
            self.assertEqual(record["llm_failover"]["events"], 2)

    async def test_client_error_400_no_failover(self):
        FakeGenaiClient.BEHAVIOR = {PRIMARY: "400"}
        with tempfile.TemporaryDirectory() as tmp:
            record = await run_fake_session(
                tmp, make_job(), ["Hi, how's it going?"])
            spoken = [t for t in record["transcript"]
                      if t["role"] == "assistant"]
            self.assertEqual(spoken, [])
            self.assertEqual(FakeGenaiClient.calls, [PRIMARY])
            self.assertEqual(record["llm_failover"]["events"], 0)


class TestClassification(unittest.TestCase):
    def test_failover_worthy_matrix(self):
        self.assertTrue(lf.is_failover_worthy(errors.ServerError(
            503, _error_body(503, "high demand", "UNAVAILABLE"))))
        self.assertTrue(lf.is_failover_worthy(errors.ServerError(
            500, _error_body(500, "internal", "INTERNAL"))))
        self.assertTrue(lf.is_failover_worthy(errors.ClientError(
            429, _error_body(429, "rate limited", "RESOURCE_EXHAUSTED"))))
        self.assertTrue(lf.is_failover_worthy(errors.ClientError(
            404, _error_body(404, "not found", "NOT_FOUND"))))
        self.assertTrue(lf.is_failover_worthy(TimeoutError("slow")))
        self.assertFalse(lf.is_failover_worthy(errors.ClientError(
            400, _error_body(400, "bad request", "INVALID_ARGUMENT"))))
        self.assertFalse(lf.is_failover_worthy(errors.ClientError(
            403, _error_body(403, "forbidden", "PERMISSION_DENIED"))))
        self.assertFalse(lf.is_failover_worthy(ValueError("bug")))

    def test_model_chain_order_and_dedup(self):
        env = {"GEMINI_FAST_MODELS": "a,b,a",
               "GEMINI_FRONTIER_MODELS": "c,b"}
        self.assertEqual(lf.model_chain("a", "c", env), ["a", "b", "c"])
        self.assertEqual(lf.model_chain("x", "y", {}), ["x", "y"])
        self.assertEqual(lf.model_chain("x", "x", {}), ["x"])


class _StubSettings:
    def __init__(self, model):
        self.model = model


class _StubBase:
    """Minimal _stream_response seam keyed by the current model."""

    def __init__(self, behavior=None):
        self._settings = _StubSettings("m1")
        self.behavior = behavior or {}

    async def _stream_response(self, context):
        model = self._settings.model
        action = self.behavior[model]
        if action == "fail":
            raise errors.ServerError(
                503, _error_body(503, "high demand", "UNAVAILABLE"))
        if action == "yield_then_fail":
            yield _chunk("partial")
            raise errors.ServerError(
                503, _error_body(503, "high demand", "UNAVAILABLE"))
        yield _chunk(f"reply from {model}")


class _StubFailoverService(lf.LLMFailoverMixin, _StubBase):
    def __init__(self, behavior, pool):
        # MRO: mixin __init__ -> _StubBase.__init__ (settings), then
        # the mixin reads _settings and installs the pool.
        super().__init__(model_pool=pool)
        self.behavior = behavior


async def _collect(service):
    return [chunk async for chunk in service._stream_response(None)]


class TestMixinUnits(unittest.IsolatedAsyncioTestCase):
    async def test_failover_before_first_chunk(self):
        service = _StubFailoverService(
            {"m1": "fail", "m2": "ok"}, ["m1", "m2"])
        chunks = await _collect(service)
        self.assertEqual(chunks[0].candidates[0].content.parts[0].text,
                         "reply from m2")
        self.assertEqual(service.failover_events, 1)
        self.assertEqual(service.active_model, "m2")

    async def test_no_failover_after_first_chunk(self):
        service = _StubFailoverService(
            {"m1": "yield_then_fail", "m2": "ok"}, ["m1", "m2"])
        with self.assertRaises(errors.ServerError):
            await _collect(service)
        self.assertEqual(service.failover_events, 0)


if __name__ == "__main__":
    unittest.main()
