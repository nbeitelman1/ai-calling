"""Launcher for the live FastAPI server (live_server.py).

Resolves Secure Vault surrogates into PROCESS MEMORY ONLY (never files,
never logs, never printed) for the env names live_pipeline._resolve_ref
reads, sets the deploy-time model pools, then starts uvicorn on 8001
(the port the tunnel forwards to).

Model pools (Oct 29, 2026; verified against the account's live
/v1/models list — first pool entry present wins):
  GEMINI_FAST_MODELS     = gemini-3.5-flash-lite
  GEMINI_FRONTIER_MODELS = gemini-3.8-flash
  GROQ_STT_MODELS        = whisper-large-v3-turbo, whisper-large-v3
  ELEVENLABS_TTS_MODELS  = (unset -> service default eleven_flash_v2_5)

TELNYX_WEBHOOK_PUBLIC_KEY must already be in the environment (public
by design; supplied by the parent process, never from a file here).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
def _provision(env_name: str, credential_name: str) -> None:
    if os.environ.get(env_name, "").strip():
        print(f"using pre-provisioned {env_name}")
        return
    sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
    try:
        from dynamic_credentials import dynamic_credential_entry
    except ImportError as exc:
        raise RuntimeError(
            f"{env_name} is unset and the optional vault bridge is unavailable") from exc
    entry = dynamic_credential_entry(credential_name)
    surrogate = str(entry["surrogate"]).strip()
    if not surrogate.startswith("hsurr:"):
        raise RuntimeError(f"non-surrogate value for {credential_name}")
    os.environ[env_name] = surrogate
    print(f"provisioned {env_name} from {credential_name} (surrogate only)")


def main() -> int:
    if os.environ.get("AI_CALLING_LOG_JSON", "1") == "1":
        from loguru import logger
        logger.remove()
        logger.add(sys.stderr, serialize=True,
                   level=os.environ.get("AI_CALLING_LOG_LEVEL", "INFO"))
    if not os.environ.get("TELNYX_WEBHOOK_PUBLIC_KEY", "").strip():
        print("Refusing to start: TELNYX_WEBHOOK_PUBLIC_KEY is not set.")
        return 2
    _provision("GROQ_API_KEY", "custom.groq")
    _provision("GEMINI_API_KEY", "custom.gemini")
    _provision("ELEVENLABS_API_KEY", "custom.elevenlabs")
    _provision("TELNYX_API_KEY", "custom.telnyx")
    os.environ.setdefault("GEMINI_FAST_MODELS", "gemini-3.5-flash-lite")
    os.environ.setdefault("GEMINI_FRONTIER_MODELS", "gemini-3.8-flash")
    os.environ.setdefault("GROQ_STT_MODELS",
                          "whisper-large-v3-turbo,whisper-large-v3")
    os.environ.setdefault("TELNYX_WEBHOOK_DB",
                          str(Path(__file__).resolve().parent.parent
                              / "logs" / "webhook.db"))
    import uvicorn
    uvicorn.run("live_server:app", host="127.0.0.1", port=8001,
                log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
