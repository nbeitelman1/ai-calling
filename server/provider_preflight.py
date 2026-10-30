"""No-generation provider/model preflight. Writes a readiness receipt only."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import aiohttp

from model_select import resolve_models_from_env


async def _get_json(session, url, headers):
    async with session.get(url, headers=headers) as response:
        if response.status != 200:
            raise RuntimeError(f"provider preflight returned HTTP {response.status}")
        return await response.json()


async def run_preflight() -> dict:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        groq, gemini, voice, eleven_models = await asyncio.gather(
            _get_json(session, "https://api.groq.com/openai/v1/models",
                      {"Authorization": "Bearer " + os.environ["GROQ_API_KEY"]}),
            _get_json(session,
                      "https://generativelanguage.googleapis.com/v1beta/models",
                      {"x-goog-api-key": os.environ["GEMINI_API_KEY"]}),
            _get_json(session,
                      "https://api.elevenlabs.io/v1/voices/"
                      + os.environ.get("ELEVENLABS_VOICE_ID",
                                       "cgSgspJ2msm6clMCkdW9"),
                      {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}),
            _get_json(session, "https://api.elevenlabs.io/v1/models",
                      {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}),
        )
    offered = {
        "groq": [item["id"] for item in groq.get("data", [])],
        "gemini": [item["name"].removeprefix("models/")
                   for item in gemini.get("models", [])],
    }
    resolved = resolve_models_from_env(offered)
    eleven_ids = {item.get("model_id") for item in eleven_models}
    tts_model = os.environ.get("ELEVENLABS_TTS_MODELS", "eleven_flash_v2_5").split(",")[0]
    if tts_model not in eleven_ids:
        raise RuntimeError("configured ElevenLabs model is unavailable")
    receipt = {"ready": True, "checked_at": time.time(), "models": resolved,
               "tts_model": tts_model, "voice_name": voice.get("name", "")}
    path = Path(os.environ.get(
        "AI_CALLING_PREFLIGHT_RECEIPT",
        str(Path(__file__).resolve().parent.parent / "logs" /
            "provider-preflight.json")))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    temporary.replace(path)
    return receipt


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_preflight()), indent=2))
