"""Resolve credentials in-memory, preflight providers, prewarm audio, prune logs."""
import asyncio
import os
import time
from pathlib import Path

from provider_preflight import run_preflight
from prewarm_phrases import main as prewarm
from start_live_server import _provision


async def main():
    _provision("GROQ_API_KEY", "custom.groq")
    _provision("GEMINI_API_KEY", "custom.gemini")
    _provision("ELEVENLABS_API_KEY", "custom.elevenlabs")
    _provision("TELNYX_API_KEY", "custom.telnyx")
    await run_preflight()
    await prewarm()
    log_dir = Path(os.environ["AI_CALLING_LOG_DIR"])
    cutoff = time.time() - int(os.environ.get("AI_CALLING_LOG_RETENTION_DAYS", "30")) * 86400
    for path in log_dir.glob("*.jsonl"):
        if path.stat().st_mtime < cutoff:
            path.unlink()


if __name__ == "__main__":
    asyncio.run(main())
