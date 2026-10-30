# Server skeleton — NOTES

Date: 2026-10-27. No accounts created, no numbers bought, no money spent,
no keys/secrets stored. This file, `server.py`, and `requirements.txt` are
the only new files in this pass (plus the pre-existing `call_job.py`,
`voice_config.json`, `README.md` in this folder).

## What is implemented (and testable without any credentials)

- `POST /calls` — validates a CallJob and checks the wallet, exactly per
  `docs/ARCHITECTURE.md`:
  - Destination must be +1 US/Canada E.164; emergency/premium destinations blocked.
  - `business` and `objective` non-empty; `max_minutes` 1–60.
  - One concurrent call (in-memory gate; the SQLite DB is the durable record).
  - Wallet: $20/month cap, $5/call soft cap, 80% alert flag. Estimated cost
    uses a placeholder $0.15/min (see "Cost estimate" below).
  - The disclosure opening is FORCED to the architecture template
    ("Hi, this is Nathan's assistant calling on his behalf about …").
    A job cannot substitute an opening that drops "Nathan's assistant".
  - Returns `queued-not-dialled` — a queued job never dials by itself.
- `GET /calls/{id}` — returns the queued record (polling hook for Muse).
- `GET /health` — reports skeleton status and whether Pipecat imported.
- `POST /calls/{call_id}/loop-in` — validates the trigger against the
  architecture's loop-in list, records the request, and returns
  `not_configured` (no Nathan number set) or `stubbed-awaiting-carrier`.
  It makes NO phone call in this skeleton.
- `call_job.py` (pre-existing, reused unchanged) — CallJob schema,
  disclosure template, AI-identity answer, forbidden topics, loop-in
  triggers, SQLite wallet/calls tables.
- `voice_config.json` (pre-existing, reused) — feminine, warm, slightly
  sultry stock-voice direction. Hard rule kept in code: NO clone of
  Megan Fox, Madison Beer, or any real person; no impersonating Nathan.

## What is stubbed / outlined, NOT live

- **Outbound dialling.** `POST /calls` queues only. The real step is a
  Twilio REST `calls.create` with a TwiML Bin pointing at
  `wss://<public-host>/twilio/media`, passing the queued call id as a
  custom stream parameter so the media handler can attach the right job.
  Not wired: no Twilio account, SID, token, or from-number exists yet.
- **`/twilio/media` Pipecat pipeline.** The handler shape follows
  Pipecat's documented Twilio pattern (accept socket, parse
  connected/start handshake, `TwilioFrameSerializer`, `FastAPIWebsocketTransport`
  with audio in/out, `add_wav_header=False`, then
  transport -> STT -> LLM -> TTS -> transport). The pipeline body in
  `run_pipeline()` is an outline: it is guarded behind
  `PIPELINE_AVAILABLE`, and if Pipecat is not installed the socket closes
  with code 1011 and a "install requirements.txt first" reason.
- **Loop-in bridging.** Real behaviour: agent says "Let me bring Nathan
  in — one moment.", server dials `AI_CALLING_NATHAN_NUMBER`, plays a
  private ~10s briefing, bridges only on Nathan's accept. Not wired:
  no Nathan number is set in this repo (env var is empty by default),
  and no carrier bridge code exists yet.
- **Job-to-stream attachment.** The skeleton's media handler builds a
  placeholder `UNATTACHED-STREAM` job because no call has been dialled.
  Before the first live call, attach the queued CallJob by the Twilio
  call/stream id passed in the TwiML `<Stream>` custom parameters.
- **Transcript persistence / cost metering.** The DB tables exist, but
  live transcripts and measured provider costs are not yet written by the
  pipeline. Cost estimate is a placeholder (below).

## What still needs real credentials (Nathan's steps, never in chat)

1. Twilio OR Telnyx account + a NEW dedicated AI number (not Nathan's
   personal number; no porting). Needs: account SID, auth token,
   from-number, and a TwiML Bin pointing at the public WebSocket URL.
2. Deepgram API key (STT), OpenAI API key (fast LLM), Cartesia API key
   + a stock feminine voice id chosen by ear on a test call.
   Keys go via environment / Secure Vault surrogate only — never in
   this repo, never in chat, never in logs.
3. `AI_CALLING_NATHAN_NUMBER` for loop-in, set at deploy time.
4. A server to run it on: always-on VPS or home server with a public,
   stable WebSocket endpoint. Prototype tunnel (ngrok/tunl) is fine for
   the test call to Nathan's own phone only.
5. Nathan's approval of the exact first test-call job and of the
   disclosure wording, before any real business call.

## Cost estimate — placeholder, not a quote

`ESTIMATED_COST_PER_MINUTE_USD = 0.15` in `server.py` is a conservative
placeholder for telephony + STT + LLM + TTS combined. Replace it with
measured per-minute costs from the first test calls (Twilio per-minute
rate + Deepgram + OpenAI tokens + Cartesia characters) before relying
on the $20/mo cap as a real spend control. The cap logic itself
(reject over cap, reject over $5/call estimate, flag at 80%) is
implemented; the unit price feeding it is not yet measured.

## Versions — verified vs not verified

Verified from PyPI/docs on 2026-10-27 (see requirements.txt comments):
- `pipecat-ai==1.8.0` (PyPI release page, 2026-08-26 wheel).
- `fastapi==0.158.2` (PyPI release page, 2026-10-21 wheel).
- Pipecat provider extras `deepgram`, `openai`, `cartesia`, `silero`
  (Pipecat's own services table).

NOT verified — must be checked on the deploy machine before first live run:
- Pipecat 1.8.0 module paths and constructor signatures used in
  `run_pipeline()` / `/twilio/media` (`Pipeline`, `PipelineRunner`,
  `PipelineTask`, `PipelineParams`, `LLMContext`, `LLMAggregator`,
  `LLMUserAggregator`, `parse_telephony_websocket`,
  `TwilioFrameSerializer`, `FastAPIWebsocketTransport`,
  `DeepgramSTTService`, `OpenAILLMService`, `CartesiaTTSService`).
  Pipecat's own docs and secondary sources note these paths/arguments
  have moved between releases (1.10/1.12 mentions surfaced in search),
  and Pipecat is NOT installed in this VM, so imports could not be
  verified here. `py_compile` passes (syntax only) — it does NOT prove
  the Pipecat imports resolve.
- `uvicorn` exact version (left unpinned deliberately; pin on deploy).
- `twilio` stable version (search surfaced only dev/rc versions, so
  no pin was guessed; the skeleton imports no twilio package at all).
- The LLM model name `gpt-4o-mini` in `run_pipeline()` is a placeholder
  fast-model choice, not a verified provider recommendation for this
  pipeline; confirm model + streaming support at wiring time.
- `CARTESIA_VOICE_ID` — no voice has been selected yet. Pick 2–3 stock
  feminine voices by ear on the test call, per voice_config.json.

## Compile check (this pass)

- `python3 -m py_compile server/server.py server/call_job.py` — PASSED.
- FastAPI and Pipecat are NOT installed in this VM, so the server was
  NOT run and no endpoint was exercised live. The first run must be:
  `pip install -r requirements.txt` on the deploy machine, then
  `uvicorn server:app --host 0.0.0.0 --port 8000`, then `GET /health`
  (which reports `pipeline_available` honestly) before any call job.
