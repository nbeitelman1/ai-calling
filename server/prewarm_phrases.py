"""Generate fixed Jessica phrases once; subsequent calls replay cached PCM."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import aiohttp

from live_pipeline import (
    DISCLOSURE_OPENING,
    FixedPhraseCache,
)
from model_select import REFUSAL_LINE

PHRASES = {
    DISCLOSURE_OPENING,
    "Are you still there?",
    "I haven't heard anything, so I'll end the call now. Goodbye.",
    "Let me bring Nathan in — one moment.",
    REFUSAL_LINE,
}


async def main():
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "cgSgspJ2msm6clMCkdW9")
    model = os.environ.get("ELEVENLABS_TTS_MODELS", "eleven_flash_v2_5").split(",")[0]
    rate = int(os.environ.get("ELEVENLABS_SAMPLE_RATE", "16000"))
    cache = FixedPhraseCache(
        os.environ.get("AI_CALLING_PHRASE_CACHE",
                       str(Path(__file__).resolve().parent.parent / "logs" /
                           "phrase-cache")),
        namespace=f"{voice}/{model}/pcm_{rate}", sample_rate=rate,
    )
    missing = [phrase for phrase in PHRASES if cache.load(phrase) is None]
    if not missing:
        return
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
    headers = {"xi-api-key": os.environ["ELEVENLABS_API_KEY"],
               "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        for phrase in missing:
            async with session.post(
                    url, headers=headers, params={"output_format": f"pcm_{rate}"},
                    json={"text": phrase, "model_id": model}) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"ElevenLabs phrase prewarm returned HTTP {response.status}")
                content_type = response.headers.get("Content-Type", "").lower()
                if not content_type.startswith("audio/"):
                    raise RuntimeError("ElevenLabs phrase prewarm returned non-audio content")
                audio = await response.read()
                if not audio:
                    raise RuntimeError("ElevenLabs phrase prewarm returned empty audio")
                cache.store(phrase, audio)


if __name__ == "__main__":
    asyncio.run(main())
