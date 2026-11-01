# Architecture (project docs, concatenated)

--- docs/ARCHITECTURE.md ---

# Nathan AI Calling — self-hosted, end-to-end (scaffold, Oct 27, 2026)

Status: SCAFFOLD ONLY. No accounts created, no numbers bought, no keys stored, no live calls made.
Design owner: Muse (Nathan's assistant). In-call voice engine: a fast streaming voice pipeline, NOT the Muse chat loop itself.

## The one hard line

The system will sound natural — natural voice, pauses, barge-in, short sentences — but it will NOT pretend to be a human, and it will NOT lie if asked whether it is AI.

Opening line (natural, not robotic):
> "Hi, this is Nathan's assistant calling on his behalf about [objective]. Is now an okay time to talk through that?"

That does three things without a clunky "I am an artificial intelligence" lead-in:
1. It does not claim to be Nathan.
2. It does not claim to be a human employee.
3. It gives the other side a clean out — if they don't want to talk to an assistant, they can say so and the call ends.

If asked directly "are you a person / are you AI?", the script answer is:
> "I'm Nathan's AI assistant, calling for him. I can keep going, or I can bring Nathan in if you'd prefer to talk to him directly."

Why this line is not negotiable in this build:
- Florida is an all-party-consent state for recording (Fla. Stat. § 934.03). Every call we record/transcribe must disclose recording and get continued consent.
- The FCC treats AI-generated voices as "artificial" under the TCPA. Artificial-voice calls need disclosure and an easy human handoff.
- A business that discovers it was tricked into thinking it talked to a human is far more likely to hang up, refuse the outcome, or flag the number than one that heard a natural assistant intro and stayed on.
- This is not legal advice. Wording should be reviewed if the system ever calls outside personal errands.

## Voice choice (Nathan, Oct 27, 2026 — approved)

- Feminine voice, warm and slightly sultry in tone — the direction Nathan described as "like Megan Fox or Madison Beer" in vibe, NOT a clone or impersonation of either person.
- No voice cloning of real people: providers prohibit cloning a real person's voice without consent, and a clone would read as impersonation on a business call. The build uses a stock synthetic voice (Cartesia / ElevenLabs / Deepgram Aura) tuned in that direction: feminine, warm, confident, slightly slower pacing, mid-low pitch, natural breaths/pauses.
- Final voice is picked by listening to 2–3 stock candidates on a test call to Nathan's own phone, not by name-matching a celebrity.
- Voice settings live in `server/voice_config.json` and are swappable without changing the pipeline.

What "sound human" means here, in practice:
- Short, spoken sentences, not paragraphs read aloud.
- Natural pacing, filler-free but not stilted, interruption handling (barge-in).
- No "as an AI language model" phrasing, no menu-style recitation, no repeating the whole objective every turn.
- Voice choice tuned for warmth/clarity, not for impersonating Nathan or any real person.

## How "driven by you" actually works (latency honesty)

Muse's chat loop is too slow for real-time phone audio. So the split is:

- **Muse (this assistant) = the brain before and during the call, at job level:**
  - Takes Nathan's request ("call Portfolio, ask X, accept Y, don't accept Z").
  - Writes the call job: number, objective, allowed decisions, forbidden topics, max duration, loop-in triggers.
  - Monitors the live transcript stream and can intervene / loop Nathan in.
  - Reports back with transcript, outcome, commitments, and follow-ups.
- **In-call voice pipeline = the mouth and ears, turn by turn:**
  - Streaming STT -> fast streaming LLM -> streaming TTS, with voice-activity detection.
  - Target round trip ~700-900ms. That is what makes it feel realtime.
  - It does NOT freelance beyond the job Muse wrote. Anything outside the job = loop-in or end call, not improvisation.

That is the only way to get both "realtime" and "driven by Muse" without lying about latency.

## Architecture (self-hosted, self-developed)

```
Nathan / Muse (chat)
  -> call job JSON (approved by Nathan, per call at first)
  -> ai-calling server (FastAPI, your server / VPS)
      -> Pipecat pipeline (open source, Python)
          -> Transport: Twilio or Telnyx WebSocket / SIP media stream
          -> STT: Deepgram (streaming) — swappable
          -> LLM: fast streaming model — swappable
          -> TTS: Cartesia / ElevenLabs / Deepgram Aura — swappable
          -> VAD + interruption handling (Pipecat built-in)
      -> Transcript log (SQLite, local)
      -> Wallet ledger (SQLite, local, prepaid only)
      -> Loop-in bridge: dial Nathan's phone, private briefing, then bridge or hand off
  -> Muse polls / webhook: transcript, outcome, cost
```

Why Pipecat:
- Open source, Python, built for exactly this: audio in -> STT -> LLM -> TTS -> audio out, with interruption handling.
- Twilio transport is documented: TwiML Bin -> WebSocket stream to your server; outbound via Twilio REST + media stream.
- Services are swappable, so no vendor lock-in on voice/model.

Why NOT a hosted voice platform in this build:
- Nathan asked for self-hosted, self-developed, end-to-end. Vapi/Retell would be faster to ship, but they are hosted control planes. This scaffold keeps the control plane on hardware Nathan controls.
- Trade-off, stated plainly: more work, more maintenance, and Nathan owns uptime. If the server is down, calls don't run.

## Hosting reality

- Prototype: this Muse VM + a tunnel (like the sms-relay tunl setup) is fine for a test call to Nathan's own phone.
- Real use: an always-on server with a public, stable WebSocket/SIP endpoint — a small VPS, or a home server with port forwarding and a stable domain. A laptop that sleeps will drop calls.
- One concurrent call at first. Scale later only if needed.

## Phone number

- Nathan said: his number OR another number, he doesn't care.
- Build default: a NEW dedicated number from Twilio/Telnyx for the AI caller.
  - Keeps his personal number clean, avoids porting, makes callbacks routable (callback -> loop-in or voicemail -> transcript to Nathan).
  - Recipient sees a real number they can call back; the callback goes to the AI number, not to Nathan's personal phone, unless looped in.
- His personal number as caller ID is NOT the default in this build, because carrier verification/porting for a personal cell is a separate, messy step and risks his own service. Revisit only if he explicitly wants it after the dedicated number works.

## Wallet — "nothing serious" version

- Local prepaid ledger, NOT a bank connection.
- How it works:
  - Nathan tops up provider balances manually (Twilio/Telnyx/STT/TTS/LLM) with a card, in small amounts.
  - The local ledger records estimated cost per call (telephony minutes + STT + LLM tokens + TTS characters) and stops new calls at a cap.
  - Default caps for the build: $20/mo local cap to start, $5/call soft cap, alert at 80% of monthly cap.
  - No auto-drain from a bank account. No auto-recharge from a bank account. Manual top-up is the guardrail.
  - If he later wants provider-side auto-recharge, that is a separate, explicit decision on the provider's dashboard, on a low-limit card — not wired into this server.
- "Wallet" in Muse (Shop Pay / Stripe Link) is unrelated and is NOT used here.

## Loop-in: only call Nathan in if needed

Triggers (any one = pause the agent, brief Nathan, bridge or hand off):
- The other side asks for a human, asks "are you AI?", or refuses to talk to an assistant.
- A decision outside the approved job (price, date, payment, legal wording, anything binding).
- Sensitive data requested: SSN, card number, bank details, one-time code, password, background-check consent, legal attestation signature.
- Confusion / repeated misunderstanding (2 failed clarifications on the same point).
- The call is going over max duration, or the other side is upset / escalating.
- Voicemail / phone tree that won't resolve: hang up, do NOT leave an AI voicemail with details, report back.

Loop-in mechanics:
- Agent says: "Let me bring Nathan in — one moment." (No details leaked while bridging.)
- Server dials Nathan, plays a private 10-second briefing (who, objective, where the call stands), then bridges only if Nathan accepts.
- If Nathan doesn't answer, agent ends politely and reports back with the exact stopping point.

## Guardrails (baked into every job)

- Allowed: ask questions, check status, book/reschedule/cancel within approved bounds, get quotes, confirm details, read back confirmation numbers.
- Forbidden on the call, always: SSN, card/bank numbers, one-time codes, passwords, background-check consent, legal-attestation signatures, payments without Nathan's explicit per-call approval, medical/legal advice beyond the job.
- No bulk, no cold outreach, no marketing calls. One user-directed call at a time.
- US/Canada numbers only at first. No emergency services, no premium-rate numbers.
- Every call: transcript + outcome + cost logged locally. Nothing is sent to a third party except the providers needed to run the call itself (carrier, STT, LLM, TTS).
- First live calls: Nathan approves the exact job (number + objective + bounds) before dialing. Full autonomy (no per-call approval) is a later decision, after test calls prove out — not day one.

## Call job format (what Muse writes, Nathan approves)

```json
{
  "to": "+1XXXXXXXXXX",
  "business": "Portfolio / GAP claims",
  "objective": "Ask for GAP claim status, confirm packet completeness, ask about any 150%-of-ACV cap",
  "allowed_decisions": ["accept a callback time", "confirm contact details already on file"],
  "forbidden": ["SSN", "card numbers", "payment authorisation", "signing anything"],
  "max_minutes": 10,
  "loop_in_triggers": ["human requested", "decision outside job", "sensitive data requested"],
  "opening": "Hi, this is Nathan's assistant calling on his behalf about his GAP claim status. Is now an okay time?"
}
```

## Build order

1. Scaffold (this doc + code skeleton) — done, no spend.
2. Choose STT/LLM/TTS providers and get API keys via Secure Vault (Nathan's step).
3. Twilio or Telnyx account + dedicated number (Nathan's step).
4. Server deploy + tunnel/VPS endpoint.
5. Test call to Nathan's own phone. Tune voice, pacing, barge-in, disclosure line.
6. First real call, with Nathan approving the exact job, listening to transcript after.
7. Only then: loosen approval (e.g., pre-approved job types), raise caps, add calendar/CRM hooks if wanted.

## What still needs Nathan

- Provider choice confirmation (Twilio vs Telnyx; STT/TTS/LLM defaults in scaffold are Deepgram + fast LLM + Cartesia, swappable).
- A server to run it on (VPS vs home server).
- Account creation, number purchase, API keys via Secure Vault — never in chat.
- Approval of the test call and of the disclosure wording above.
- Monthly cap confirmation (scaffold default $20/mo).

--- docs/LIVE_PIPELINE_2026-10-29.md ---

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
