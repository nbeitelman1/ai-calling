"""Offline tests for the live voice pipeline body (Oct 29, 2026).

No network, no provider calls, no dialling, no key values. Everything
runs against fakes: a fake canonical adapter, a fake media socket, a
fake clock. Provider service constructors are exercised only with
dummy keys and are never started (starting one would open a websocket
to a provider).
"""
import base64
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/server")

from telnyx_media import (  # noqa: E402
    FakeMediaSocket,
    MediaAttachGate,
    TelnyxMediaSession,
    pcm16_to_pcmu,
    pcmu_to_pcm16,
    streaming_start_request,
)
from model_select import (  # noqa: E402
    CallCost,
    CostLedger,
    parse_pool,
    resolve_model,
    sanitize_outgoing,
    redact_record,
)
from voice_pipeline import EscalationState  # noqa: E402
from live_pipeline import (  # noqa: E402
    CallActivity,
    BrainLadder,
    FixedPhraseCache,
    InputAudioRechunker,
    LiveCallJob,
    SilencePolicy,
    TTSCharacterBudget,
    TranscriptTap,
    build_system_prompt,
    limit_spoken_response,
)

ATTEMPT_ID = "11111111-2222-3333-4444-555555555555"
CONTROL_ID = "v2:control-abc"
ROUTE = ("3081546725312169326", "+13214978649", "+13213677796")


class FakeAdapter:
    """Minimal stand-in for TelnyxAdapter.get_attempt()."""

    def __init__(self, state="initiated", control_id=CONTROL_ID, route=ROUTE):
        self._row = {
            "attempt_id": ATTEMPT_ID,
            "state": state,
            "control_id": control_id,
            "payload_json": json.dumps({
                "connection_id": route[0], "from": route[1], "to": route[2],
                "client_state": "e2F0dGVtcHRfaWQ6e30=", "command_id": ATTEMPT_ID}),
        }

    def get_attempt(self, attempt_id):
        if attempt_id != ATTEMPT_ID:
            raise KeyError("unknown attempt")
        return dict(self._row)


class TestCodec(unittest.TestCase):
    def test_silence_round_trips(self):
        self.assertEqual(pcmu_to_pcm16(b"\xff"), b"\x00\x00")

    def test_encode_decode_is_identity_except_pm_zero(self):
        # Mu-law has two zeros (0xFF and 0x7F both decode to 0); every
        # other byte must survive decode->encode exactly.
        bad = [b for b in range(256)
               if pcm16_to_pcmu(pcmu_to_pcm16(bytes([b]))) != bytes([b])]
        self.assertEqual(bad, [0x7F])

    def test_20ms_frame_size(self):
        self.assertEqual(len(pcmu_to_pcm16(bytes(160))), 320)

    def test_odd_pcm_rejected(self):
        with self.assertRaises(ValueError):
            pcm16_to_pcmu(b"\x00")


class TestStreamingStart(unittest.TestCase):
    def test_request_shape(self):
        req = streaming_start_request("wss://host.example/telnyx/media/" + ATTEMPT_ID)
        self.assertEqual(req["method"], "POST")
        self.assertIn("streaming_start", req["path"])
        self.assertEqual(req["json"]["stream_track"], "inbound_track")
        self.assertEqual(req["json"]["stream_bidirectional_mode"], "rtp")
        self.assertEqual(req["json"]["stream_bidirectional_codec"], "PCMU")

    def test_non_wss_rejected(self):
        with self.assertRaises(ValueError):
            streaming_start_request("https://host.example/x")


class TestAttachGate(unittest.TestCase):
    def test_happy_path(self):
        gate = MediaAttachGate(FakeAdapter())
        self.assertEqual(gate.authorize(
            attempt_id=ATTEMPT_ID, call_control_id=CONTROL_ID,
            expected_route=ROUTE), (True, "attached"))

    def test_unknown_attempt_rejected(self):
        gate = MediaAttachGate(FakeAdapter())
        ok, reason = gate.authorize(
            attempt_id="99999999-2222-3333-4444-555555555555",
            call_control_id=CONTROL_ID, expected_route=ROUTE)
        self.assertFalse(ok)
        self.assertEqual(reason, "unknown_attempt")

    def test_prepared_attempt_rejected(self):
        gate = MediaAttachGate(FakeAdapter(state="prepared", control_id=""))
        ok, reason = gate.authorize(
            attempt_id=ATTEMPT_ID, call_control_id=CONTROL_ID,
            expected_route=ROUTE)
        self.assertFalse(ok)
        self.assertEqual(reason, "unclaimed_attempt")

    def test_unbound_attempt_rejected(self):
        gate = MediaAttachGate(FakeAdapter(state="submitted", control_id=""))
        ok, reason = gate.authorize(
            attempt_id=ATTEMPT_ID, call_control_id=CONTROL_ID,
            expected_route=ROUTE)
        self.assertFalse(ok)
        self.assertEqual(reason, "unbound_attempt")

    def test_control_mismatch_quarantined(self):
        gate = MediaAttachGate(FakeAdapter())
        ok, reason = gate.authorize(
            attempt_id=ATTEMPT_ID, call_control_id="v2:control-other",
            expected_route=ROUTE)
        self.assertFalse(ok)
        self.assertEqual(reason, "control_mismatch")

    def test_route_mismatch_quarantined(self):
        gate = MediaAttachGate(FakeAdapter())
        ok, reason = gate.authorize(
            attempt_id=ATTEMPT_ID, call_control_id=CONTROL_ID,
            expected_route=("other-conn", "+13214978649", "+13213677796"))
        self.assertFalse(ok)
        self.assertEqual(reason, "route_mismatch")

    def test_session_id_alone_cannot_attach(self):
        # The gate takes no session id at all: there is no parameter to
        # smuggle one through. A caller holding only a session id has
        # nothing to call authorize() with except a guessed attempt id,
        # which fails as unknown_attempt.
        gate = MediaAttachGate(FakeAdapter())
        ok, _ = gate.authorize(
            attempt_id="v2:session-only-not-an-attempt",
            call_control_id=CONTROL_ID, expected_route=ROUTE)
        self.assertFalse(ok)


class TestMediaSession(unittest.TestCase):
    def _started(self):
        session = TelnyxMediaSession(
            attempt_id=ATTEMPT_ID, call_control_id=CONTROL_ID)
        session.handle_message({"event": "streaming_started",
                                "stream_id": "stream-1",
                                "call_control_id": CONTROL_ID})
        return session

    def test_media_before_start_dropped(self):
        session = TelnyxMediaSession(
            attempt_id=ATTEMPT_ID, call_control_id=CONTROL_ID)
        payload = base64.b64encode(b"\xff" * 160).decode()
        self.assertEqual(session.handle_message(
            {"event": "media", "media": {"payload": payload,
                                         "track": "inbound_track"}}),
            "dropped_before_start")
        self.assertEqual(session.drain_inbound_pcm(), b"")

    def test_media_queued_and_decoded(self):
        session = self._started()
        payload = base64.b64encode(b"\xff" * 160).decode()
        self.assertEqual(session.handle_message(
            {"event": "media", "media": {"payload": payload,
                                         "track": "inbound_track"}}),
            "media_queued")
        self.assertEqual(len(session.drain_inbound_pcm()), 320)

    def test_live_inbound_track_name_is_queued(self):
        session = self._started()
        payload = base64.b64encode(b"\xff" * 160).decode()
        self.assertEqual(session.handle_message(
            {"event": "media", "media": {"payload": payload,
                                           "track": "inbound"}}),
            "media_queued")

    def test_outbound_track_names_are_dropped(self):
        session = self._started()
        payload = base64.b64encode(b"\xff" * 160).decode()
        for track in ("outbound", "outbound_track"):
            self.assertEqual(session.handle_message(
                {"event": "media", "media": {"payload": payload,
                                               "track": track}}),
                "ignored_non_inbound_track")
        self.assertEqual(session.drain_inbound_pcm(), b"")

    def test_stop_absorbs_later_media(self):
        session = self._started()
        session.handle_message({"event": "streaming_stopped"})
        payload = base64.b64encode(b"\xff" * 160).decode()
        self.assertEqual(session.handle_message(
            {"event": "media", "media": {"payload": payload}}),
            "dropped_after_stop")
        self.assertEqual(session.state, "stopped")

    def test_state_never_regresses(self):
        session = self._started()
        session.handle_message({"event": "streaming_stopped"})
        session.handle_message({"event": "streaming_started",
                                "stream_id": "stream-2",
                                "call_control_id": CONTROL_ID})
        self.assertEqual(session.state, "stopped")

    def test_barge_in_flag(self):
        session = self._started()
        session.agent_speaking = True
        session.note_user_speech()
        self.assertTrue(session.take_interruption())
        self.assertFalse(session.take_interruption())

    def test_outbound_encode_round_trip(self):
        session = self._started()
        pcm = b"\x00\x00" * 160
        msg = session.encode_outbound_media(pcm)
        self.assertEqual(msg["event"], "media")
        decoded = pcmu_to_pcm16(base64.b64decode(msg["media"]["payload"]))
        self.assertEqual(decoded, pcm)

    def test_fake_socket_round_trip(self):
        sock = FakeMediaSocket()
        sock.feed({"event": "streaming_started", "stream_id": "s",
                   "call_control_id": CONTROL_ID})
        session = TelnyxMediaSession(
            attempt_id=ATTEMPT_ID, call_control_id=CONTROL_ID)
        for message in sock.send_queue:
            session.handle_message(message)
        self.assertEqual(session.state, "started")


class TestProductionTrackFilter(unittest.IsolatedAsyncioTestCase):
    async def test_outbound_wire_track_drops_before_base_serializer(self):
        from live_pipeline import _CountingTelnyxSerializer

        serializer = _CountingTelnyxSerializer(
            stream_id="stream", outbound_encoding="PCMU",
            inbound_encoding="PCMU", call_control_id=CONTROL_ID,
            api_key="dummy")
        result = await serializer.deserialize(json.dumps({
            "event": "media",
            "media": {"track": "outbound", "payload": "not-base64"},
        }))
        self.assertIsNone(result)
        self.assertEqual(serializer._dropped_count, 1)

    async def test_malformed_media_drops_before_base_serializer(self):
        from live_pipeline import _CountingTelnyxSerializer

        serializer = _CountingTelnyxSerializer(
            stream_id="stream", outbound_encoding="PCMU",
            inbound_encoding="PCMU", call_control_id=CONTROL_ID,
            api_key="dummy")
        result = await serializer.deserialize(json.dumps({"event": "media"}))
        self.assertIsNone(result)
        self.assertEqual(serializer._dropped_count, 1)

    async def test_duplicate_and_gap_sequences_are_accounted(self):
        from live_pipeline import _CountingTelnyxSerializer

        serializer = _CountingTelnyxSerializer(
            stream_id="stream", outbound_encoding="PCMU",
            inbound_encoding="PCMU", call_control_id=CONTROL_ID,
            api_key="dummy")
        for sequence in (1, 3, 3):
            await serializer.deserialize(json.dumps({
                "event": "media", "sequence_number": sequence,
                "media": {"track": "outbound", "payload": "x"},
            }))
        self.assertEqual(serializer._sequence_gaps, 1)
        self.assertEqual(serializer._duplicate_or_old, 1)


class TestModelSelect(unittest.TestCase):
    def test_pool_intersection(self):
        self.assertEqual(
            resolve_model("fast", ["model-a", "model-b"], ["model-b", "model-c"]),
            "model-b")

    def test_empty_pool_fails_closed(self):
        with self.assertRaises(ValueError):
            resolve_model("fast", [])

    def test_no_offered_model_fails(self):
        with self.assertRaises(ValueError):
            resolve_model("fast", ["model-a"], ["model-z"])

    def test_no_hard_pinned_model_in_resolver(self):
        # The resolver must not contain a baked-in model name: it only
        # ever returns pool entries.
        import inspect
        import model_select
        source = inspect.getsource(model_select.resolve_model)
        self.assertNotIn("gpt-", source)
        self.assertNotIn("whisper", source)

    def test_parse_pool(self):
        self.assertEqual(parse_pool("a, b,,a "), ["a", "b"])


class TestCostLedger(unittest.TestCase):
    def test_test_budget(self):
        ledger = CostLedger(test_call=True)
        ok_cost = CallCost(minutes=5, stt_minutes=5, tts_chars=1900)
        self.assertEqual(ledger.project(ok_cost), "ok")
        over_cost = CallCost(minutes=5, stt_minutes=5, tts_chars=30000)
        self.assertEqual(ledger.project(over_cost), "test_budget_exceeded")

    def test_soft_cap(self):
        ledger = CostLedger(test_call=False)
        cost = CallCost(minutes=10, tts_chars=150000)
        self.assertEqual(ledger.project(cost), "soft_cap")

    def test_monthly_cap(self):
        ledger = CostLedger(spent_this_month_cents=1999.0)
        cost = CallCost(minutes=5, stt_minutes=5)
        self.assertEqual(ledger.project(cost), "monthly_cap")

    def test_record_is_jsonl(self):
        ledger = CostLedger(test_call=True)
        with tempfile.NamedTemporaryFile("r+", delete=False, suffix=".jsonl") as fh:
            path = fh.name
        ledger.record(path, {"outcome": "completed", "transcript": []})
        with open(path) as fh:
            entry = json.loads(fh.readline())
        self.assertEqual(entry["outcome"], "completed")
        self.assertIn("recorded_at", entry)

    def test_persisted_transcript_redacts_sensitive_number_shapes(self):
        record = redact_record({"transcript": [
            {"role": "user", "text": "SSN 123-45-6789"}]})
        self.assertEqual(record["transcript"][0]["text"], "SSN [REDACTED]")

    def test_persisted_transcript_redacts_codes_accounts_and_passwords(self):
        turns = [
            {"role": "user", "text": "verification code 123456"},
            {"role": "user", "text": "routing number 123456789"},
            {"role": "user", "text": "password: hunter2"},
        ]
        record = redact_record({"transcript": turns})
        self.assertTrue(all("[REDACTED]" in turn["text"]
                            for turn in record["transcript"]))

    def test_persisted_transcript_redacts_provider_credentials(self):
        turns = [
            {"role": "user", "text": "Authorization: Bearer secret-value"},
            {"role": "user", "text": "x-goog-api-key=secret-value"},
            {"role": "user", "text": "hsurr:" + "abcdefghijk"},
            {"role": "user", "text": "sk-" + "abcdefghijklmnopqrstuvwxyz"},
        ]
        record = redact_record({"transcript": turns})
        self.assertTrue(all("[REDACTED]" in turn["text"]
                            for turn in record["transcript"]))

    def test_tts_character_budget(self):
        budget = TTSCharacterBudget(limit=10)
        self.assertTrue(budget.reserve("12345"))
        self.assertTrue(budget.reserve("67890"))
        self.assertFalse(budget.reserve("x"))

    def test_response_limits_vary_by_stage(self):
        text = "One sentence. Two sentence. Three sentence."
        self.assertEqual(limit_spoken_response(text, "fast"), "One sentence.")
        self.assertEqual(limit_spoken_response(text, "frontier"),
                         "One sentence. Two sentence.")


class TestEscalation(unittest.TestCase):
    def _ladder(self, now=1000.0):
        clock = lambda: now  # noqa: E731
        return BrainLadder(frontier_model="frontier-test-model", clock=clock)

    def test_fast_to_frontier_on_human_request(self):
        ladder = self._ladder()
        action = ladder.on_user_text("Let me talk to a human please")
        self.assertEqual(action["action"], "switch_to_frontier")
        self.assertEqual(ladder.stage(), "frontier")

    def test_three_frontier_replies_loop_in(self):
        ladder = self._ladder()
        ladder.on_user_text("talk to a human")
        self.assertEqual(ladder.on_assistant_text("One moment.")["action"], "continue")
        self.assertEqual(ladder.on_assistant_text("Checking.")["action"], "continue")
        action = ladder.on_assistant_text("Still here.")
        self.assertEqual(action["action"], "loop_in_nathan")
        self.assertEqual(ladder.stage(), "nathan")
        self.assertIsNotNone(ladder.loop_in_reason)

    def test_time_bound_fires_without_replies(self):
        now = [1000.0]
        ladder = BrainLadder(frontier_model="f", clock=lambda: now[0])
        ladder.on_user_text("talk to a human")
        now[0] = 1000.0 + 121.0
        action = ladder.check_time_bound()
        self.assertEqual(action["action"], "loop_in_nathan")

    def test_repeated_utterance_escalates(self):
        ladder = self._ladder()
        ladder.on_user_text("hello?")
        action = ladder.on_user_text("hello?")
        self.assertEqual(action["action"], "switch_to_frontier")

    def test_sensitive_request_escalates(self):
        ladder = self._ladder()
        action = ladder.on_user_text("What is my social security number?")
        self.assertEqual(action["action"], "switch_to_frontier")

    def test_phone_tree_and_voicemail_end_instead_of_escalate(self):
        for text in ("For sales press 1", "Please leave a message after the tone"):
            action = self._ladder().on_user_text(text)
            self.assertEqual(action["action"], "end_call")

    def test_human_mentions_do_not_trigger_phone_tree_end(self):
        for text in ("You can press 1 on your keypad", "Don't leave a message"):
            self.assertEqual(self._ladder().on_user_text(text)["action"], "continue")

    def test_general_early_ivr_menu_ends(self):
        for text in ("Please press 2 for billing", "Welcome to the main menu",
                     "Enter your extension now"):
            self.assertEqual(self._ladder().on_user_text(text)["action"], "end_call")

    def test_sanitize_outgoing_blocks_ssn(self):
        self.assertIn("can't share", sanitize_outgoing("My SSN is 123-45-6789"))
        self.assertEqual(sanitize_outgoing("Hello there"), "Hello there")


class TestJobAndPrompt(unittest.TestCase):
    def test_disclosure_required(self):
        job = LiveCallJob(to="+13213677796", business="Test",
                          objective="pipeline test")
        self.assertTrue(job.opening.startswith("Hi, this is Nathan's assistant"))
        prompt = build_system_prompt(job)
        self.assertIn("ending the call", prompt)
        self.assertIn("NEVER ask for", prompt)
        self.assertIn("Nathan's AI assistant", prompt)

    def test_default_allowed_decisions(self):
        job = LiveCallJob(to="+13213677796", business="Test",
                          objective="pipeline test")
        self.assertEqual(job.allowed_decisions, ["ending the call"])

    def test_missing_disclosure_rejected(self):
        with self.assertRaises(ValueError):
            LiveCallJob(to="+13213677796", business="Test",
                        objective="pipeline test", opening="Hello")

    def test_extended_disclosure_rejected_to_preserve_cache_contract(self):
        with self.assertRaises(ValueError):
            LiveCallJob(to="+13213677796", business="Test",
                        objective="pipeline test",
                        opening="Hi, this is Nathan's assistant calling about insurance.")

    def test_pronunciation_hints_are_in_prompt(self):
        job = LiveCallJob(to="+13213677796", business="Test",
                          objective="pipeline test",
                          pronunciation_hints={"Acme": "ACK-mee"})
        self.assertIn("Acme: ACK-mee", build_system_prompt(job))


class TestAudioRechunker(unittest.IsolatedAsyncioTestCase):
    async def test_emits_exact_32ms_frames_and_keeps_remainder(self):
        from pipecat.frames.frames import InputAudioRawFrame
        from pipecat.processors.frame_processor import FrameDirection

        pushed = []

        class CapturingRechunker(InputAudioRechunker):
            async def push_frame(self, frame, direction):
                pushed.append(frame)

        rechunker = CapturingRechunker()
        await rechunker.process_frame(
            InputAudioRawFrame(b"x" * 3032, 16000, 1),
            FrameDirection.DOWNSTREAM)
        audio = [frame for frame in pushed if isinstance(frame, InputAudioRawFrame)]
        self.assertEqual([len(frame.audio) for frame in audio], [1024, 1024])
        self.assertEqual(len(rechunker._buffer), 984)


class TestFixedPhraseCache(unittest.TestCase):
    def test_round_trip_and_cache_key_changes_with_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            first = FixedPhraseCache(directory, namespace="jessica/flash/16000")
            second = FixedPhraseCache(directory, namespace="jessica/other/16000")
            audio = b"\x00\x00" * 1600
            first.store("hello", audio)
            self.assertEqual(first.load("hello"), audio)
            metadata = json.loads(first._path("hello").with_suffix(".json").read_text())
            self.assertEqual(metadata["content_type"], "audio/pcm")
            self.assertEqual(metadata["duration_seconds"], 0.1)
            self.assertIsNone(second.load("hello"))


class TestSilencePolicy(unittest.TestCase):
    def test_one_reprompt_then_end_and_speech_resets(self):
        policy = SilencePolicy()
        self.assertEqual(policy.on_timeout(), "reprompt")
        self.assertEqual(policy.on_timeout(), "end")
        policy.on_speech()
        self.assertEqual(policy.on_timeout(), "reprompt")

    def test_activity_tracks_speaking_state(self):
        activity = CallActivity(clock=lambda: 5.0)
        activity.user_started()
        self.assertTrue(activity.user_speaking)
        activity.user_stopped()
        self.assertFalse(activity.user_speaking)
        self.assertEqual(activity.last_activity, 5.0)


class TestProviderConstructionOffline(unittest.TestCase):
    """Construct (never start) the real Pipecat services with dummy keys.
    Construction must not touch the network; starting them would, so no
    test here starts a service or a pipeline. Direct construction also
    needs the proxy-env cleaning (see _clean_proxy_env): the VM's
    NO_PROXY contains bracketed IPv6 entries httpx cannot parse."""

    def test_services_construct(self):
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
        from pipecat.services.google.llm import GoogleLLMService
        from pipecat.services.groq.stt import GroqSTTService
        from pipecat.serializers.telnyx import TelnyxFrameSerializer
        from live_pipeline import _clean_proxy_env

        with _clean_proxy_env():
            stt = GroqSTTService(
                api_key="dummy",
                settings=GroqSTTService.Settings(model="whisper-large-v3-turbo"))
            llm = GoogleLLMService(
                api_key="dummy",
                settings=GoogleLLMService.Settings(model="gemini-test"))
            tts = ElevenLabsTTSService(api_key="dummy",
                                       voice_id="kCx3Qoh3lfILbbTZftSq")
        serializer = TelnyxFrameSerializer(
            stream_id="stream-1", outbound_encoding="PCMU",
            inbound_encoding="PCMU", call_control_id=CONTROL_ID,
            api_key="dummy")
        self.assertIsNotNone(stt)
        self.assertIsNotNone(llm)
        self.assertIsNotNone(tts)
        self.assertIsNotNone(serializer)

    def test_full_pipeline_builds_offline(self):
        """Build the whole pipeline against a fake websocket object.
        No service is started, so no network is touched. VAD model is
        bundled locally (silero_vad.onnx ships with pipecat)."""
        import os as _os
        _os.environ["GROQ_API_KEY"] = "dummy"
        _os.environ["GEMINI_API_KEY"] = "dummy"
        _os.environ["ELEVENLABS_API_KEY"] = "dummy"
        _os.environ["TELNYX_API_KEY"] = "dummy"
        from live_pipeline import LiveCallJob, LiveCredentialRefs, build_live_pipeline

        class FakeWebSocket:
            pass

        job = LiveCallJob(to="+13213677796", business="Test",
                          objective="pipeline test")
        task, ladder, services = build_live_pipeline(
            FakeWebSocket(),
            {"stream_id": "stream-1", "call_id": CONTROL_ID,
             "outbound_encoding": "PCMU"},
            job, LiveCredentialRefs(),
            {"fast": "dummy-fast", "frontier": "dummy-frontier",
             "stt": "dummy-stt"})
        self.assertEqual(services["call_control_id"], CONTROL_ID)
        self.assertEqual(ladder.stage(), "fast")
        self.assertIsNotNone(task)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestResponseGate(unittest.IsolatedAsyncioTestCase):
    async def _drive(self, ladder, fragments):
        from pipecat.frames.frames import (
            LLMFullResponseEndFrame,
            LLMFullResponseStartFrame,
            TextFrame,
        )
        from pipecat.processors.frame_processor import FrameDirection
        from live_pipeline import ResponseGate

        pushed = []

        class CapturingGate(ResponseGate):
            async def push_frame(self, frame, direction):
                pushed.append(frame)

        gate = CapturingGate(ladder)
        await gate.process_frame(LLMFullResponseStartFrame(),
                                 FrameDirection.DOWNSTREAM)
        for fragment in fragments:
            await gate.process_frame(TextFrame(text=fragment),
                                     FrameDirection.DOWNSTREAM)
        await gate.process_frame(LLMFullResponseEndFrame(),
                                 FrameDirection.DOWNSTREAM)
        return pushed

    async def test_fragments_released_as_one_response(self):
        from pipecat.frames.frames import AggregatedTextFrame
        ladder = BrainLadder(frontier_model="f")
        pushed = await self._drive(ladder, ["Hel", "lo there."])
        texts = [f.text for f in pushed if isinstance(f, AggregatedTextFrame)]
        self.assertEqual(texts, ["Hello there."])

    async def test_ssn_response_replaced_with_refusal(self):
        from pipecat.frames.frames import AggregatedTextFrame
        ladder = BrainLadder(frontier_model="f")
        pushed = await self._drive(ladder, ["Your number is 123-45-6789."])
        texts = [f.text for f in pushed if isinstance(f, AggregatedTextFrame)]
        self.assertEqual(texts, ["I can't share or repeat that kind of information on a call."])

    async def test_third_frontier_reply_defers_loop_in_playback_to_outer_watcher(self):
        ladder = BrainLadder(frontier_model="f")
        ladder.on_user_text("talk to a human")
        ladder.on_assistant_text("One.")
        ladder.on_assistant_text("Two.")
        pushed = await self._drive(ladder, ["Three."])
        texts = [f.text for f in pushed if f.__class__.__name__ == "TextFrame"]
        self.assertEqual(texts, [])
        self.assertEqual(ladder.stage(), "nathan")
        self.assertIsNotNone(ladder.loop_in_reason)


class TestProxyEnvCleaning(unittest.TestCase):
    def test_bracketed_entries_stripped_and_restored(self):
        import os
        from live_pipeline import _clean_proxy_env
        old_lower = os.environ.pop("no_proxy", None)
        os.environ["NO_PROXY"] = "localhost,[::1],127.0.0.1"
        try:
            with _clean_proxy_env():
                self.assertEqual(os.environ["NO_PROXY"], "localhost,127.0.0.1")
            self.assertEqual(os.environ["NO_PROXY"], "localhost,[::1],127.0.0.1")
        finally:
            if old_lower is not None:
                os.environ["no_proxy"] = old_lower


class TestTranscriptTap(unittest.IsolatedAsyncioTestCase):
    async def test_completed_segment_records_exactly_once(self):
        from pipecat.frames.frames import TranscriptionFrame
        from pipecat.processors.frame_processor import FrameDirection

        pushed = []

        class CapturingTap(TranscriptTap):
            async def push_frame(self, frame, direction):
                pushed.append(frame)

        ladder = BrainLadder(frontier_model="frontier")
        tap = CapturingTap(ladder)
        frame = TranscriptionFrame("hello", "caller", "2026-10-29T17:00:00Z",
                                   finalized=True)
        await tap.process_frame(frame, FrameDirection.DOWNSTREAM)
        await tap.process_frame(frame, FrameDirection.DOWNSTREAM)
        self.assertEqual(ladder.transcript, [{"role": "user", "text": "hello"}])
        self.assertEqual(pushed, [frame, frame])

    async def test_interim_transcript_is_not_recorded(self):
        from pipecat.frames.frames import TranscriptionFrame
        from pipecat.processors.frame_processor import FrameDirection

        class CapturingTap(TranscriptTap):
            async def push_frame(self, frame, direction):
                pass

        ladder = BrainLadder(frontier_model="frontier")
        await CapturingTap(ladder).process_frame(
            TranscriptionFrame("partial", "caller", "timestamp"),
            FrameDirection.DOWNSTREAM)
        self.assertEqual(ladder.transcript, [])


class TestRuntimeBounds(unittest.IsolatedAsyncioTestCase):
    async def test_call_limit_returns_without_waiting_in_test(self):
        from live_pipeline import _wait_for_call_limit

        sleep = AsyncMock()
        self.assertEqual(await _wait_for_call_limit(2, sleep=sleep),
                         "max_minutes")
        sleep.assert_awaited_once_with(120)

    async def test_frontier_limit_fires_without_reply(self):
        from live_pipeline import _wait_for_frontier_limit

        now = [1000.0]
        ladder = BrainLadder(frontier_model="frontier", clock=lambda: now[0])
        ladder.on_user_text("talk to a human")

        async def advance(_):
            now[0] += 121

        result = await _wait_for_frontier_limit(ladder, sleep=advance)
        self.assertEqual(result["action"], "loop_in_nathan")

    async def test_canonical_hangup_uses_persisted_command(self):
        from live_pipeline import _hangup_via_adapter

        class Context:
            def __init__(self, value):
                self.value = value

            async def __aenter__(self):
                return self.value

            async def __aexit__(self, *_):
                return False

        class Response:
            status = 200

            async def json(self):
                return {"data": {"result": "ok"}}

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def post(self, *_args, **_kwargs):
                return Context(Response())

        class Adapter:
            def __init__(self):
                self.acknowledged = False

            def request_stop(self, **_kwargs):
                return {"stop": True}

            def hangup_command(self, _attempt_id):
                return {"path": "/v2/calls/control/actions/hangup",
                        "json": {"command_id": ATTEMPT_ID}}

            def acknowledge_hangup(self, _attempt_id, response):
                self.acknowledged = response == {"data": {"result": "ok"}}

        session = Session()
        adapter = Adapter()
        with patch("aiohttp.ClientSession", return_value=session) as factory:
            self.assertTrue(await _hangup_via_adapter(
                adapter, ATTEMPT_ID, "dummy", "test"))
        factory.assert_called_once()
        self.assertTrue(factory.call_args.kwargs['trust_env'])
        self.assertEqual(factory.call_args.kwargs['timeout'].total, 8)
        self.assertEqual(factory.call_args.kwargs['timeout'].connect, 3)
        self.assertTrue(adapter.acknowledged)

    async def test_serializer_has_no_direct_hangup_fallback(self):
        from live_pipeline import _CountingTelnyxSerializer

        serializer = _CountingTelnyxSerializer(
            stream_id="stream", outbound_encoding="PCMU",
            inbound_encoding="PCMU", call_control_id=CONTROL_ID,
            api_key="dummy")
        with patch("aiohttp.ClientSession") as factory:
            await serializer._hang_up_call()
        factory.assert_not_called()

    async def test_cost_watcher_stops_on_projected_cap(self):
        from live_pipeline import _wait_for_cost_gate

        ladder = BrainLadder(frontier_model="frontier")
        ladder.transcript.append({"role": "assistant", "text": "x" * 30000})
        result = await _wait_for_cost_gate(
            ladder, CostLedger(test_call=True), started=0,
            sleep=AsyncMock(), clock=lambda: 1)
        self.assertEqual(result, "test_budget_exceeded")

    async def test_cost_watcher_stops_on_soft_cap(self):
        from live_pipeline import _wait_for_cost_gate

        ladder = BrainLadder(frontier_model="frontier")
        ladder.transcript.append({"role": "assistant", "text": "x" * 150000})
        result = await _wait_for_cost_gate(
            ladder, CostLedger(test_call=False), started=0,
            sleep=AsyncMock(), clock=lambda: 1)
        self.assertEqual(result, "soft_cap")


class TestVADBufferBoundary(unittest.TestCase):
    """Regression test for Pipecat 1.8.0 VAD buffer-boundary behavior.

    What this test proves (and what it does not): VADAnalyzer._run_analyzer
    processes every complete 32 ms window in an incoming buffer inside one
    while loop, and only AFTER the loop checks whether STARTING has reached
    its confirmation count. A passing run can therefore be reset by a
    failing window inside the SAME buffer before SPEAKING is ever confirmed
    -- even though the same score sequence delivered one window per call
    DOES confirm. This matters live because our frames are 3,032 bytes =
    94.75 ms at 16 kHz = almost three VAD windows, so window-to-buffer
    alignment is not an academic detail.

    Method: the REAL SileroVADAnalyzer state machine (_run_analyzer) is
    driven with a scripted per-window confidence sequence (test-harness
    stub of voice_confidence/_get_smoothed_volume only -- production code
    is untouched). Volume is held at 0.9, above the 0.6 gate, so volume
    never binds; only the confidence sequence and the delivery mode vary.
    Buffer contents are zero bytes and are ignored by the stub, so this
    test characterizes the state machine only, NOT Silero's speech
    recognition, and NOT its 5-second wall-clock model-reset schedule
    (the stub bypasses the ONNX model entirely). Gate values are the
    live ones: confidence 0.7, start_secs 0.2, stop_secs 0.2,
    min_volume 0.6, at 16 kHz (512-sample / 1,024-byte windows, so
    start_secs 0.2 needs 6 consecutive passing windows).

    Observed behavior (asserted, not assumed -- if Pipecat changes the
    state machine, this test fails and must be re-characterized, not
    force-fit):
    - Arm A, one 1,024-byte window per call, scores 6x pass then 2x fail:
      SPEAKING is confirmed on the 6th call.
    - Arm B, the SAME scores delivered in 3,032-byte live-shaped buffers
      (2 + 3 + 3 windows across three buffers): SPEAKING is NEVER
      observed; the final state is QUIET, because the failing windows
      in the third buffer reset STARTING before the post-loop
      confirmation check runs.
    - Arm C, sustained passing scores in the same 3,032-byte buffers:
      SPEAKING IS confirmed -- so 3,032-byte delivery per se does not
      break confirmation; it is specifically trailing failing windows
      inside the confirming buffer that kill it.
    """

    def test_same_scores_confirm_per_window_but_not_per_live_buffer(self):
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.audio.vad.vad_analyzer import VADState

        class ScriptedAnalyzer(SileroVADAnalyzer):
            """Test-only stub: scripted scores, real _run_analyzer."""

            def __init__(self, confidences, volume=0.9):
                super().__init__()
                self._script = list(confidences)
                self._index = 0
                self._volume = volume

            def voice_confidence(self, buffer):
                value = self._script[self._index] if self._index < len(self._script) else 0.0
                self._index += 1
                return value

            def _get_smoothed_volume(self, audio):
                return self._volume

        def make_analyzer(confidences):
            analyzer = ScriptedAnalyzer(confidences)
            analyzer.set_sample_rate(16000)
            self.assertEqual(analyzer._vad_start_frames, 6)
            return analyzer

        window = b"\x00" * 1024  # one 32 ms window at 16 kHz
        live_frame = b"\x00" * 3032  # live-shaped frame: ~3 windows
        passing, failing = 0.9, 0.1  # above / below the 0.7 gate

        # Arm A: one window per call, 6 passes then 2 fails.
        analyzer_a = make_analyzer([passing] * 6 + [failing] * 2)
        states_a = [analyzer_a._run_analyzer(window) for _ in range(8)]
        self.assertEqual(
            states_a[5], VADState.SPEAKING,
            "per-window delivery must confirm SPEAKING on the 6th passing window")
        self.assertEqual(
            states_a[7], VADState.STOPPING,
            "after confirmation, 2 failing windows move SPEAKING to STOPPING")

        # Arm B: same scores in 3,032-byte buffers (2 + 3 + 3 windows).
        analyzer_b = make_analyzer([passing] * 6 + [failing] * 2)
        states_b = [analyzer_b._run_analyzer(live_frame) for _ in range(3)]
        self.assertEqual(
            analyzer_b._index, 8,
            "three live frames must consume exactly the 8 scripted windows")
        self.assertNotIn(
            VADState.SPEAKING, states_b,
            "live-buffer delivery must NEVER confirm SPEAKING for this sequence")
        self.assertEqual(
            states_b[2], VADState.QUIET,
            "trailing failing windows in the confirming buffer reset STARTING to QUIET")

        # Arm C: sustained passing scores in the same 3,032-byte buffers.
        analyzer_c = make_analyzer([passing] * 8)
        states_c = [analyzer_c._run_analyzer(live_frame) for _ in range(3)]
        self.assertEqual(
            states_c[2], VADState.SPEAKING,
            "sustained passing scores must still confirm, even in live-shaped buffers")
