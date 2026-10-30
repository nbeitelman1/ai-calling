# Live voice pipeline — build report (Oct 29, 2026)

Deepgram is DROPPED (Nathan, Oct 29: "screw deepgram"). STT is OpenAI
streaming transcription. No Deepgram code path remains in the live
pipeline; the only Deepgram mentions left are DROPPED notes and the
superseded skeleton import in server.py.

## Files (all under ~/workspace/ai-calling/server/)

- `telnyx_media.py` (NEW): attach gate, media session state machine,
  G.711 mu-law codec, streaming_start request builder.
- `model_select.py` (NEW): dynamic model resolution (env pools, no
  hard-pinned names), cost ledger (test $1.00 / soft $5.00 / monthly
  $20.00), outgoing-text guard.
- `live_pipeline.py` (NEW): Pipecat 1.8.0 wiring — Telnyx websocket
  transport (TelnyxFrameSerializer, PCMU 8 kHz) -> Silero VAD (barge-in)
  -> OpenAI Realtime STT (local-VAD mode) -> user tap -> context
  aggregator pair -> OpenAI LLM -> assistant aggregator -> ResponseGate
  -> ElevenLabs TTS (Natasha) -> transport output. Brain ladder
  (fast -> frontier -> Nathan) with the 3-turn / 120s bound enforced
  in code; model swap via LLMUpdateSettingsFrame.
- `voice_pipeline.py` (EDITED): Deepgram ref replaced with OpenAI STT
  ref + DROPPED note. EscalationState / guards unchanged.
- `server.py` (EDITED): docstring note only — its Twilio/Deepgram
  run_pipeline() is superseded by live_pipeline.py; code untouched.
- `voice_config.json` (EDITED): STT = OpenAI, Deepgram DROPPED note.
- `tests/test_live_pipeline.py` (NEW): 44 offline tests.

## Tests (offline, no network, no dialling, no keys)

- New suite: **44/44 PASS**. Full tree (incl. canonical adapter):
  **81/81 PASS** (unittest, deploy venv).
- Proven: attach gate (happy path, unknown/unclaimed/unbound/control
  mismatch/route mismatch, session-id-alone cannot attach); media
  session monotonicity + stop absorption + pre-start drop + barge-in
  flag; mu-law codec (identity except the inherent ±0 byte 0x7F);
  streaming_start request shape; dynamic resolver (intersection,
  fail-closed on empty pool, no baked-in model names); cost gates
  (test $1.00 / soft $5.00 / monthly $20.00); escalation (human
  request, repeated utterance, sensitive request -> frontier; 3rd
  frontier reply -> Nathan; 120s time bound without replies);
  ResponseGate (fragments released as one response; SSN response
  replaced with refusal; 3rd frontier reply replaced with loop-in
  line + EndFrame); full pipeline construction offline (services,
  VAD, transport, serializer — never started).
- Two real bugs found and fixed during this build: (1) Pipecat 1.8.0
  renamed the context aggregator (LLMContextAggregatorPair, not
  LLMFullResponseAggregator(context)) and made VADProcessor /
  LLMFullResponseAggregator keyword-only; (2) the VM's NO_PROXY
  contains bracketed IPv6 entries that httpx cannot parse at OpenAI
  client construction — build_live_pipeline() now strips bracketed
  entries around provider-client construction and restores them.

## Deliberate design trade-off (stated, not hidden)

ResponseGate buffers each full assistant response before TTS so a
forbidden shape spanning tokens is caught before speech, and so the
frontier bound counts whole replies. Cost: TTS starts after the full
response rather than streaming token-by-token, so first-audio latency
is higher than a pure streaming design. Safety + a correct bound
were chosen over that latency for the test.

## GO / NO-GO for the test call

**CONDITIONAL GO for a single supervised test call to Lauren's
number, once the pre-dial checklist below is done.** The pipeline
body is built and proven offline; it has NOT yet carried a real call.

Pre-dial checklist (parent, not this build):
1. Env pools set: OPENAI_FAST_MODELS, OPENAI_FRONTIER_MODELS,
   OPENAI_STT_MODELS (comma-separated; first offered model wins when
   an available-model list is supplied, else first pool entry).
   Credential refs (env names): OPENAI_API_KEY, ELEVENLABS_API_KEY,
   TELNYX_API_KEY — resolved via Secure Vault at deploy, never files.
2. Deploy-time cost estimate recomputed with real rates; if it
   exceeds the $1.00 test budget, the test does NOT proceed without
   fresh approval. Least certain line: OpenAI Realtime STT per-minute
   rate (ledger uses an estimate, not a quote).
3. Media websocket handler: FastAPI route wss://host/telnyx/media/
   {attempt_id} calling handle_media_websocket() with the attempt id
   from the URL path, expected route (connection_id, from, to), and
   the approved job. Streaming_start must be issued after
   call.answered with stream_url pointing at that route, track
   both_tracks, bidirectional RTP PCMU 8 kHz. The optional codec/rate
   fields in streaming_start_request() are documented defaults — the
   first live API response is their final confirmation.
4. Disclosure-first-audio confirmation is test-list criterion #1:
   the opening is queued directly at the TTS service so it is spoken
   verbatim, but whether it is literally the first audio the caller
   hears is only proven on the live run.
5. Lauren-expecting confirmation + Nathan present listening + final
   test list approval (parent's gates, unchanged).

## Remaining gaps (not papered over)

- No live call has run through this pipeline; real Telnyx media
  websocket shape, streaming_start acceptance, and OpenAI Realtime
  STT turn behaviour are unproven until the test call.
- Loop-in bridging (second Telnyx dial + bridge to Nathan) is NOT
  implemented: on the nathan stage the pipeline speaks the loop-in
  line, ends gracefully, and records loop_in_requested for the parent.
- No running-call watchdog timer (max_minutes gated at approval, not
  enforced mid-call); hold/unhold ordering unimplemented; quarantine
  has a list function, no operator UI.
- Failover webhook URL shares the prototype tunnel host (single point
  of failure); the tunnel hostname is anonymous and changes on
  reconnect (re-point the Telnyx app if it does).
- Barge-in is implemented via Silero VAD + allow_interruptions, but
  only proven at the flag level offline — the live test must exercise
  a real interruption.
- Cost rates are estimates; provider metering at call time governs.

## Pre-dial integration (Oct 29, 2026, evening)

- Live server: `server/live_server.py` (FastAPI) replaced the stdlib
  webhook-only server on port 8001, so tunnel forwarding is unchanged.
  Serves GET /healthz, POST /telnyx/webhooks + /telnyx/webhooks-failover
  (canonical adapter ingest, ack = stored), and WS
  /telnyx/media/{attempt_id} routed to handle_media_websocket().
  Unregistered attempts close 1011 with no provider call. Started by
  `server/start_live_server.py`, which resolves vault surrogates into
  process memory only and sets the model pools below.
- Model pools (verified against the account's live /v1/models list,
  132 models): fast = gpt-4o-mini (pool: gpt-4o-mini, gpt-4.1-mini,
  gpt-4o); frontier = gpt-5 (pool: gpt-5, gpt-5.1, gpt-4o); STT =
  gpt-4o-mini-transcribe (pool: gpt-4o-mini-transcribe,
  gpt-4o-transcribe). TTS model pool unset -> service default
  eleven_flash_v2_5 (Natasha voice id unchanged).
- Media route verified on the real stack (no Telnyx call): unregistered
  attempt -> 1011 "no job registered"; registered job + non-Telnyx
  messages -> 1011 "not a Telnyx media stream", outcome rejected.
- Deploy-time cost estimate for a 5-minute test call: ~$0.15 expected,
  ~$0.35 pessimistic (see parent report) — inside the $1.00 test cap.
  Telnyx balance verified $3.90 (covers the $0.035 Telnyx leg many times
  over; OpenAI/ElevenLabs legs bill to those accounts separately).
- Media jobs are registered in-process by the parent dial step via
  live_server.register_media_job() BEFORE dialing; there is no network
  API for registration, by design.
