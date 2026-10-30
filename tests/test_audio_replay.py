"""Synthetic PCMU replay checks. No personal audio and no provider calls."""
import base64
import json
import math
import os
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from live_pipeline import InputAudioRechunker, _CountingTelnyxSerializer
from pipecat.processors.frame_processor import FrameDirection
from telnyx_media import pcm16_to_pcmu


def tone_pcm(seconds=1.0, amplitude=5000, frequency=220, rate=8000):
    return b"".join(
        int(amplitude * math.sin(2 * math.pi * frequency * i / rate))
        .to_bytes(2, "little", signed=True)
        for i in range(int(seconds * rate)))


class TestSyntheticReplay(unittest.IsolatedAsyncioTestCase):
    async def _decode(self, pcmu, chunk=160):
        serializer = _CountingTelnyxSerializer(
            stream_id="stream", outbound_encoding="PCMU",
            inbound_encoding="PCMU", call_control_id="control", api_key="dummy")
        serializer._sample_rate = 16000
        frames = []
        for index in range(0, len(pcmu), chunk):
            frame = await serializer.deserialize(json.dumps({
                "event": "media", "sequence_number": index // chunk + 1,
                "media": {"track": "inbound", "payload": base64.b64encode(
                    pcmu[index:index + chunk]).decode()},
            }))
            if frame:
                frames.append(frame)
        return frames, serializer

    async def test_silence_and_tone_preserve_expected_duration(self):
        for pcm in (b"\x00\x00" * 8000, tone_pcm()):
            frames, serializer = await self._decode(pcm16_to_pcmu(pcm))
            total_samples = sum(frame.num_frames for frame in frames)
            self.assertEqual(serializer._input_payload_bytes, 8000)
            self.assertGreaterEqual(total_samples, 15000)
            self.assertLessEqual(total_samples, 16000)
            self.assertEqual(serializer._sequence_gaps, 0)

    async def test_rechunking_conserves_complete_windows(self):
        frames, _ = await self._decode(pcm16_to_pcmu(tone_pcm(seconds=1.1)))
        output = []

        class Capture(InputAudioRechunker):
            async def push_frame(self, frame, direction):
                output.append(frame)

        rechunker = Capture()
        for frame in frames:
            await rechunker.process_frame(frame, FrameDirection.DOWNSTREAM)
        self.assertTrue(output)
        self.assertTrue(all(len(frame.audio) == 1024 for frame in output))
        self.assertLess(len(rechunker._buffer), 1024)

    async def test_noise_replay_is_deterministic(self):
        random.seed(7)
        pcm = b"".join(random.randint(-1200, 1200).to_bytes(
            2, "little", signed=True) for _ in range(8000))
        frames_a, _ = await self._decode(pcm16_to_pcmu(pcm))
        frames_b, _ = await self._decode(pcm16_to_pcmu(pcm))
        self.assertEqual([len(frame.audio) for frame in frames_a],
                         [len(frame.audio) for frame in frames_b])
        self.assertEqual(sum(frame.num_frames for frame in frames_a),
                         sum(frame.num_frames for frame in frames_b))


if __name__ == "__main__":
    unittest.main()
