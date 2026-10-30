"""No-generation provider/model preflight. Writes a readiness receipt only."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import aiohttp

from model_select import resolve_models_from_env
from provider_config import configuration_fingerprint, receipt_path, tts_configuration


async def _gemini_model_catalog(session, headers):
    from urllib.parse import urlencode
    models = []
    seen_tokens = set()
    token = None
    for _ in range(20):
        params = {'pageSize': 1000}
        if token:
            params['pageToken'] = token
        page = await _get_json(session,
            'https://generativelanguage.googleapis.com/v1beta/models?' + urlencode(params), headers)
        models.extend(page.get('models', []))
        token = page.get('nextPageToken')
        if not token:
            return {'models': models}
        if token in seen_tokens:
            raise RuntimeError('provider model pagination repeated a token')
        seen_tokens.add(token)
    raise RuntimeError('provider model pagination exceeded safety bound')


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
            _gemini_model_catalog(session,
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
                   for item in gemini.get("models", [])
                   if "generateContent" in item.get("supportedGenerationMethods", [])],
    }
    resolved = resolve_models_from_env(offered)
    eleven_ids = {item.get("model_id") for item in eleven_models}
    tts = tts_configuration()
    if tts['model'] not in eleven_ids:
        raise RuntimeError("configured ElevenLabs model is unavailable")
    receipt = {"ready": True, "checked_at": time.time(), "models": resolved,
               "tts_model": tts['model'], "voice_id": tts['voice'],
               "voice_name": voice.get("name", ""),
               "configuration_fingerprint": configuration_fingerprint()}
    path = receipt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    temporary.replace(path)
    return receipt


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_preflight()), indent=2))
