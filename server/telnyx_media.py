"""Telnyx media streaming: attach gate, session state machine, and audio codecs.

Built Oct 29, 2026 for the live voice pipeline (Groq STT, ElevenLabs
TTS). No Deepgram anywhere in this file. No call is dialled here and no
provider credential value is read here — the attach gate works only
against the canonical adapter's durable attempt records.

How a live call's media attaches (outbound flow):
  1. Parent dials through the canonical adapter (dial payload carries
     client_state = base64({"attempt_id": <uuid>})).
  2. On call.answered, the parent issues streaming_start (see
     streaming_start_request() below) with stream_url =
     wss://<public-host>/telnyx/media/<attempt_id>. The attempt id in
     the URL path is a correlation token, not a secret — it is the same
     value the dial already carried in client_state.
  3. Telnyx connects its media websocket to that URL. The media handler
     reads the attempt token from the URL path and passes it to
     MediaAttachGate.authorize() together with the call_control_id
     from the websocket start message.
  4. The gate authorizes ONLY when the attempt exists, is past the
     prepared state, is bound to exactly that call_control_id, and its
     route (connection_id, from, to) matches the approved job's route.
     The call_session_id is NEVER consulted: session-id-only joins
     cannot attach, by construction — the gate's signature has no
     session-id parameter at all.
  5. Audio: Telnyx speaks PCMU (G.711 mu-law) at 8 kHz by default.
     Inbound PCMU is decoded to 16-bit PCM for the pipeline; outbound
     PCM is encoded back to PCMU for Telnyx. Barge-in is the pipeline's
     VAD interruption; this module only tracks the speaking flags the
     tests exercise (user speech while the agent is speaking).

Telnyx media websocket messages handled here (Twilio-compatible shape
Telnyx documents for its media streams):
  {"event": "streaming_started", "stream_id": ..., "call_control_id": ...}
  {"event": "media", "media": {"payload": "<base64 PCMU>", "track": "inbound"}}
  {"event": "streaming_stopped"} / {"event": "stop"}
Anything else is ignored and counted, never attached, never acted on.
"""
from __future__ import annotations

import base64
import binascii
import json

# ---------------------------------------------------------------------------
# G.711 mu-law codec (pure Python; no dependency). Telnyx default is PCMU
# at 8 kHz. Decode table built from the standard mu-law expansion.
# ---------------------------------------------------------------------------

def _build_ulaw_decode_table() -> list[int]:
    # G.711: the wire byte is complemented before decoding.
    table = []
    for byte in range(256):
        u = (~byte) & 0xFF
        sign = -1 if (u & 0x80) else 1
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        sample = ((mantissa << 3) + 0x84) << exponent
        sample -= 0x84
        table.append(sign * sample)
    return table

_ULAW_DECODE = _build_ulaw_decode_table()


def pcmu_to_pcm16(data: bytes) -> bytes:
    """Decode mu-law bytes to little-endian signed 16-bit PCM."""
    out = bytearray()
    for byte in data:
        sample = _ULAW_DECODE[byte]
        out += int(sample).to_bytes(2, "little", signed=True)
    return bytes(out)


def pcm16_to_pcmu(data: bytes) -> bytes:
    """Encode little-endian signed 16-bit PCM to mu-law bytes."""
    if len(data) % 2:
        raise ValueError("pcm16 input must have an even byte length")
    out = bytearray()
    for i in range(0, len(data), 2):
        sample = int.from_bytes(data[i:i + 2], "little", signed=True)
        sign = 0x80 if sample < 0 else 0
        biased = min(abs(sample), 32635) + 0x84
        # Segment: decoded values satisfy biased in [132*2^exp, 252*2^exp),
        # so the highest set bit of biased is always bit (7 + exp).
        exponent = max(0, min(7, biased.bit_length() - 8))
        mantissa = (biased >> (exponent + 3)) & 0x0F
        out.append((~(sign | (exponent << 4) | mantissa)) & 0xFF)
    return bytes(out)


# ---------------------------------------------------------------------------
# streaming_start request builder (Call Control action).
# POST /v2/calls/{call_control_id}/actions/streaming_start
# Field names verified against Telnyx's documented streaming parameters
# and two independent integration write-ups: stream_url, stream_track,
# stream_bidirectional_mode are the core fields; codec/rate are the
# documented defaults for bidirectional RTP. The first live run's API
# response is the final confirmation for the optional fields — a 4xx
# there surfaces before any audio flows, never mid-call silently.
# ---------------------------------------------------------------------------

def streaming_start_request(stream_url: str, *, track: str = "inbound_track",
                            bidirectional_mode: str = "rtp",
                            bidirectional_codec: str = "PCMU",
                            bidirectional_sampling_rate: int = 8000) -> dict:
    if not stream_url.startswith("wss://"):
        raise ValueError("stream_url must be a wss:// URL")
    if track not in ("inbound_track", "outbound_track", "both_tracks"):
        raise ValueError(f"unknown stream track: {track}")
    if bidirectional_mode not in ("rtp", "mp3"):
        raise ValueError(f"unknown bidirectional mode: {bidirectional_mode}")
    return {
        "method": "POST",
        "path": "/v2/calls/{call_control_id}/actions/streaming_start",
        "json": {
            "stream_url": stream_url,
            "stream_track": track,
            "stream_bidirectional_mode": bidirectional_mode,
            "stream_bidirectional_codec": bidirectional_codec,
            "stream_bidirectional_sampling_rate": bidirectional_sampling_rate,
        },
    }


# ---------------------------------------------------------------------------
# Attach gate — the ONLY way a media stream joins a call.
# ---------------------------------------------------------------------------

class MediaAttachGate:
    """Authorize a media websocket against the canonical adapter.

    adapter must expose get_attempt(attempt_id) -> dict with keys
    state, control_id, and payload_json (the canonical dial payload:
    {"connection_id", "from", "to", "client_state", "command_id"}).
    """

    def __init__(self, adapter):
        self._adapter = adapter

    def authorize(self, *, attempt_id: str, call_control_id: str,
                  expected_route: tuple[str, str, str]) -> tuple[bool, str]:
        """Return (True, 'attached') or (False, reason). Never attaches
        on a session id: this method takes no session id at all."""
        if not attempt_id or not call_control_id:
            return False, "missing_identity"
        try:
            attempt = self._adapter.get_attempt(attempt_id)
        except Exception:
            return False, "unknown_attempt"
        if attempt is None:
            return False, "unknown_attempt"
        if attempt.get("state") in ("prepared", "cancelled_before_submit"):
            return False, "unclaimed_attempt"
        if attempt.get("state") == "hangup" or attempt.get("stop"):
            return False, "terminal_or_stopped_attempt"
        bound_control = attempt.get("control_id")
        if not bound_control:
            return False, "unbound_attempt"
        if attempt.get("state") not in ("submit_unknown", "initiated", "answered"):
            return False, "invalid_attempt_state"
        if bound_control != call_control_id:
            return False, "control_mismatch"
        try:
            payload = json.loads(attempt.get("payload_json") or "{}")
        except (ValueError, TypeError):
            return False, "corrupt_attempt"
        route = (payload.get("connection_id"), payload.get("from"), payload.get("to"))
        if route != expected_route:
            return False, "route_mismatch"
        return True, "attached"


# ---------------------------------------------------------------------------
# Media session state machine (pure logic; the websocket loop feeds it
# dict messages and drains PCM it can hand to the pipeline).
# States: created -> started -> stopped. Monotonic: stopped absorbs
# everything; media before started is dropped and counted, never queued.
# ---------------------------------------------------------------------------

class TelnyxMediaSession:
    def __init__(self, *, attempt_id: str, call_control_id: str, stream_id: str = ""):
        self.attempt_id = attempt_id
        self.call_control_id = call_control_id
        self.stream_id = stream_id
        self.state = "created"
        self._inbound_pcm = bytearray()
        self.dropped_before_start = 0
        self.dropped_after_stop = 0
        self.ignored_messages = 0
        self.agent_speaking = False
        self.interruption_pending = False

    def handle_message(self, message: dict) -> str:
        """Feed one Telnyx media websocket message. Returns an outcome
        string for logging; never raises on a malformed message."""
        if not isinstance(message, dict):
            self.ignored_messages += 1
            return "ignored"
        event = message.get("event")
        if self.state == "stopped":
            self.dropped_after_stop += 1
            return "dropped_after_stop"
        if event == "streaming_started":
            if message.get("call_control_id") not in (None, self.call_control_id):
                return "ignored_control_mismatch"
            self.stream_id = message.get("stream_id", self.stream_id)
            self.state = "started"
            return "started"
        if event in ("streaming_stopped", "stop"):
            self.state = "stopped"
            return "stopped"
        if event == "media":
            if self.state != "started":
                self.dropped_before_start += 1
                return "dropped_before_start"
            media = message.get("media") or {}
            if media.get("track") not in (None, "inbound", "inbound_track"):
                return "ignored_non_inbound_track"
            try:
                raw = base64.b64decode(media.get("payload", ""), validate=True)
            except (binascii.Error, ValueError, TypeError):
                self.ignored_messages += 1
                return "ignored_bad_payload"
            self._inbound_pcm += pcmu_to_pcm16(raw)
            return "media_queued"
        self.ignored_messages += 1
        return "ignored"

    def drain_inbound_pcm(self) -> bytes:
        data = bytes(self._inbound_pcm)
        self._inbound_pcm.clear()
        return data

    def note_user_speech(self) -> None:
        """Caller started speaking. If the agent is speaking, this is a
        barge-in: flag it so the pipeline can interrupt TTS output."""
        if self.agent_speaking:
            self.interruption_pending = True

    def take_interruption(self) -> bool:
        pending = self.interruption_pending
        self.interruption_pending = False
        return pending

    def encode_outbound_media(self, pcm16: bytes, stream_id: str = "") -> dict:
        """Encode pipeline PCM16 output as a Telnyx media message."""
        return {
            "event": "media",
            "stream_id": stream_id or self.stream_id,
            "media": {"payload": base64.b64encode(pcm16_to_pcmu(pcm16)).decode()},
        }


class FakeMediaSocket:
    """In-memory stand-in for a Telnyx media websocket, for tests.

    send_queue holds messages the test "server" sends to the session;
    recv_queue holds messages the session sends back. No network.
    """

    def __init__(self):
        self.send_queue: list[dict] = []
        self.recv_queue: list[dict] = []

    def feed(self, message: dict) -> None:
        self.send_queue.append(message)

    def send(self, message: dict) -> None:
        self.recv_queue.append(message)
