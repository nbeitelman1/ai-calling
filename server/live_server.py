"""Live server: Telnyx webhook POSTs + media websocket on one FastAPI app.

Replaces the stdlib webhook-only server (run_webhook_server.py) on port
8001 so the tunnel forwarding (80 -> 127.0.0.1:8001) needs no change.

Routes:
  GET  /healthz                          -> {"status": "ok", "mode": ...}
  POST /telnyx/webhooks                  -> canonical adapter ingest (ack = stored)
  POST /telnyx/webhooks-failover         -> same handler, same rules
  WS   /telnyx/media/{attempt_id}        -> handle_media_websocket() from
       live_pipeline.py, ONLY when the parent dial step has registered a
       job for that attempt id. Unregistered attempts are closed 1011
       with no provider call. No route dials: dialing stays with the
       parent through the canonical adapter.

Credential values are NEVER read here: the webhook public key comes
from env TELNYX_WEBHOOK_PUBLIC_KEY (public by design), and pipeline
credential refs are env-var names resolved at call-build time by
live_pipeline._resolve_ref. The launcher (start_live_server.py)
resolves vault surrogates into process memory for those env names;
nothing is written to files or logs.

Media jobs are registered by the parent dial step via
register_media_job() before dialing, or over loopback via
POST /internal/register-media-job (see below). That endpoint is
token-gated and returns 404 for any request without the correct
token, so its existence is not revealed to anyone else. The public
webhook routes never register jobs.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from loguru import logger

from telnyx_adapter import AdapterConfig, TelnyxAdapter
from telnyx_webhook import WEBHOOK_PATHS, adapter_event_data

PUBLIC_KEY = os.environ.get("TELNYX_WEBHOOK_PUBLIC_KEY", "").strip()
MODE = "telnyx-key" if PUBLIC_KEY else "test-key-only"
if not PUBLIC_KEY:
    # Fail closed: without the real Telnyx key, signed events cannot
    # verify. Refuse to serve webhooks rather than 401ing real events.
    raise RuntimeError("TELNYX_WEBHOOK_PUBLIC_KEY is not set; refusing to start")

DB_PATH = os.environ.get(
    "TELNYX_WEBHOOK_DB",
    str(Path(__file__).resolve().parent.parent / "logs" / "webhook.db"),
)
adapter = TelnyxAdapter(DB_PATH, PUBLIC_KEY, AdapterConfig())

app = FastAPI(title="Nathan AI Calling (live)")

# Compatibility mirror for tests and old in-process callers. Durable state is
# authoritative in the adapter's media_jobs table.
JOB_REGISTRY: dict = {}
JOB_TTL_SECONDS = 300
LOG_DIR = Path(os.environ.get(
    "AI_CALLING_LOG_DIR",
    str(Path(__file__).resolve().parent.parent / "logs"),
)).resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)


def register_media_job(attempt_id, job, refs, expected_route, log_path,
                       quota_reservations=None):
    adapter.release_expired_media_job_quotas(time.time())
    if attempt_id in JOB_REGISTRY:
        raise ValueError("attempt is already registered")
    name = Path(log_path).name
    if name != log_path or not name.endswith(".jsonl"):
        raise ValueError("log_path must be a .jsonl filename")
    resolved = (LOG_DIR / name).resolve()
    if resolved.parent != LOG_DIR:
        raise ValueError("log path escapes log directory")
    payload = {"job": asdict(job), "refs": asdict(refs),
               "route": list(expected_route), "log_name": name}
    adapter.register_media_job(attempt_id, payload,
                               expires_at=time.time() + JOB_TTL_SECONDS,
                               quota_reservations=quota_reservations)
    JOB_REGISTRY[attempt_id] = True


def call_quota_reservations(job):
    period = time.strftime("%Y-%m-%d", time.gmtime())
    try:
        limits = {
            "groq_audio_seconds": float(os.environ["GROQ_AUDIO_SECONDS_DAILY_LIMIT"]),
            "gemini_requests": float(os.environ["GEMINI_REQUESTS_DAILY_LIMIT"]),
            "elevenlabs_characters": float(os.environ["ELEVENLABS_CHARACTERS_MONTHLY_LIMIT"]),
        }
    except (KeyError, ValueError) as exc:
        raise ValueError("provider quota limits are not configured") from exc
    month = period[:7]
    return [
        ("groq_audio_seconds", period, job.max_minutes * 60,
         limits["groq_audio_seconds"]),
        ("gemini_requests", period, 50, limits["gemini_requests"]),
        ("elevenlabs_characters", month,
         int(os.environ.get("AI_CALLING_TTS_CHAR_LIMIT", "4000")),
         limits["elevenlabs_characters"]),
    ]


def claim_media_job(attempt_id):
    payload = adapter.claim_media_job(attempt_id, now=time.time())
    JOB_REGISTRY.pop(attempt_id, None)
    if payload is None:
        return None
    from live_pipeline import LiveCallJob, LiveCredentialRefs
    return (LiveCallJob(**payload["job"]), LiveCredentialRefs(**payload["refs"]),
            tuple(payload["route"]), str(LOG_DIR / payload["log_name"]))


def peek_media_job(attempt_id):
    payload = adapter.peek_media_job(attempt_id, now=time.time())
    if payload is None:
        return None
    from live_pipeline import LiveCallJob, LiveCredentialRefs
    return (LiveCallJob(**payload["job"]), LiveCredentialRefs(**payload["refs"]),
            tuple(payload["route"]), str(LOG_DIR / payload["log_name"]))


async def _read_limited(request: Request, limit: int = 65536) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > limit:
        raise ValueError("body too large")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise ValueError("body too large")
    return bytes(body)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "mode": MODE}


def readiness_status() -> dict:
    failures = []
    public_host = os.environ.get("AI_CALLING_PUBLIC_HOST", "").strip()
    if (not public_host or public_host.endswith(".lhr.life")
            or "/" in public_host):
        failures.append("public_host")
    for name in ("GROQ_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY",
                 "TELNYX_API_KEY"):
        if not os.environ.get(name, "").strip():
            failures.append(name.lower())
    for pool in ("GROQ_STT_MODELS", "GEMINI_FAST_MODELS",
                 "GEMINI_FRONTIER_MODELS"):
        if not os.environ.get(pool, "").strip():
            failures.append(pool.lower())
    for quota in ("GROQ_AUDIO_SECONDS_DAILY_LIMIT",
                  "GEMINI_REQUESTS_DAILY_LIMIT",
                  "ELEVENLABS_CHARACTERS_MONTHLY_LIMIT"):
        try:
            if float(os.environ.get(quota, "0")) <= 0:
                failures.append(quota.lower())
        except ValueError:
            failures.append(quota.lower())
    try:
        from provider_config import load_preflight_receipt
        load_preflight_receipt()
    except Exception:
        failures.append("provider_preflight")
    try:
        import nltk.data
        nltk.data.find('tokenizers/punkt_tab/english/')
    except (LookupError, ImportError):
        failures.append('nltk_punkt_tab')
    try:
        from live_pipeline import DISCLOSURE_OPENING, FixedPhraseCache
        from model_select import REFUSAL_LINE
        from provider_config import tts_configuration
        tts = tts_configuration()
        voice, model, rate = tts['voice'], tts['model'], tts['sample_rate']
        cache = FixedPhraseCache(
            os.environ.get("AI_CALLING_PHRASE_CACHE", str(LOG_DIR / "phrase-cache")),
            namespace=f"{voice}/{model}/pcm_{rate}", sample_rate=rate)
        phrases = (DISCLOSURE_OPENING, "Are you still there?",
                   "I haven't heard anything, so I'll end the call now. Goodbye.",
                   "Nathan will need to take this over. I'll end the call now.", REFUSAL_LINE)
        if any(cache.load(phrase) is None for phrase in phrases):
            failures.append("phrase_cache")
    except Exception:
        failures.append("phrase_cache")
    try:
        with adapter._tx() as conn:
            conn.execute("SELECT 1").fetchone()
        if not LOG_DIR.is_dir() or not os.access(LOG_DIR, os.W_OK):
            raise OSError("log directory is not writable")
    except Exception:
        failures.append("local_storage")
    return {"ready": not failures, "failures": sorted(set(failures))}


@app.get("/readyz")
async def readyz():
    status = readiness_status()
    return JSONResponse(status, status_code=200 if status["ready"] else 503)


_JOB_FIELDS = {"to", "business", "objective", "allowed_decisions",
               "max_minutes", "test_call", "opening", "pronunciation_hints"}
_REF_FIELDS = {"groq_api_key_ref", "gemini_api_key_ref", "elevenlabs_api_key_ref",
               "telnyx_api_key_ref", "elevenlabs_voice_id"}


@app.post("/internal/register-media-job")
async def register_media_job_http(request: Request):
    """Loopback-only job registration for the parent dial step.

    The server sits behind a public tunnel, so loopback appearance
    alone is NOT sufficient: the request must carry header
    X-Internal-Token equal to env INTERNAL_TOKEN. Any request
    without the correct token gets 404 (not 401), so the endpoint's
    existence is not revealed. With no INTERNAL_TOKEN configured,
    every request gets 404 (fail closed).

    Body (JSON): attempt_id (str), job (LiveCallJob fields: to,
    business, objective, plus optional allowed_decisions,
    max_minutes, test_call, opening), refs (LiveCredentialRefs
    fields, all optional — env-var names only, never values),
    expected_route (exactly 3 strings: connection_id, from, to),
    log_path (str). Unknown job/ref fields are rejected with 400.
    """
    token = os.environ.get("INTERNAL_TOKEN", "").strip()
    supplied = request.headers.get("X-Internal-Token", "")
    if not token or not hmac.compare_digest(supplied, token):
        return JSONResponse({"detail": "not found"}, status_code=404)
    try:
        from telnyx_adapter.wire import parse_json_bytes
        body = parse_json_bytes(await _read_limited(request))
    except Exception:
        return JSONResponse({"detail": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "object required"}, status_code=400)
    attempt_id = body.get("attempt_id")
    job_data = body.get("job")
    refs_data = body.get("refs") or {}
    route = body.get("expected_route")
    log_path = body.get("log_path")
    if not attempt_id or not isinstance(attempt_id, str):
        return JSONResponse({"detail": "attempt_id required"}, status_code=400)
    if not isinstance(job_data, dict):
        return JSONResponse({"detail": "job object required"}, status_code=400)
    if not isinstance(refs_data, dict):
        return JSONResponse({"detail": "refs object required"}, status_code=400)
    if set(job_data) - _JOB_FIELDS:
        return JSONResponse({"detail": "unknown job field"}, status_code=400)
    if set(refs_data) - _REF_FIELDS:
        return JSONResponse({"detail": "unknown refs field"}, status_code=400)
    if (not isinstance(route, list) or len(route) != 3
            or not all(isinstance(x, str) for x in route)):
        return JSONResponse(
            {"detail": "expected_route must be 3 strings"}, status_code=400)
    if not log_path or not isinstance(log_path, str):
        return JSONResponse({"detail": "log_path required"}, status_code=400)
    try:
        from live_pipeline import LiveCallJob, LiveCredentialRefs
        job = LiveCallJob(**job_data)
        refs = LiveCredentialRefs(**refs_data)
    except Exception as exc:
        return JSONResponse({"detail": f"invalid job/refs: {exc}"[:200]},
                            status_code=400)
    try:
        register_media_job(
            attempt_id, job, refs, tuple(route), log_path,
            quota_reservations=call_quota_reservations(job))
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)[:200]}, status_code=400)
    return {"registered": True, "attempt_id": attempt_id}


def _ingest(request: Request, raw: bytes):
    result = adapter.ingest_webhook(raw, request.headers)
    outcome = None
    # Processing is idempotent. A duplicate delivery must retry a stored but
    # unprocessed event rather than leave it stranded after an earlier error.
    try:
        data = json.loads(adapter_event_data(adapter, raw))
        outcome = adapter.process_event(data["id"])
    except Exception:
        outcome = "processing_deferred_operator_review"
    return {"accepted": True,
            "duplicate": result.get("duplicate", False),
            "outcome": outcome}


@app.post("/telnyx/webhooks")
async def webhook_primary(request: Request):
    try:
        raw = await _read_limited(request, adapter.config.webhook_body_cap_bytes)
        return _ingest(request, raw)
    except Exception:
        return JSONResponse({"accepted": False, "error": "request rejected"},
                            status_code=400)


@app.post("/telnyx/webhooks-failover")
async def webhook_failover(request: Request):
    try:
        raw = await _read_limited(request, adapter.config.webhook_body_cap_bytes)
        return _ingest(request, raw)
    except Exception:
        return JSONResponse({"accepted": False, "error": "request rejected"},
                            status_code=400)


@app.websocket("/telnyx/media/{attempt_id}")
async def media_socket(websocket: WebSocket, attempt_id: str):
    from media_auth import verify_media_upgrade
    try:
        verify_media_upgrade(
            websocket.headers, PUBLIC_KEY,
            os.environ.get("AI_CALLING_PUBLIC_HOST", ""), attempt_id,
            query=websocket.scope.get("query_string", b""))
    except (ValueError, TypeError) as exc:
        # Reject before accept/claim/provider work. Route IDs are correlation,
        # not authentication. There is deliberately no unsigned debug bypass.
        logger.warning("media upgrade rejected attempt={} type={}",
                       attempt_id, type(exc).__name__)
        await websocket.close(code=1008, reason="media authentication failed")
        return
    logger.info("media-stage signature_verified attempt={}", attempt_id)
    await websocket.accept()
    entry = peek_media_job(attempt_id)
    if entry is None:
        await websocket.close(code=1011, reason="no job registered")
        return
    job, refs, expected_route, log_path = entry
    try:
        from live_pipeline import handle_media_websocket
        await handle_media_websocket(
            websocket, attempt_id, job, refs, adapter,
            expected_route, log_path)
    except Exception as exc:
        # Signature and durable registration already passed. A pre-build
        # exception (bad config / missing start frame) must not strand a call.
        from live_pipeline import _hangup_via_adapter, _resolve_ref
        from model_select import CostLedger
        reason = f"handler_error:{type(exc).__name__}"
        logger.error("media handler failed attempt={} type={}", attempt_id, type(exc).__name__)
        acknowledged = False
        try:
            acknowledged = await _hangup_via_adapter(
                adapter, attempt_id, _resolve_ref(refs.telnyx_api_key_ref), reason)
        except Exception:
            logger.error("media handler emergency hangup failed attempt={}", attempt_id)
        try:
            CostLedger(test_call=job.test_call).record(log_path, {
                "attempt_id": attempt_id, "outcome": reason,
                "hangup_acknowledged": acknowledged,
                "hangup_required": not acknowledged, "transcript": []})
        except Exception:
            logger.error("media handler emergency record failed attempt={}", attempt_id)
        try:
            await websocket.close(code=1011, reason="media handler error")
        except Exception:
            pass
    finally:
        JOB_REGISTRY.pop(attempt_id, None)
