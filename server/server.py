"""Nathan's self-hosted AI calling server — SKELETON ONLY.

NOTE (Oct 29, 2026): the Twilio/Deepgram run_pipeline() in this file is
SUPERSEDED by live_pipeline.py (Telnyx media + OpenAI STT + ElevenLabs
TTS). Do not use run_pipeline() for the test call; it is kept only so
the old skeleton still imports.

No live calls are made by this file as shipped:
- The Pipecat pipeline is guarded behind ``PIPELINE_AVAILABLE``. If Pipecat
  (or its provider extras) is not installed, /twilio/media closes with a
  clear "not installed" message instead of dialling anything.
- Outbound dialling and loop-in bridging are STUBBED: they return the exact
  steps that would run, and refuse to run without credentials supplied via
  environment variables (never in this file, never in chat, never in git).
- No accounts, numbers, or keys are created, bought, or stored here.

Rules baked in, from docs/ARCHITECTURE.md and call_job.py (do not weaken):
- Disclosure opening, always: "Hi, this is Nathan's assistant calling on
  his behalf about <objective>. Is now an okay time to talk through that?"
- If asked "are you a person / are you AI?": "I'm Nathan's AI assistant,
  calling for him. I can keep going, or I can bring Nathan in if you'd
  prefer to talk to him directly."
- Voice: feminine, warm, slightly sultry in vibe — a stock synthetic
  voice, NEVER a clone of Megan Fox, Madison Beer, or any real person.
  No impersonating Nathan or any real employee.
- Forbidden on every call: SSN, card/bank numbers, one-time codes,
  passwords, background-check consent, legal-attestation signatures,
  payment authorisation without Nathan's explicit per-call approval.
- Loop-in triggers: human requested, asked if AI, refused to talk to an
  assistant, decision outside the job, sensitive data requested, repeated
  misunderstanding, max duration reached, caller upset/escalating,
  voicemail or unresolvable phone tree.
- Wallet: $20/month local cap, $5/call soft cap, alert at 80% of the
  monthly cap. Prepaid / manual top-up only — NO bank auto-recharge.
- One concurrent call at first. US/Canada (+1) numbers only. No emergency
  services, no premium-rate numbers, no bulk / cold / marketing calls.

Run (after installing requirements.txt and supplying credentials):
    uvicorn server:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from call_job import (
    AI_IDENTITY_ANSWER,
    FORBIDDEN_TOPICS,
    LOOP_IN_TRIGGERS,
    OPENING_TEMPLATE,
    CallJob,
    init_db,
)

# ---------------------------------------------------------------------------
# Configuration — environment only, never hard-coded secrets.
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("AI_CALLING_DB", str(HERE / "ai_calling.db")))
VOICE_CONFIG_PATH = Path(
    os.environ.get("AI_CALLING_VOICE_CONFIG", str(HERE / "voice_config.json"))
)
# Nathan's number for loop-in. Deliberately NOT filled in here: the parent
# agent supplies it at deploy time (env AI_CALLING_NATHAN_NUMBER), and
# loop-in refuses to run until it is set.
NATHAN_NUMBER = os.environ.get("AI_CALLING_NATHAN_NUMBER", "")

MONTHLY_CAP_USD = 20.0
CALL_SOFT_CAP_USD = 5.0
ALERT_AT_FRACTION = 0.80
# Placeholder estimate until real provider metering lands. It is deliberately
# conservative and is replaced by measured STT+LLM+TTS+telephony costs in
# the live pipeline (see NOTES.md). It is NOT a provider quote.
ESTIMATED_COST_PER_MINUTE_USD = 0.15

BLOCKED_DESTINATIONS = {"+1911", "+911", "+112", "+1900"}

# ---------------------------------------------------------------------------
# Pipecat — guarded import. The skeleton must compile and import-test
# without Pipecat installed; the live pipeline is wired in run_pipeline().
# Pipecat's own docs note module paths / constructor arguments have moved
# between releases, so re-verify them against the INSTALLED version before
# the first live call (see NOTES.md).
# ---------------------------------------------------------------------------
try:  # pragma: no cover - exercised only when Pipecat is installed
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response import (
        LLMFullResponseAggregator,
    )
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMUserAggregator,
    )
    from pipecat.runner.utils import parse_telephony_websocket
    from pipecat.serializers.twilio import TwilioFrameSerializer
    from pipecat.services.cartesia.tts import CartesiaTTSService
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.openai.llm import OpenAILLMService
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketParams,
        FastAPIWebsocketTransport,
    )

    PIPELINE_AVAILABLE = True
    PIPELINE_IMPORT_ERROR = ""
except Exception as exc:  # noqa: BLE001 - skeleton must survive missing deps
    PIPELINE_AVAILABLE = False
    PIPELINE_IMPORT_ERROR = str(exc)

app = FastAPI(title="Nathan AI Calling (skeleton)")

# In-memory job registry. The SQLite DB (init_db) is the durable record;
# this dict is only the "one concurrent call" gate for the skeleton.
_active_calls: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class CallRequest(BaseModel):
    """A call job as written by Muse and approved by Nathan, per call."""

    to: str = Field(..., description="E.164 destination, +1 US/Canada only")
    business: str
    objective: str
    allowed_decisions: list[str] = Field(default_factory=list)
    max_minutes: int = 10


class LoopInRequest(BaseModel):
    trigger: str
    note: str = ""


# ---------------------------------------------------------------------------
# Validation + wallet — pure logic, no network, no credentials.
# ---------------------------------------------------------------------------
def validate_destination(to: str) -> str:
    """Return a normalised +1 destination or raise HTTP 400/403."""
    digits = re.sub(r"\D", "", to or "")
    if digits.startswith("1") and len(digits) == 11:
        normalised = f"+{digits}"
    else:
        raise HTTPException(
            status_code=400,
            detail="Destination must be a US/Canada number in E.164 form (+1XXXXXXXXXX).",
        )
    if normalised in BLOCKED_DESTINATIONS:
        raise HTTPException(
            status_code=403, detail="Emergency / premium-rate destinations are blocked."
        )
    return normalised


def build_system_prompt(job: CallJob, voice_style: dict) -> str:
    """The in-call LLM prompt. Forbidden topics and identity are fixed here,
    not left to whatever the caller says, and not overridable by a job."""
    return (
        "You are Nathan's assistant, making a phone call on his behalf.\n"
        f"Business: {job.business}\n"
        f"Objective: {job.objective}\n"
        "Allowed decisions (nothing else is binding): "
        + (", ".join(job.allowed_decisions) if job.allowed_decisions else "none — ask, do not commit")
        + "\n"
        "NEVER ask for, repeat, or act on: " + ", ".join(FORBIDDEN_TOPICS) + ".\n"
        "If any of those is requested, or if the caller asks for a human, "
        "asks if you are AI, or refuses to talk to an assistant, say "
        "'Let me bring Nathan in — one moment.' and trigger loop-in.\n"
        f"If asked directly whether you are a person or AI, answer exactly: {AI_IDENTITY_ANSWER}\n"
        "Style: short spoken sentences, natural pacing, "
        f"feminine voice, {voice_style.get('tone', 'warm, confident')}. "
        "Do not claim to be Nathan or any real person. Do not claim to be "
        "human. Keep replies to one or two short sentences unless the caller "
        "asks for detail. If you reach voicemail or an unresolvable phone "
        "tree, end the call without leaving details and report back.\n"
        "Loop-in triggers: " + ", ".join(LOOP_IN_TRIGGERS) + "."
    )


def load_voice_style() -> dict:
    try:
        data = json.loads(VOICE_CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    style = data.get("style", {}) if isinstance(data, dict) else {}
    return style if isinstance(style, dict) else {}


def check_wallet(conn: sqlite3.Connection, estimated_cost: float) -> dict:
    """Enforce the $20/mo cap and $5/call soft cap. Raises HTTP 402."""
    row = conn.execute(
        "SELECT monthly_cap_usd, spent_this_month_usd FROM wallet WHERE id = 1"
    ).fetchone()
    cap = float(row[0]) if row else MONTHLY_CAP_USD
    spent = float(row[1]) if row else 0.0
    if estimated_cost > CALL_SOFT_CAP_USD:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Estimated cost ${estimated_cost:.2f} exceeds the "
                f"${CALL_SOFT_CAP_USD:.2f} per-call soft cap. Reduce "
                "max_minutes or raise the cap explicitly with Nathan."
            ),
        )
    if spent >= cap or spent + estimated_cost > cap:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Wallet cap reached: spent ${spent:.2f} of ${cap:.2f}/month "
                f"(estimated ${estimated_cost:.2f} for this call). "
                "Top up manually; no bank auto-recharge is wired in."
            ),
        )
    return {
        "monthly_cap_usd": cap,
        "spent_this_month_usd": spent,
        "alert_at_80_percent": spent + estimated_cost
        >= cap * ALERT_AT_FRACTION,
    }


def build_job(req: CallRequest) -> CallJob:
    to = validate_destination(req.to)
    if not req.business.strip() or not req.objective.strip():
        raise HTTPException(
            status_code=400, detail="business and objective must be non-empty."
        )
    if not 1 <= req.max_minutes <= 60:
        raise HTTPException(
            status_code=400, detail="max_minutes must be between 1 and 60."
        )
    job = CallJob(
        to=to,
        business=req.business.strip(),
        objective=req.objective.strip(),
        allowed_decisions=list(req.allowed_decisions),
        max_minutes=req.max_minutes,
    )
    # The disclosure opening is not overridable: a job may not substitute
    # an opening that drops "Nathan's assistant".
    job.opening = OPENING_TEMPLATE.format(objective=job.objective)
    return job


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {
        "status": "skeleton-ok",
        "pipeline_available": PIPELINE_AVAILABLE,
        "pipeline_import_error": PIPELINE_IMPORT_ERROR or None,
        "active_calls": len(_active_calls),
    }


@app.post("/calls", status_code=201)
def create_call(req: CallRequest) -> dict:
    """Validate a job, check the wallet, queue it. Does NOT dial yet.

    Dialling is a separate, explicit step (Twilio REST, see NOTES.md) so a
    queued job can never accidentally place a call on import or restart.
    """
    if _active_calls:
        raise HTTPException(
            status_code=409,
            detail="One concurrent call only (skeleton limit). A call is already active.",
        )
    job = build_job(req)
    estimated = round(job.max_minutes * ESTIMATED_COST_PER_MINUTE_USD, 2)
    conn = init_db(DB_PATH)
    try:
        wallet = check_wallet(conn, estimated)
        call_id = uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO calls (to_number, business, objective, outcome, "
            "minutes, estimated_cost_usd) VALUES (?, ?, ?, ?, ?, ?)",
            (job.to, job.business, job.objective, "queued", job.max_minutes, estimated),
        )
        conn.commit()
    finally:
        conn.close()
    record = {
        "call_id": call_id,
        "status": "queued-not-dialled",
        "job": json.loads(job.to_json()),
        "system_prompt_preview": build_system_prompt(job, load_voice_style())[:400],
        "estimated_cost_usd": estimated,
        "wallet": wallet,
        "next_step": (
            "STUB: dial via Twilio REST with a TwiML Bin pointing at "
            "wss://<your-public-host>/twilio/media once TWILIO_ACCOUNT_SID / "
            "TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER are set. See NOTES.md."
        ),
    }
    _active_calls[call_id] = record
    return record


@app.get("/calls/{call_id}")
def get_call(call_id: str) -> dict:
    record = _active_calls.get(call_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown call id (skeleton registry only).")
    return record


@app.post("/calls/{call_id}/loop-in")
def loop_in(call_id: str, req: LoopInRequest) -> dict:
    """Loop Nathan in — STUBBED until carrier credentials are configured.

    Real behaviour (to implement, see NOTES.md):
    1. Agent says "Let me bring Nathan in — one moment." (no details leaked
       while bridging).
    2. Server dials NATHAN_NUMBER and plays a private ~10s briefing:
       who, objective, where the call stands.
    3. Bridge only if Nathan accepts; otherwise the agent ends politely
       and reports the exact stopping point.
    """
    record = _active_calls.get(call_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown call id.")
    if req.trigger not in LOOP_IN_TRIGGERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown loop-in trigger. Expected one of: {', '.join(LOOP_IN_TRIGGERS)}",
        )
    record["loop_in_requested"] = {"trigger": req.trigger, "note": req.note}
    if not NATHAN_NUMBER:
        return {
            **record,
            "loop_in": "not_configured",
            "detail": (
                "AI_CALLING_NATHAN_NUMBER is not set, so no dial was made. "
                "Set it at deploy time, then this endpoint dials Nathan, "
                "briefs him privately, and bridges only on his accept."
            ),
        }
    # Credentials for the actual bridge call are deliberately NOT read here
    # in the skeleton: see NOTES.md "what still needs real credentials".
    return {
        **record,
        "loop_in": "stubbed-awaiting-carrier",
        "detail": (
            "Nathan number is configured, but the carrier bridge call is "
            "stubbed in this skeleton. Wire Twilio REST (calls.create with "
            "a conference/bridge TwiML) here before the first live call."
        ),
    }


# ---------------------------------------------------------------------------
# Twilio media WebSocket — Pipecat pipeline outline.
# ---------------------------------------------------------------------------
async def run_pipeline(transport, job: CallJob) -> None:
    """STT -> LLM -> TTS pipeline. Implemented only when Pipecat is present.

    Pipeline order (Pipecat docs pattern):
        transport.input() -> Deepgram STT -> LLM context aggregator
        -> OpenAI LLM -> Cartesia TTS -> transport.output()
    VAD / interruption handling is Pipecat's built-in behaviour on this
    chain; the system prompt (build_system_prompt) carries the job bounds,
    disclosure line, forbidden topics, and loop-in triggers.
    """
    if not PIPELINE_AVAILABLE:  # pragma: no cover - guard, not live path
        raise RuntimeError(f"Pipecat is not installed: {PIPELINE_IMPORT_ERROR}")

    stt = DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"])
    llm = OpenAILLMService(api_key=os.environ["OPENAI_API_KEY"], model="gpt-4o-mini")
    # Voice selection: stock voice id only, chosen by ear on a test call.
    # The env var holds a provider voice id — never a cloned real person.
    tts = CartesiaTTSService(
        api_key=os.environ["CARTESIA_API_KEY"],
        voice_id=os.environ.get("CARTESIA_VOICE_ID", ""),
    )
    context = LLMContext(
        [{"role": "system", "content": build_system_prompt(job, load_voice_style())}]
    )
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            LLMFullResponseAggregator(context),
            llm,
            tts,
            transport.output(),
        ]
    )
    task = PipelineTask(pipeline, params=PipelineParams(enable_metrics=True))
    await PipelineRunner().run(task)


@app.websocket("/twilio/media")
async def twilio_media(websocket: WebSocket) -> None:
    """Twilio Media Streams endpoint.

    Twilio flow: TwiML Bin -> <Connect><Stream url="wss://host/twilio/media"/>
    -> Twilio sends "connected", then "start" (streamSid/callSid). The
    Pipecat TwilioFrameSerializer decodes mu-law 8 kHz audio into frames.
    """
    await websocket.accept()
    if not PIPELINE_AVAILABLE:
        await websocket.close(
            code=1011,
            reason="Pipecat not installed: install requirements.txt first (see NOTES.md).",
        )
        return
    try:
        # parse_telephony_websocket reads the connected/start handshake and
        # returns (transport_type, call_data) with stream_id / call_id.
        _transport_type, call_data = await parse_telephony_websocket(websocket)
        serializer = TwilioFrameSerializer(
            stream_sid=call_data["stream_id"],
            call_sid=call_data["call_id"],
            account_sid=os.environ["TWILIO_ACCOUNT_SID"],
            auth_token=os.environ["TWILIO_AUTH_TOKEN"],
        )
        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
                serializer=serializer,
            ),
        )
        # The job for this stream is matched by call_id in the live build;
        # the skeleton has no dialled call yet, so this is the wiring point
        # where the queued CallJob is attached (see NOTES.md).
        job = CallJob(
            to="+10000000000",
            business="UNATTACHED-STREAM",
            objective="attach queued job by call_id before first live call",
        )
        await run_pipeline(transport, job)
    except WebSocketDisconnect:
        return
    except KeyError as exc:
        # Missing env credential or malformed handshake: close, do not dial.
        await websocket.close(code=1011, reason=f"Missing configuration or handshake field: {exc}")
