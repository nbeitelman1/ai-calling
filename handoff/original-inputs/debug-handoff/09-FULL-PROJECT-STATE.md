# AI Calling Project — Full State Export

**Export date:** Thursday, October 29, 2026, ~5:25pm EDT (America/New_York)
**Prepared by:** Pip (Muse), Nathan's personal assistant, for handoff to Nathan's other
AI systems (large-scale coding engineering and verifier agents).
**Project home on disk:** `~/workspace/ai-calling/`
**Supersedes:** `AI_CALLING_PROJECT_EXPORT.md` (Oct 28, 2026 export in the same
directory — stale; it predates the entire Oct 29 live-call campaign).

## 0. How to use this package

This document is the single entry point. It states, in order: what the system is,
what state it is in right now, every decision that constrains future work, the
complete test history with measured evidence, the current blockers in fix order,
and a verifier checklist. The accompanying zip contains the full source tree,
tests, docs, call records, the second (unreconciled) adapter, and the
independent investigator's audit.

**Read this file first. Do not start from the Oct 28 export, and do not treat
any single doc in `docs/` as current on its own** — several were written before
later fixes and are kept for history. Where this file and an older doc disagree,
this file wins.

**Scope of this export (important):** it covers ONLY the self-hosted AI calling
build. It deliberately does NOT include Nathan's finances, medical records,
house-purchase documents, job-search materials, or message history. If those are
needed, they are a separate, explicit decision by Nathan — never bundle them
silently.

## 1. Current status (one paragraph)

**Post-export engineering update, Oct 29, 2026:** the provider ambiguity is
resolved: listening means **Groq Whisper** and thinking means **Google Gemini**.
The exported source has been migrated to Pipecat's native `GroqSTTService` and
`GoogleLLMService`; OpenAI is no longer in the live STT/LLM path. The canonical
adapter now fails closed on an empty destination allowlist, binds one approval
to one attempt, rejects negative costs, uses calendar-month reservations,
propagates job stops, includes Telnyx `time_limit_secs`, and blocks unmarked
live transports while disabled. Media registrations are expiring and single-use,
log writes are confined to the logs directory, webhook bodies are bounded,
public errors are generic, the service unit is non-root/hardened, and independent
server/frontier timeout tasks are wired. Offline verification now expects
**129/129 project tests**, **57/57 reference tests**, **3/3 mutations**, and a
28-file reference manifest. The second adapter is archived comparative evidence.
No post-export live call was placed; provider credentials, deployment, tunnel,
and live qualification remain external readiness steps requiring current values.

**Reliability/cost improvement update:** the live path now requests inbound-only
Telnyx media, rechunks decoded PCM into exact 32 ms VAD frames, uses Pipecat's
non-deprecated `PipelineWorker`, caches/prewarms five fixed Jessica phrases,
waits for terminal speech playback before canonical hangup, limits fast/frontier
reply length, applies a per-call TTS character budget, reprompts once on silence,
detects strict early-call IVR/voicemail phrases, tracks media bytes/gaps/duplicates,
persists media registrations in SQLite with authorize-before-claim, reserves and
settles provider quotas atomically, reconciles answered/hangup events, exposes
observational `/readyz`, runs no-generation provider model/voice preflight, uses
a universal hash-locked dependency file, and includes transactional Linux plus
no-dial local deployment verification. Logs are mode 0600, sensitive number
shapes are redacted before persistence, old JSONL records are pruned after 30
days, and both systemd units have resource/concurrency hardening. A fixed public
host is now mandatory; known ephemeral `.lhr.life` hosts fail readiness.

A self-hosted voice-call pipeline (Telnyx telephony + Pipecat pipeline + LLM
brain + ElevenLabs voice) is built, deployed on a VM, and has made ten live test
calls to a consenting test contact. As of the last call (Oct 29, ~5:07pm EDT),
the two hardest problems are solved with live evidence: the Telnyx media
track-filter bug is fixed and proven, and the voice-activity detector provably
reaches SPEAKING on real phone speech. The transcript is still empty, but for a
new, mundane reason: the OpenAI API account has **no credits remaining**, and
OpenAI refused the first real transcription attempt. That billing wall is the
current primary blocker. Secondary work (detector gate tuning for sustained
speech) is measured and queued behind it. A provider-direction decision was
made Oct 29, 5:20pm — "Grok for listening and Gemini for thinking" — recorded
verbatim in §4 with an open ambiguity that must be resolved before any code is
written against it. **No business calls have been made on this pipeline. No
further dialing is authorized by this document** — dialing requires Nathan's
fresh, explicit go-ahead per call or test list.

## 2. What the system is for

Nathan (software engineer, Leonardo DRS, Melbourne/Palm Bay FL) wants an AI
phone agent that can place real business calls on his behalf (e.g., to insurers,
dealerships, clinics) under strict rules: it introduces itself as his assistant,
never as a human and never as Nathan; it never gives SSNs, card/bank numbers,
codes, passwords, or signatures; it never consents to background checks or
payments without per-call approval; it never makes promises it cannot keep; and
it escalates hard questions to a stronger model, then to Nathan himself. The
built-in Muse business-calling feature has a daily call cap that Nathan hit on
Oct 29, which is why this self-hosted build exists. The self-hosted build is
currently in the **test phase only**: short test calls to Lauren (fiancée,
expecting the calls, Nathan present listening) to prove disclosure, voice
quality, understanding, barge-in, escalation, graceful ending, and
transcript/cost recording.

## 3. Architecture

```
Caller (PSTN phone)
  -> Telnyx Call Control app "Nathan AI Assistant (Call Control)"
  -> webhook POST /telnyx/webhooks (via public tunnel, see §6)
  -> live_server.py (FastAPI, 127.0.0.1:8001) verifies Ed25519 signature,
     routes webhook events, holds JOB_REGISTRY for media websockets
  -> WS /telnyx/media/{attempt_id} (Telnyx media stream, G.711 mu-law 8 kHz)
  -> live_pipeline.py (Pipecat):
       Telnyx serializer (upconverts 8 kHz PCMU to 16 kHz)
       -> VAD (Silero, local) -> STT (OpenAI Realtime, local-VAD mode)
       -> LLM user aggregator -> fast LLM -> (escalation) frontier LLM
       -> ElevenLabs TTS (Jessica) -> Telnyx media output
  -> record written to logs/test-call-*.jsonl on hangup
```

Supporting pieces:

- **Telnyx adapter** (`server/telnyx_adapter/`): canonical adapter. Validates
  every dial against an exact `Approval` (destination, max minutes, estimated
  cost) and a per-call/monthly cost ledger. Single-claim model: one dial
  attempt per claim; a refused dial is never silently retried by the adapter.
- **Dial bridge** (`POST /internal/register-media-job` on the live server):
  gated by an `X-Internal-Token` header that must equal a token stored only in
  `~/workspace/ai-calling/.internal-token` (0600). Any request without the
  correct token returns 404. Registration is in-memory: a service restart
  clears it, so registration must happen immediately before dialing.
- **Proxy shim** (`server/proxy_shim.py`, systemd
  `ai-calling-proxy-shim.service`): binds 127.0.0.1:18080 only. Forwards each
  local CONNECT unauthenticated to the VM egress proxy and replies with a
  clean `HTTP/1.1 200 Connection Established` (no `Connection: close`), then
  splices bytes. Built because the VM egress transparently intercepts direct
  TLS (causing `SSL WRONG_VERSION_NUMBER` on provider websockets) and because
  the egress proxy's CONNECT response carries `Connection: close`, which the
  `websockets` 17.3 client strictly rejects. The live server unit sets
  http_proxy/https_proxy (both cases) to the shim; NO_PROXY covers localhost.
- **STT handshake patch** (in `live_pipeline.py`): `_apply_websocket_connection_header_patch()`
  wraps `websockets.client.ClientProtocol.process_response` in memory
  (idempotent, no library edits). On InvalidUpgrade it accepts ONLY when
  status == 101 AND Upgrade is exactly one `websocket` token AND
  Sec-WebSocket-Accept validates against the request key; everything else
  re-raises. Needed because OpenAI (via Cloudflare) returns a valid 101 with
  `Connection: close`.
- **Deploy venv:** `~/workspace/ai-calling/.deploy-venv` (pipecat-ai 1.8.0,
  fastapi 0.158.2, uvicorn 0.54.0, cryptography 50.0.2, openai 2.54.0).
  System Python lacks `cryptography` — all dial/adapter scripts must run
  under the deploy venv.
- **Logging trap (do not repeat):** the live server runs uvicorn at
  log_level="warning" with no root-logger config, so stdlib INFO logging is
  silently dropped in production. Pipeline instrumentation must use loguru.
  A false "no frames arrived" finding on Oct 29 came from exactly this trap
  and was later corrected in chat.
- **Journal timezones:** `journalctl` lines display local EDT; loguru lines
  inside them carry UTC. Convert carefully when reading a call window.

## 4. Decisions log (all by Nathan unless noted; approvals stay literal)

| Date/time (EDT) | Decision |
|---|---|
| Oct 29, 12:32pm | Escalation ladder: fast model → frontier model auto-escalation → Nathan. Frontier bound enforced in code: max 3 frontier turns AND max 120 seconds. $1.00 hard budget for test calls; $20/mo ledger cap and $5/call soft cap remain binding for live business calls. Disclosure opening verbatim: "Hi, this is Nathan's assistant calling on his behalf." Forbidden: SSN, card/bank numbers, codes, passwords, background-check consent, legal signatures, payments without per-call approval. Dedicated new AI number; never port the personal number. Loop-in bridging NOT implemented (pipeline speaks a loop-in line, ends gracefully, records `loop_in_requested`). |
| Oct 29, 12:54pm | Model selection "dynamic always": pipeline selects dynamically within caps. Pools (from live /v1/models): FAST = gpt-4o-mini, gpt-4.1-mini, gpt-4o; FRONTIER = gpt-5, gpt-5.1, gpt-4o; STT = gpt-4o-mini-transcribe, gpt-4o-transcribe (first pool entry present wins). ELEVENLABS_TTS_MODELS unset → eleven_flash_v2_5. |
| Oct 29, 12:35pm → 12:43pm | Telnyx upgrade + number approved; **$20 funding CANCELLED** ("test it out with that first"). No payment method exists on the Telnyx account; nothing was ever funded beyond credits. Any future funding needs fresh approval. |
| Oct 29, 1:29pm | **Deepgram DROPPED** ("Screw deepgram"): two independently created keys probed 401 through the vault path even with correct `Authorization: Token <key>` format; the vault surrogate path cannot deliver a key embedded in a larger header value. Do not re-attempt Deepgram through that path without a new mechanism. OpenAI transcription is the hearing side now (but see §9: OpenAI credits exhausted). |
| Oct 29, 3:15pm | Voice pick: **Jessica** (ElevenLabs premade, female, american, "Playful, Bright, Warm", id `cgSgspJ2msm6clMCkdW9`). Nathan, listening live, confirmed it "sounded great" — voice quality check PASSED by ear. Original pick Natasha (`kCx3Qoh3lfILbbTZftSq`) proven Oct 29, 3:12pm NOT to exist on the account (TTS closed 1008). Do not use it again. Standing rule: stock synthetic voices only, NEVER a clone of any real person (excludes the account's 2 professional + 1 cloned voices). Other premade feminine options on file: Sarah EXAVITQu4vr4xnSDxMaL, Bella hpp4J3VqNfWAUOO0d1Us, Alice Xb7hH8MSUJpSbSDYk0k2, Laura FGY2WhTYpPnrIDTdsKH5, Lily pFZP5JQG7iQjIQuC4Bku, Matilda XrExE9yKIg1WjnnlVkGX. Direction unchanged: feminine, warm, slightly sultry, stock synthetic, never a clone. |
| Oct 29, 4:59pm | "GO FORTH": approved the inbound track-filter fix + buffer-boundary regression test + unpausing the measurement dial. Scope held tight: NO STT fallback, NO server-VAD switch, NO hangup-path changes, NO Pipecat library edits/upgrades, gates unchanged. |
| Oct 29, ~5:04pm | "Do what you think" after a safety stop on rebuilding the dial: the chosen path was rebuilding the dial script from the adapter's own source/docs, never from the two flagged files (§10). |
| Oct 29, 5:20pm | **Provider direction: "Grok for listening and Gemini for thinking."** Recorded verbatim. **OPEN AMBIGUITY — resolve before writing code:** it is NOT established whether "Grok" means Groq's Whisper transcription (the free-tier provider researched Oct 29: Whisper Large V3, free tier, no card) or xAI's Grok API (which is not a Whisper STT provider). Do not assume. The researched free-tier facts in §9 are about Groq. Confirm with Nathan which provider he means. Gemini for thinking = Google Gemini API free tier (Flash/Flash-Lite models only; the $300 Google Cloud trial cannot pay for Gemini API usage since March 2026). |

Standing rules that bind any future agent working on this project:

- Approvals stay literal to their exact brief. A go-ahead for one call, one test
  list, or one funding step does not stretch into a submission, a second dial,
  or an adjacent step.
- Never edit the installed Pipecat library or upgrade Pipecat without separate,
  explicit approval.
- Never dial a business number from this pipeline; test calls only, to the
  verified test number, with Nathan present, until he authorizes otherwise.
- Never store, print, or report API key values (masked fragments included).
  Keys are created by Nathan himself and delivered only through the Secure
  Vault. Config files hold credential REFERENCES (env-var names) only.
- The Telnyx webhook public key and SSH public keys are public by design and
  may be handled openly. The internal media-job token is secret and is
  EXCLUDED from this export.
- Trial-tier limit: outbound calls only to verified numbers (Telnyx 403 error
  10010/D60 otherwise). Dialing any new number requires verification or a tier
  upgrade (upgrade needs funding approval).
- Journal/tunnel discipline: localhost.run tunnel hosts die constantly
  (minutes to ~30 minutes). Always re-verify health immediately before dialing;
  if dead, restart the tunnel, re-point the Telnyx app, read back, then dial
  back-to-back. Never reuse a consumed attempt id. Never dial from a script
  that was not freshly verified against the current tunnel host.
- No running-call watchdog timer exists yet (max_minutes is gated at approval,
  not mid-call). The dial script enforces the $1/10-minute stop by polling.
- Florida all-party-consent / TCPA disclosure and recording/transcription
  consent review is still open (see §8). The opening disclosure alone does not
  establish consent to transcription/recording.

## 5. Accounts, numbers, and identifiers (non-secret)

- Telnyx account: Business Trial. Identity verification completed by Nathan
  (GitHub OAuth + 2FA; evaluation_id efdcb703-7594-4423-8226-ed458a7b83d4).
  No payment method on file.
- Telnyx number: **+1 321 497 8649** (US local, Melbourne, FL, voice-enabled,
  HD Voice, only number on the account). Rental $1.00/mo. Purchase deduction
  $1.10; balance after purchase $3.90. **Balance $3.84 was last observed after
  test call 7; later balances were not freshly observed — do NOT present any
  balance figure as current, and do NOT price calls from balance deltas.**
- Call Control app: "Nathan AI Assistant (Call Control)", connection_id
  **3081546725312169326**, webhook API v2, voice only, number assigned and
  Active. Outbound voice profile "Default" (ID 3080348738397406661) assigned
  Oct 29, 1:27pm — outbound dialing enabled.
- Current webhook URL (as of export): `https://5e20d30fd78a98.lhr.life/telnyx/webhooks`
  — ephemeral localhost.run host; assume dead on arrival for any future work
  and re-verify. Failover URL still points at a long-dead host
  (`https://21fb999c9359c5.lhr.life/telnyx/webhooks-failover`) — known gap,
  has not mattered so far.
- Test destination: Lauren, **+13213677796**, verified on the Telnyx account
  Oct 29, 2:48pm. Lauren confirmed expecting the test calls.
- API keys (all stored via Secure Vault; values never in this package):
  Telnyx CONNECTED, ElevenLabs CONNECTED (Google sign-in), OpenAI CONNECTED
  (key named "muse", created Oct 29, Active, All permissions, Default project,
  never expires; two pre-existing unused Dec 22, 2024 keys on the account were
  flagged, no action taken), Deepgram UNUSABLE through vault path (401),
  DROPPED.
- Key mechanics: `start_live_server.py` resolves vault surrogates into real
  keys in process memory only at startup (never files/logs). A raw surrogate
  used directly as an API key gets HTTP 403 — an invalid probe, not evidence
  about the real key. Surrogate access in scripts: `sys.path.insert(0,
  "/opt/muse/skills/custom-connector-setup/bin")`,
  `from dynamic_credentials import dynamic_credential_entry`
  (`dynamic_credential_entry(credential_name, entry_name="access_token")`;
  `ensure_allowed_url(url, allowed_hosts)`).

## 6. Infrastructure and file map

Live server: `server/live_server.py` + `server/start_live_server.py`, one
FastAPI app on 127.0.0.1:8001: GET /healthz, POST /telnyx/webhooks +
/telnyx/webhooks-failover, WS /telnyx/media/{attempt_id} (in-process
JOB_REGISTRY; unregistered attempts closed 1011). Runs under systemd
`ai-calling-live.service` (master copy `systemd/ai-calling-live.service`,
installed to /etc/systemd/system — ephemeral on VM replacement, reinstall from
master; use system systemctl, NOT --user). Refuses to start without
TELNYX_WEBHOOK_PUBLIC_KEY (in unit Environment line, public by design).

Live pipeline files: `server/live_pipeline.py`, `server/telnyx_media.py`
(MediaAttachGate, streaming_start_request() builder, G.711 μ-law codec),
`server/model_select.py`, `server/voice_config.json` (Jessica),
`server/voice_pipeline.py` (older outline; the live path is live_pipeline.py),
`server/telnyx_webhook.py`, `server/call_job.py`, `server/server.py`,
`server/run_webhook_server.py`, `server/run_webhook_smoke.py`.

Adapter API (verified from `server/telnyx_adapter/` source):
`TelnyxAdapter(db_path, public_key_b64, config=None)`;
`prepare(*, job_id, connection_id, from_number, to_number, attempt_id,
approval, max_minutes=10)`; `claim_submission(attempt_id)`;
`record_response(attempt_id, response)` returns (control_id, leg_id,
session_id); `get_attempt(attempt_id)`; `hangup_command(attempt_id)`.
`Approval(job_id, destination, max_minutes, estimated_cost_cents,
approver_ref="nathan")` — adapter validates approval matches
job/destination/max_minutes and estimate ≤ per-call cap. `AdapterConfig`
caps: monthly 2000¢, per-call 500¢. `wire.hangup_request(call_control_id,
command_id)`. `telnyx_media.streaming_start_request(stream_url,
track="both_tracks", bidirectional_mode="rtp", bidirectional_codec="PCMU",
bidirectional_sampling_rate=8000)` returns {method POST, path
`/v2/calls/{call_control_id}/actions/streaming_start`, json{stream_url,
stream_track, ...}}.

Schema pins: Voice API v2, webhook envelope v2, Ed25519 over
`{timestamp}|{raw_body}`, 300s window, Python 3.12.3, cryptography 46.0.6,
PyNaCl 1.6.2.

Test pack (independent reference material authored in A-GILE, NOT by Muse —
never claim authorship): zip SHA-256
9d9aa9da3f2f8efc3671187131df64e0e7933490e408c1970f44caa270bb948f,
extracted at `test-pack-v1/` in this project and at
`~/workspace/telnyx-adapter-work/` (second build site). Manifest: 28 files,
0 errors, PASS (`verify_manifest.py`).

## 7. Qualification results (offline, before any live call)

- Reference pack re-run: **57/57 PASS**; mutations **3/3 caught**.
- Archived (second-site) adapter vs its own fake carrier: **30/30 PASS**.
- Canonical adapter (`server/telnyx_adapter/`): **37/37 PASS**.
- Full tree incl. canonical adapter + live pipeline tests: **81/81 PASS** at
  build; later **82/82** with the buffer-boundary test (§8).
- `sdk_compare.py` NOT re-run, not passed: requires an operator-supplied
  pinned SDK helper not in the pack, not downloaded. Adapters use no Telnyx
  SDK (stdlib HTTP).
- **Reconciliation flag (open):** two adapters exist — canonical
  `server/telnyx_adapter/` (37/37) and second-site
  `telnyx-adapter-work/adapter/` (30/30, included in this export under
  `second-adapter/`). Neither suite count is the other's. They must be
  reconciled into one before anything goes live. Nothing live was spent,
  changed, or called in the second-site build; all traffic was loopback plus
  public docs reads.

## 8. Fixes applied Oct 29 (all in `server/live_pipeline.py` unless noted)

1. **Disclosure AggregatedTextFrame fix (~4:01pm).** Disclosure queued as
   `AggregatedTextFrame(SENTENCE)` instead of plain TextFrame. Root cause:
   Pipecat 1.8.0 `SimpleTextAggregator` in SENTENCE mode holds a sentence
   ending in punctuation until non-whitespace lookahead arrives; the lone
   disclosure ending in "." never released (held 302s on the 3:20pm call).
   PROVEN LIVE on call 7 onward: disclosure spoken, test contact heard it.
2. **Record-on-hangup fix (same change).** Record now writes in a finally path
   raced against transport disconnect, priced from actual hangup timing
   (watcher poll ~0.25s), not pipeline lifetime. PROVEN LIVE: records for
   calls 7–10 written promptly. (Record files' `minutes` /
   `estimated_cost_cents` are pipeline estimates — do NOT quote as exact call
   length or Telnyx bill; actual answered durations come from webhook events.)
3. **VAD sample-rate fix (~4:15pm).** `SileroVADAnalyzer()` with NO sample_rate
   arg (was pinned 8000). Root cause: pipeline runs 16 kHz (serializer
   upconverts Telnyx 8 kHz PCMU), but `VADAnalyzer.set_sample_rate()` keeps a
   truthy init rate, so the analyzer stayed at 8 kHz with 256-sample chunks,
   consuming 16 kHz audio at half speed; offline repro max confidence 0.427 vs
   the 0.7 gate, never left QUIET; correct path 0.58–0.92. OpenAI Realtime STT
   runs turn_detection=False (local-VAD mode) and commits ONLY on
   VADUserStoppedSpeakingFrame, so no VAD event → no commit → no transcript
   → no reply. Code-correct and offline-verified; LIVE-PROVEN on call 10
   (§9).
4. **Instrument fix (~4:29pm).** `InboundDebugTap` switched from stdlib
   logging to **loguru** (both "frames" and "vad" instances), plus new
   `_CountingTelnyxSerializer` that loguru-logs inbound WS `media` events
   (first + every 500th). This corrected a false finding: "no inbound frames
   reached the pipeline" from zero tap lines on call 8 was VOID, because the
   tap logged via stdlib INFO which uvicorn's warning level silently drops
   (proven offline; journal history showed 0 tap lines ever). The correction
   was owned to Nathan in chat ~4:27pm. Offline repro of fixed instrument: 60
   fed media messages → serializer count 60/60, 12 frames downstream, loguru
   lines emitted.
5. **Measurement instrumentation (~4:46pm).** `_LoggingSileroVADAnalyzer`
   logs per-chunk confidence/volume (every 10th chunk) + ALL internal state
   transitions via loguru (including STARTING blips, previously invisible);
   `_CountingTelnyxSerializer` logs `track` values. Gates UNCHANGED
   (confidence 0.7, start 0.2s, stop 0.2s, min_volume 0.6), NO STT fallback.
   Offline repro PASS (35 passing / 10 outbound dropped; synthetic speech
   showed QUIET→STARTING at confidence 0.811 then reset to QUIET at 0.583
   with volume 0.893 — the diagnosed flap). Note: Silero `voice_confidence`
   returns a shape-(1,) ndarray, not a float — a `_as_float()` helper coerces
   it for logging only (no decision-math change). Tests stayed 44/44.
   Service restarted ~4:46pm.
6. **Track-filter fix (~5:03pm, Nathan-approved).**
   `_CountingTelnyxSerializer` now accepts track in (None, "inbound",
   "inbound_track") and drops anything else with counted/logged drops; class
   docstring corrected (request param `stream_track` uses
   inbound_track/outbound_track/both_tracks, but the WS media EVENT field
   `media.track` uses inbound/outbound). Offline repro PASS: inbound 5/5,
   inbound_track 5/5, no-track 5/5 passed; outbound 5/5, outbound_track 5/5
   dropped; counters 15 pass / 10 drop. Tests: test_live_pipeline 44→45,
   full suite 81→82, all pass. Service restarted 5:03:14pm EDT, /healthz
   ok/telnyx-key.

## 9. Diagnoses (measured, not guessed)

### VAD diagnosis #2 (Oct 29, ~4:41pm) — the live problem before call 10

- VAD **receives** frames but **never fires** (on calls 7–9): Silero
  confidence on narrowband phone speech (8 kHz-origin μ-law, soxr-upconverted
  to 16 kHz) hovers near the 0.7 gate and flaps, while the state machine
  demands **6 CONSECUTIVE passing chunks** (start_secs 0.2 / 32ms chunks =
  192 ms) and resets STARTING→QUIET on any single failing chunk. Full live
  path offline: **only 1% of chunks ≥0.7, best passing run 0.**
- Volume almost certainly NOT the blocker (smoothed volume 0.73–0.95 in all
  offline arms vs 0.6 gate) but live audio level was unmeasured at that time.
- "Zero transitions" prove never-SPEAKING only, not never-STARTING (STARTING
  is internal/unlogged in the base analyzer — the new logging analyzer now
  exposes it).
- Second vector: stream runs track "both" and Pipecat's serializer **ignores
  track entirely** (verified: `serializers/telnyx.py` in the deploy venv has
  NO track handling), so outbound packets were concatenated into the same
  resampler stream as inbound audio. Frame-rate math (17.7 frames/s × 3,032
  bytes ≈ 1.68 audio-seconds per wall-second) is consistent with excess media
  flowing.
- Smart Turn RULED OUT (downstream of transcription only, cannot suppress VAD
  broadcasts). Rate fix confirmed correct (pipeline 16,000 Hz; no-arg analyzer
  takes 16,000).
- No synthetic signal has ever reached SPEAKING offline at either rate — the
  gate theory was a strong lead, not a closed proof, until call 10.

### Buffer-boundary finding (investigator, reproduced in our harness ~5:03pm)

Pipecat 1.8.0 `VADAnalyzer._run_analyzer` processes every complete 32 ms
window in an incoming buffer, then checks confirmation only AFTER the loop —
so a qualifying run can be reset by a failing window within the SAME buffer
before SPEAKING confirms. New test class `TestVADBufferBoundary`
(test-file-only stub overriding voice_confidence/_get_smoothed_volume;
volume held 0.9; gates live 0.7/0.2/0.2/0.6; `_vad_start_frames` = 6):
**Arm A** — same scores one 1,024-byte window per call (6 pass then 2 fail):
SPEAKING confirmed on 6th call. **Arm B** — same scores in 3,032-byte
live-shaped buffers (2+3+3 windows): SPEAKING NEVER observed, ends QUIET.
**Arm C** — sustained passing scores in 3,032-byte buffers: SPEAKING
confirmed. So 3,032-byte delivery per se doesn't break confirmation; trailing
failing windows inside the confirming buffer kill it. "6 consecutive chunks"
alone is an incomplete explanation; threshold tuning alone cannot fix a
mid-buffer kill. Also: **1.8.0 resets Silero's recurrent state on a 5-second
wall-clock schedule** — fast offline replay and real-time streaming exercise
different reset behavior. Do NOT edit the installed Pipecat library or upgrade
Pipecat without separate approval.

### Independent investigator audit (Oct 29, PDF ingested via WhatsApp thread)

Investigator model azure/gpt-6-astra; report saved as
`docs/INVESTIGATOR_VERDICT_2026-10-29.md` (+ `.txt` extraction) and PDF copy
in this export under `investigator/`. Investigator did NOT inspect deployed
files/VM, change anything, or place a call — deployment conclusions are
conditional. Findings:

1. **Track-name mismatch — PAUSE the measurement call until checked.**
   (Verified against deployed code ~4:55pm: CONFIRMED as a real risk; the
   live filter dropped track not in (None, "inbound_track"); the project's
   "inbound_track" wire value came only from self-invented fixtures
   (`telnyx_media.py` docstring example + its line-214 helper, which is NOT
   in the pipeline path); an independent Telnyx integration's code accepts
   BOTH "inbound" and "inbound_track"; Twilio-format track is
   inbound/outbound. The actual wire track value had NEVER been observed live
   — no call had run since track logging existed. Fixed by §8.6 and proven
   live on call 10.)
2. Buffer-boundary VAD behavior (reproduced above).
3. Counters don't prove the frames contained intelligible caller speech,
   correct track, matching codec/rate, or preserved ordering/duration;
   Telnyx start.media_format identifies encoding/rate/channels; measure total
   samples, not just frames.
4. Empty transcript does NOT prove OpenAI received no usable audio — commit
   boundary unproven; suggested controlled diagnostic: known speech fixture
   through the same STT session + one explicit commit, without dialing
   (diagnostic only, not an endorsement of timer-based commits).
5. Server VAD: OpenAI's transcription-session reference lists BOTH
   gpt-4o-mini-transcribe AND server_vad → documented config to VALIDATE,
   not a reason to assume a model change; acceptance by the exact live
   session untested; older session-creation endpoint is deprecated, preserve
   current GA session format.
6. Hangup/duration: elevate before another call — Telnyx dial API supports
   `time_limit_secs` (30–14,400, measured after answer, hangup cause
   `time_limit`); aiohttp ClientSession doesn't read proxy env by default
   (trust_env=True or explicit proxy config needed) — concrete explanation
   for the serializer hangup bypass; a duration limit is NOT a $1 guarantee
   (budget must include telephony+STT+LLM+TTS).
7. Consent belongs before measurement, not only before business calls:
   Florida's general consent provision refers to prior consent from all
   parties; the opening disclosure alone doesn't establish consent to
   transcription/recording; FCC treats AI-generated voices as artificial
   voices under the TCPA. Compliance checkpoint, NOT a conclusion the earlier
   tests were unlawful.

**Lauren transcription-consent question:** the investigator's consent point
was raised to Nathan and he did not answer it explicitly; his "GO FORTH" was
treated as proceeding with the measurement dial. If he later says hold for her
explicit consent, hold the dial.

## 10. Live test-call history (all Oct 29, 2026; test contact Lauren)

Approved brief (Oct 29, 12:54pm + test list approved 1:30pm): call Lauren
(+13213677796) from +1 321 497 8649, Nathan present listening, max 10 min
(expected ~5), **$1.00 hard cap**. Lauren confirmed expecting the test calls.
Nathan 2:52pm: "Go ahead and call when your ready" — standing go-ahead for
re-dials once the voice side verifies ready. Seven test checks: (1)
disclosure verbatim first audio — **PASSED live (call 7 onward)**; (2) voice
naturalness by ear — **PASSED (Nathan: sounded great)**; (3) barge-in —
untested; (4) ordinary understanding — **FAILED so far** (no turn ever
completed); (5) escalation via out-of-scope question — never reached;
(6) graceful end, no promises, no forbidden info — trivially passed (nothing
beyond disclosure ever spoken); (7) transcript + cost after — records
written, transcript always [].

Costs below are pipeline estimates from record files, NOT Telnyx bills. Only
"well under $1 cap" is solid for short calls.

1. **2:41pm — dial REFUSED, no call.** Telnyx **403 error 10010/D60**: trial
   accounts can only call verified numbers. **Fix: Lauren's number verified
   in portal 2:48pm** ($0.03 on success). Attempt
   1ee9a900-e013-45ed-a5d8-ca6673d51929 consumed, submit_unknown.
2. **2:49pm — FIRST LIVE CALL: connected, NO AUDIO either direction.**
   Attempt 5718bf54-68d1-4f27-866e-78c7a537329a; 45.1s, Lauren hung up
   (normal clearing). **OpenAI Realtime STT and ElevenLabs TTS both failed
   SSL WRONG_VERSION_NUMBER** (VM egress transparently intercepts direct
   TLS). No disclosure spoken, no transcript. Cost $0.04 total incl.
   verification. → Proxy vars added to live unit; websockets 17.3 honors env
   proxy.
3. **2:59pm — SECOND LIVE CALL: connected, NO AUDIO, new error.** Attempt
   d74c6a0e-8021-4909-8c05-94ca780ebe70; 35.1s. Both provider websockets
   failed **"invalid Connection header: close"** (egress proxy's CONNECT
   response trips websockets' strict validation). Cost $0.01. → Proxy shim
   built.
4. **3:12pm — THIRD LIVE CALL: STT CONNECTED, TTS failed on Natasha.**
   Attempt 6d2652ed-4806-4faa-9ff4-ae2031f1d7d4; 38.2s. **OpenAI STT session
   created + ready — proxy/shim + handshake fixes proven live.** ElevenLabs
   TTS closed **1008: Natasha voice id does not exist** → disclosure never
   spoken. Cost $0.02. → Jessica picked.
5. **3:18pm — FOURTH CALL (first Jessica): aborted by accident.** Attempt
   a9dd234e-461a-4453-af6d-fb45ecdc7780; hangup 0.3s after answer — **Nathan
   accidentally tapped hangup as the phone rang (his correction, 3:19pm) —
   NOT Lauren rejecting the call.** Streaming never started. Cost $0.01.
6. **3:20pm — FIFTH CALL (Jessica): everything connected, STILL no voice
   heard.** Attempt af532f19-f5e2-464f-89b2-aa2e9a13f3c1; ~29s live, Lauren
   hung up. STT ready, TTS websocket clean. Nathan reported: no voice heard.
   **Root cause proven 3:47pm: the sentence-aggregator lookahead trap**
   (§8.1). Cost $0.01.
7. **~4:06pm — SIXTH CALL (Jessica, AggregatedTextFrame fix live):
   DISCLOSURE HEARD, reply side failed.** Attempt
   aed6cff3-8d28-448c-9bea-62f4c7035fc6; answered 28.5s, Lauren hung up
   (normal clearing). Journal: TTS generating disclosure 20:06:27Z, bot
   speaking ~3.85s; **Nathan live report: Lauren's phone played something
   (first audio ever), she said "who is this", got ~21s of silence, hung
   up.** Journal after disclosure: NO VAD event, NO STT frame, NO LLM/TTS
   reply; transcript []. Telnyx inbound packets 1379 (PSTN leg only — does
   not prove WS forwarding). Record: logs/test-call-2026-10-29-jessica4.jsonl.
   → VAD diagnosis (§8.3).
8. **~4:20pm — SEVENTH CALL (Jessica, VAD fix live, parent dialed directly):
   disclosure heard, sounded great, STILL no reply.** Attempt
   5a7c7650-1c74-42e8-b25f-a07afc5d7da8; answered ~7–9s, hangup observed at
   35s monitor (48s from dial). **Nathan live report: she heard it, sounded
   great, just no response still.** Transcript [] again. Parent initially
   read zero InboundDebugTap lines as "no frames arrived" — **that inference
   was later proven VOID (blind tap, §8.4) and corrected to Nathan.**
   Record: logs/test-call-2026-10-29-jessica6.jsonl (outcome hangup, est
   0.86c). Control id
   v3:B_HfbxsxyV-7Q84Fo8Pz38tHVPYPBWsRaVG1wb5h6h3E9d2_MPYbSA.
9. **~4:31pm — EIGHTH CALL (Jessica, instrument fixed, parent dialed
   directly): audio proven arriving, VAD still never fired.** Attempt
   05777655-bd5f-4e3f-99ff-97f256be31bf; dial 200, answered after 7.0s,
   streaming_start 200, hangup observed at 40s monitor (51s from dial).
   Disclosure spoken 20:31:17–20:31:21Z. **Instrumented journal: ws-media
   events reached #2500+ (Telnyx IS forwarding inbound media — that
   hypothesis is RULED OUT); inbound-audio frames reached #500+ (frames ARE
   reaching the pipeline); ZERO vad-transition lines; transcript [].**
   Record: logs/test-call-2026-10-29-jessica7.jsonl (outcome hangup, est
   0.92c). Control id
   v3:BcaBxLqZQxEtAuujWavTz8akKX_QP0uJHPyeGuv6Nu3_xWpa0lVS8w.
   **Nathan confirmed ~4:32pm: Lauren spoke AT LENGTH on this call ("had a
   mouthful to say")** — so VAD silence was NOT correct behavior; frames +
   real speech arrived and VAD never fired. → Diagnosis #2 + measurement fix
   + investigator review.
10. **~5:07pm — NINTH LIVE CALL = MEASUREMENT CALL (Jessica, filter fix +
    measurement instrumentation live, rebuilt dial script, new tunnel
    5e20d30fd78a98).** Attempt 3ba7f62e-b37c-43e7-ab21-0fcdeb8b7396; dial
    200, answered after 11.0s, streaming_start 200, hangup observed at 25s
    monitor (39.8s from dial). Record:
    logs/test-call-2026-10-29-measure.jsonl (outcome hangup, minutes 0.365
    estimate, estimated_cost_cents 0.55). Control id
    v3:rKw1JSJLOINzOKozkN9wifLxjab_6gf8Yji90VMErKfCfCkvOnxMNg.
    Transcript [] — but the journal (EDT) tells the real story, in order:
    - 17:06:52 — STT session created + configured and ready; TTS generating
      disclosure "Hi, this is Nathan's assistant calling on his behalf."
    - 17:06:52 — **ws-media event #1 track=inbound** (FIRST live observation
      of the wire value); outbound event #1 dropped. **Filter fix PROVEN
      LIVE** — the investigator was right; the old filter would have dropped
      ALL caller audio on this call.
    - 17:06:58 — **vad-state QUIET -> STARTING chunk #162 confidence=0.828
      volume=0.758; STARTING -> SPEAKING chunk #165 confidence=0.769
      volume=0.845; SPEAKING -> STOPPING chunk #171; STOPPING -> QUIET chunk
      #177.** VADUserStartedSpeakingFrame and VADUserStoppedSpeakingFrame
      both emitted. **VAD REACHED SPEAKING ON REAL SPEECH.**
    - 17:06:59 — 0.5s after the stop frame, OpenAI Realtime STT errored:
      "You have no credits remaining. Add credits to continue"
      (ErrorFrame, fatal False). **Transcription was never attempted before
      this call because no stop event ever fired — the VAD failure MASKED
      the billing failure all day.**
    - 17:07:02–17:07:07 — longer speech: confidence flapping 0.53–0.75,
      volume 0.9+; only STARTING blips (#275 conf 0.713 → QUIET #278 conf
      0.606; #376 conf 0.723 → QUIET #379 conf 0.689; #381 conf 0.745 →
      QUIET #384 conf 0.668; #396 conf 0.726 → QUIET #402 conf 0.053).
      Volume is NOT binding; the 0.7 gate with 6-consecutive-chunks is
      marginal for sustained narrowband speech.
    - Teardown: the known SSL hangup error recurred (harmless, callee
      already hung up).

Aborted dial attempts (no call, no charge, no attempt id consumed): ~4:03pm
(tunnel host e17b88c0879c30 died between verification and dial) and ~4:18pm
(tunnel host 7eb7c60e58943c died the same way). Tunnel host history Oct 29
(all .lhr.life): a07f1349afba14 → 8cad1f2e7a54dc → d827f3dd4572b1 →
c3971b840019d2 → a8bdf5dfe8633f → beb9af97a9ee46 → 845c7d50393e33 →
21fb999c9359c5 → e17b88c0879c30 → df6539cbafe584 (call 7) →
7eb7c60e58943c (dead) → 598e946b2627e5 (call 8) → f61cb9164485e6 (call 9,
dead by ~4:44pm) → **5e20d30fd78a98 (call 10, assumed dead by now —
re-verify before any future dial)**.

Consumed attempt ids (NEVER reuse): 1ee9a900-e013-45ed-a5d8-ca6673d51929,
5718bf54-68d1-4f27-866e-78c7a537329a,
d74c6a0e-8021-4909-8c05-94ca780ebe70,
6d2652ed-4806-4faa-9ff4-ae2031f1d7d4,
a9dd234e-461a-4453-af6d-fb45ecdc7780,
af532f19-f5e2-464f-89b2-aa2e9a13f3c1,
aed6cff3-8d28-448c-9bea-62f4c7035fc6,
5a7c7650-1c74-42e8-b25f-a07afc5d7da8,
05777655-bd5f-4e3f-99ff-97f256be31bf,
3ba7f62e-b37c-43e7-ab21-0fcdeb8b7396.

## 11. Current blockers and known gaps (in fix order)

1. **OpenAI credits exhausted (PRIMARY).** Transcription fails at commit
   with "no credits remaining." Only Nathan can add credits/payment to his
   OpenAI account; no agent can do this for him. Until fixed, no turn can
   complete regardless of any other change. Note: the same account funds the
   LLM brain, so LLM turns will presumably hit the same wall once a turn
   runs — untested, do not assume otherwise.
2. **Provider direction ambiguity (§4, Oct 29 5:20pm).** Confirm "Grok" =
   Groq Whisper vs xAI Grok before writing any provider code. Researched
   free-tier facts (Oct 29, from web search — re-verify before budgeting):
   Groq free tier, no card: Whisper Large V3 20 req/min, 2,000 req/day,
   7,200 audio-sec/hour, 28,800/day; LLM gpt-oss-120b/20b and Qwen 30
   req/min, 1,000/day, 8,000 tokens/min, 200,000 tokens/day, OpenAI-compatible
   API; Llama models left Groq's free tier Aug 2026. Gemini API free tier, no
   card: Flash/Flash-Lite only (Pro left free tier Apr 2026); $300 Google
   Cloud trial cannot pay for Gemini API usage since Mar 2026. Groq Whisper
   is request-based, NOT a persistent streaming session — the pipeline's
   commit-on-VAD-stop design fits it better than a Realtime replacement, but
   latency/turn-taking must be re-proven. Deepgram $200 signup credit (no
   card) remains the largest STT credit but is blocked by the vault
   key-format problem (§4) until a new delivery mechanism is proven.
3. **Gate tuning for sustained speech (SECONDARY, measured).** Candidates
   from call-10 data only, change ONE at a time and re-measure: start_secs
   0.2 → 0.1 (3 chunks — the observed STARTING runs were 3 chunks before a
   failing chunk killed them), confidence 0.7 → 0.5 (sustained speech sat
   0.53–0.75), min_volume 0.6 → 0.5 (NOT indicated by data — volume was
   0.9+ during speech; do not change first). Buffer-boundary mitigation
   (bounded re-chunking before VAD) is a design task needing approval, never
   a Pipecat library edit.
4. **Serializer auto-hangup POST bypasses the proxy shim** (its aiohttp
   ClientSession ignores proxy env — investigator-identified fix: trust_env
   =True or explicit proxy config; NOT yet fixed). Harmless while the callee
   hangs up first; matters for agent-initiated hangup.
5. **Webhook failover URL points at a dead host** (21fb999c9359c5). Re-point
   when convenient; primary path is unaffected.
6. **No running-call watchdog timer** (max_minutes gated at approval only).
7. **Stable tunnel address decision open.** A second tunl subdomain
   reservation is PARKED at Nathan's direction (relay uses fixed
   piperelay.tunl.online, one reserved slot, in use). Do not reopen unless he
   raises it. localhost.run churn is the current operational tax on every
   dial.
8. **Adapter reconciliation** (§7) — canonical vs second-site adapter.
9. **Florida/TCPA disclosure + recording/transcription consent review**
   (investigator §8.7) — open before business calls; Lauren's explicit
   consent for measurement transcription was never explicitly answered.
10. **Remaining product gaps:** hold/unhold ordering unimplemented; quarantine
    list-only (no operator UI); loop-in bridging not implemented; voice
    quality + consent review open; test-store SQLite unencrypted (test-only).
11. **Built-in Muse business calling is separate** and NOT part of this
    package: enabled Oct 28, outbound US business numbers only, daily limits
    (per-business limit hit ~9:07am Oct 29; overall daily limit hit 9:44am
    Oct 29 — do not retry automatically after either refusal). Its calls
    (Portfolio Oct 28, USP/NWAN/Boniface Oct 29) belong to the GAP/insurance
    thread, not this pipeline.

## 12. Canonical dial procedure (back-to-back, do not delay between steps)

1. Re-verify tunnel healthz through the CURRENT host
   (`https://<host>/healthz` → `{"status":"ok","mode":"telnyx-key"}`) AND
   local `http://127.0.0.1:8001/healthz`. If dead: restart tunnel (parent
   only), re-point the Telnyx app via browser task, read back, then proceed.
2. Fresh attempt id (uuid hex; NEVER reuse a consumed one) + fresh job id.
3. POST registration to `http://127.0.0.1:8001/internal/register-media-job`
   with the token read from file at runtime (never printed), refs explicitly
   including elevenlabs_voice_id `cgSgspJ2msm6clMCkdW9`, expected_route
   exactly [connection_id, from, to], log_path for the new record.
4. Adapter prepare + claim_submission.
5. Telnyx POST /v2/calls (connection 3081546725312169326, from
   +13214978649, to +13213677796, **client_state = adapter-encoded base64
   {"attempt_id": uuid}, NOT raw id**).
6. Poll webhook DB for call.answered at ≤1s intervals.
7. streaming_start with stream_url `wss://<host>/telnyx/media/<attempt id>`
   (both tracks, bidirectional, PCMU 8kHz, via streaming_start_request()).
8. Monitor to hangup / 10 min / $1 cap → collect transcript+cost record from
   log_path + adapter events → report → update this state file.
9. Read the journal IN THIS ORDER: ws-media counts + **track values** →
   inbound-audio frames → **vad-chunk confidence/volume lines** →
   **vad-state transitions (incl. STARTING blips)** → STT commit/error lines
   → transcript.

## 13. Verifier checklist (for the receiving agents)

- [ ] Re-run the full suite (expect 82/82) and the reference pack (57/57,
  mutations 3/3) from the included trees; report any deviation with the
  failing test name, not a summary.
- [ ] Confirm no file in this package contains a real API key, token, or
  surrogate VALUE. (Expected: the strings `hsurr:` appear only as prefix
  checks in `start_live_server.py` and the dial script; the internal token
  file is excluded; credential references are env-var names only.)
- [ ] Verify the track-filter claim against `live_pipeline.py`: the
  serializer must accept (None, "inbound", "inbound_track") and drop
  "outbound"/"outbound_track" with counted logs.
- [ ] Verify the VAD claims: no-arg `SileroVADAnalyzer()` at 16 kHz;
  disclosure via `AggregatedTextFrame(SENTENCE)`; STT turn_detection=False
  committing only on VADUserStoppedSpeakingFrame.
- [ ] Reproduce the buffer-boundary finding from `tests/test_live_pipeline.py`
  (`TestVADBufferBoundary` arms A/B/C) and confirm the conclusion that
  trailing failing windows inside a 3,032-byte buffer kill confirmation.
- [ ] Reconcile the two adapters: diff canonical `server/telnyx_adapter/`
  against `second-adapter/adapter/`; list every behavioral difference; propose
  ONE canonical adapter. Do not merge blindly.
- [ ] Audit the cost ledger: every cost figure in §10 is a pipeline estimate
  unless marked as a Telnyx observation (verification $0.03, purchase $1.10,
  rental $1.00/mo, balance $3.84 last observed after call 7). Flag any figure
  presented elsewhere as a bill.
- [ ] Audit the consent section: confirm what is proven (disclosure spoken,
  test contact expecting calls) vs what is NOT proven (explicit consent to
  transcription/recording, Florida/TCPA compliance for business calls).
- [ ] Before proposing any provider swap (Groq/Gemini/Deepgram): confirm the
  §4 ambiguity, check the provider's CURRENT free-tier/credit terms, and
  state whether the replacement is streaming or request-based and what that
  changes in the pipeline's turn-taking design.
- [ ] Do NOT dial, fund, purchase, upgrade, or contact any provider account
  on the basis of this package alone. Those actions need Nathan's explicit,
  per-action approval.

## 14. What is in the zip (and what was left out)

Included: this state file; `server/` (live pipeline, adapter, media,
model-select, voice config, proxy shim, webhook server, notes); `tests/`
(45-test live pipeline suite incl. buffer-boundary test + adapter suite);
`docs/` (architecture, connector, test plan, wiring prep, live pipeline
notes, voice shortlist, telephony/costs, need-Nathan list, adapter status,
investigator handoff + verdict MD/txt); `systemd/` (both unit masters);
`test-pack-v1/` (extracted independent test pack); `second-adapter/`
(second-site adapter + notes); `logs/` (test-call record JSONLs + offline
test/mutation result files only); `investigator/` (independent audit PDF);
`tools/dial_measure_20261029.py` (the rebuilt measurement dialer — reads
the internal token from file at runtime; the token file itself is NOT
included).

Excluded on purpose: `.internal-token` (secret, 0600); `.deploy-venv/`
(rebuild from requirements.txt); `__pycache__/`; `logs/webhook.db` (raw
webhook payload store — the summarized JSONL records are included instead);
all `logs/*.log` and `tunnel*.log` (noisy, ephemeral host history — the
history is summarized in §10); Nathan's personal files outside this project
(finances, medical, house, job search, messages).

---

*End of state export. Next update trigger: OpenAI credits restored, provider
ambiguity resolved, gate tuning applied, or the next live call — whichever
comes first. Update this file, not a new one, and keep the call log in §10
append-only.*


## Qualification attempts — Oct 29, 2026, ~10:52–10:55pm EDT (appended, prior evidence untouched)

Per-call authorization: Nathan tapped "Yes, run the qualification call" ~10:51pm EDT. Destination +1 321-831-5278 (his personal number, verified on the trial account). Dialer: `tools/dial_qualification_20261029.py` (derived from the measure dialer, repointed from Lauren's number; 10-min / $1.00 caps; Jessica cgSgspJ2msm6clMCkdW9). Local + public /readyz both `{"ready":true,"failures":[]}` before dialing.

- **Attempt A (no attempt_id — aborted before registration):** dialer exited 2 in ~1s. Local healthz 200; public healthz via the dialer's default Python-urllib signature returned **403 "error code: 1010"** (tunl's Cloudflare edge bans that UA signature). Probes immediately after: POSTs to the public /telnyx/webhooks path with curl, Telnyx, empty, and browser user-agents all reached the server and were correctly rejected **400 {"accepted":false}** (signature check working); only the Python-urllib UA got 1010. Fix applied to the dialer copy only: normal User-Agent header on its HTTP calls. No call placed, no charge.
- **Attempt B — attempt 6c7ac2c3-3884-406e-b493-9c7e323a4d6b, job 28875187-93a8-418f-a3c2-1a8c746d0c0d:** health checks 200/200, media job REGISTERED, adapter claim OK, then the dial POST to Telnyx returned **401, error 10009 "The API key looks malformed."** Dialer did not retry (single-claim model) and did not record a response. No Telnyx call was created; no charge; the phone never rang. This attempt is dead: its media job remains registered-but-unconsumed at the server and its adapter claim is spent; a retry needs a fresh attempt.
- **Root cause (verified):** the TELNYX_API_KEY value in `.provider-credentials` is **35 characters** and is byte-identical to what Nathan pasted in chat Oct 29 ~10:34pm — the placement did not truncate it; the pasted value itself is incomplete (a full Telnyx API key is ~61 chars). **Correction to the deploy report:** `logs/provider-preflight.json` verifies only Groq/Gemini/ElevenLabs (models + Jessica voice); it never authenticated the Telnyx key. This dial was the first real Telnyx auth attempt with the stored key, and it failed. Status: **BLOCKED on Nathan supplying the full Telnyx API key** (portal API Keys; if the existing key shows masked, create a new one and copy the full value at creation). Once placed, re-run the qualification dialer under the same authorization — still zero live calls on the new pipeline.


## Qualification attempts — continued, Oct 29, 2026, ~10:58–10:59pm EDT

Nathan supplied a replacement Telnyx value in chat (59 chars, KEY…_… full format); swapped into `.provider-credentials` via the same one-time stdin placement (value never printed/logged; length verified only). `ai-calling-live` restarted; local + public /readyz true.

- **Attempt C (no attempt_id — aborted before registration):** public health GET through the egress proxy died with `RemoteDisconnected` while the tunnel service itself stayed up the whole time (active since 22:07, 0 restarts; neighboring requests succeeded). Dialer health gate had no retry — added 3-attempt retry to the dialer copy. No registration, no dial, no charge.
- **Attempt D — attempt b13b032a-c93a-4508-8a9d-379ae3164f64, job 7ac0f5b9-df5b-48fb-b12c-973951aa3de6:** health gate passed on retry (transient drop reproduced once, then 200). Registered + claimed. Dial POST reached Telnyx: **401 error 10009, detail: "No key found matching the ID 'KEY01A18…' with the provided secret"** (ID redacted here to its public prefix; the ID half of an API key is an identifier, the secret half is never recorded). Meaning: the value is well-formed, but that ID+secret pair matches no key — the halves likely came from two different keys, the copy is still incomplete, or the key belongs to a different account. No call created, no charge. Attempt is dead (claim spent).
- **Next step (with Nathan):** create ONE fresh API key in the same Telnyx account that owns the Call Control app, copy the entire string from the creation dialog in a single copy, send it the same way. Then swap + re-run under the existing qualification authorization. Still zero live calls on the new pipeline.


## API Keys portal inspection — Oct 29, 2026, 11:02–11:06pm EDT (read-only)

Portal session had expired; Nathan supplied a fresh sign-in code in chat (passed to the waiting browser step as text, never recorded). Inspection of Account Settings → API Keys, nothing created/changed/deleted:

- **Exactly ONE API key exists on the account:** ID prefix KEY01A18AC15E…, tag "muse ghin", **Active**, created **Oct 29, 2026, 11:01 PM**, no expiration, **Last Used: Never**.
- That ID **matches Nathan's latest paste exactly** — right account, right key. The secret half he pasted still fails Telnyx auth ("no key found matching the ID with the provided secret", verified by a direct pre-swap auth check — no dialer attempt burned this time).
- **The portal never shows an existing key's full value again** ("This API Key is encrypted"; actions are only Deactivate / Edit (expiration + tags) / Delete). Full value exists only in the creation dialog. Conclusion: the secret was incompletely copied at creation (both of Nathan's full pastes had the same short 23-char secret) and this key's true secret is unrecoverable.
- **Fix in flight:** Nathan to create one more API key and use the creation dialog's Copy button for a single complete copy; Muse validates against Telnyx before swapping, then re-runs the qualification dialer under the existing authorization. The orphaned key can be deleted once the new one works. Legacy "API V1 Tokens" page unchecked (session dropped mid-inspection) — irrelevant to the v2 dialer.


## Stranded key in the creation dialog — Oct 29, 2026, ~11:07–11:10pm EDT

Root cause of ALL of tonight's bad pastes, finally established: **the Telnyx "API Key Created" dialog displays the key MASKED** (first ~24 chars + asterisks, no show/unmask control). The full value is reachable ONLY via the dialog's "Copy to clipboard" button. Anything captured by reading/selecting the visible field is incomplete — which is exactly what Nathan's three pastes were.

Sequence: Nathan, in his own takeover of the Muse browser (~11:07pm), deleted the orphaned AC15E… key and created a new one (created 11:07 PM, Active, ID prefix **KEY01A18AC7191E74BCF09FE6…**, no tag). The creation dialog was left OPEN by the browser agent, which also clicked "Copy to clipboard" ("Copied!") — so the full value exists ONLY in that browser's clipboard. The agent cannot read the clipboard or the masked field. A shell-side attempt to reach the browser's session/clipboard from the VM failed (browser processes are isolated from the shell user). As of ~11:10pm the browser window is under NATHAN's control (takeover) with the dialog still open. Plan put to Nathan: click "Copy to clipboard" in the dialog once more, paste straight into the main chat, Muse validates against Telnyx before swapping; fallback — he hands browser control back and the agent pastes the clipboard into a scratch textarea and reads the DOM value. If the dialog closes first, the key is lost and another must be created. Account now holds this single key.


## FIRST LIVE CALL on the merged pipeline — Oct 29, 2026, 11:12–11:13pm EDT (media blocked by tunl edge)

Key saga resolved first: Nathan's fourth paste (ID prefix KEY01A18ACB148…, 58 chars) **validated against Telnyx** (pre-swap auth check: HTTP 200 + Melbourne number confirmed on the account) and was swapped into `.provider-credentials`; server restarted; readyz green both sides. The stranded 11:07 key (…AC719…) remains on the account as an extra Active key with an unrecoverable secret (cleanup pending Nathan's word). Note the winning paste still had yet another new ID — he created a fresh key rather than recovering the stranded one.

- **Attempt E — attempt 6a552f6a-9d76-495e-9b35-94035301ab47, job 4c4412a4-3671-400c-9350-ac8630ca467a: THE CALL CONNECTED.** Dial 200, **answered by Nathan in ~7.0s**, then `STREAMING_START -> 422` (Telnyx error **90046 "Media Streaming failed — Failed to connect to destination"**). Dialer hung up via the canonical adapter command (200) and exited 5. Nathan confirmed by ear: he answered, heard **silence**, call ended. Total ~13s; trial-balance cost a few cents at most (no itemized record — see below).
- **What is now PROVEN live:** real Telnyx API auth with the stored key; outbound dial through the adapter (single-claim, approval, allowlist); trial verified-number calling; **all four webhooks (initiated/ringing/answered/hangup) delivered through the tunl tunnel and correlated** (tunnel log: POST /telnyx/webhooks 200 ×4); canonical hangup command + ACK path.
- **The one failure: the media WebSocket never connected.** Tunnel log shows NO /telnyx/media request; server journal shows no media connection. Isolation tests immediately after: **local WS upgrade to ws://127.0.0.1:8001/telnyx/media/… SUCCEEDS** (server healthy); **external WS via the public tunl hostname is rejected with HTTP 500** (request dies at the tunl gateway/edge). Conclusion: **tunl carries HTTPS webhooks fine but breaks WebSocket upgrades** — the fixed-host transport fails handoff §14's media gate. No call record/transcript/cost JSONL was written for this attempt (pipeline never started), so §13 items 1–10 remain untested and 12 unsatisfied; item 11 is satisfied at the adapter/webhook level.
- **Next step = Nathan's decision:** (a) check tunl's docs/plan for WebSocket support (minutes; may be a tier limit), (b) rent-a-front-door VPS (public IP + TLS proxy for HTTPS/WSS into this VM — the durable fix the design always pointed at; small monthly spend, his call), (c) park for the night. No re-dial until the media transport changes.


## Option 1 verdict (tunl WebSocket check) — Oct 29, 2026, ~11:16–11:20pm EDT

Nathan chose option 1 (check tunl tier/setting for WS). Findings:

- **tunl's homepage advertises "WebSocket and SSE upgrades — Full HTTP/1.1 upgrades" on every tier** (Ephemeral free-forever and Reserved $0 preview both list it; comparison table and FAQ agree). There is NO paid tier or plan gate for WS — nothing to upgrade into.
- **Empirically broken platform-wide.** External WS upgrade tests (via egress proxy, both default and browser user-agents): reserved `musecallingthing` → **HTTP 500**; relay's reserved `piperelay` (different account) → **HTTP 500**; a fresh anonymous **ephemeral** tunnel spun up for the test → **HTTP 500**. The upgrade never appears in the tunnel traffic log and never reaches the server (server journal clean; local WS upgrade to the same route succeeds). The 500 is generated at tunl's edge.
- Conclusion: option 1 exhausted — no tunl setting/plan fixes media. Remaining: **option 2 (VPS front door)** or park. The qualification call itself is a ~2-minute re-run the moment a WS-capable public endpoint exists; everything else (keys, server, adapter, webhooks, dial/answer/hangup) is proven live.


## Front-door research — completed Oct 29, 2026, ~11:25pm EDT (deep research, sourced)

Full report: `~/workspace/research_notes/vps-front-door-wss-endpoint-20261030-0320/report.md`. Ranked:

1. **VPS front door (recommended): RackNerd 1GB KVM special $21.99/yr (~$1.83/mo)** + Caddy on the VPS (auto Let's Encrypt, WS upgrades pass with zero extra config) + **autossh -R backhaul from this VM through the egress ProxyCommand** (the already-proven pattern). Hostname: free DuckDNS (+LE DNS-01) at $21.99/yr total, or an owned .top domain (Porkbun $1.63 register / $4.63 renew) → ~$26.62/yr ongoing. ~1–2h setup. Month-to-month fallbacks: Vultr $5/mo, DigitalOcean $4–6/mo, Linode $5/mo. Hetzner no longer budget for US (~$17.49+/mo). Oracle Always Free = $0 but unreliable signup/capacity/idle-reclaim.
2. **Pinggy Pro $2.50/mo billed annually** — cheapest managed pass (SSH on port 443 rides our ProxyCommand; persistent subdomain), but its WS support is claimed, not proven — must live-test WSS on the free tier first (the tunl lesson).
3. **LocalXpose Pro $8/mo billed annually** — passes on paper incl. documented HTTP-proxy support.
4. **ngrok Pay-as-you-go $20/mo + usage** — works, but ngrok's own ERR_NGROK_9009 page states **HTTP-proxy support for the agent is a Pay-as-you-go feature** (Free and Hobbyist $8 do NOT include it) — this retroactively explains the Oct 29 ngrok failure: it was a paywall, not just a proxy quirk.
- **Eliminated:** Cloudflare Tunnel (cloudflared dials edge directly on 7844, no HTTP-proxy support — confirms our Oct 29 finding), zrok (no documented proxy support — Nathan had already ruled it out), bore (no hostname/TLS), localhost.run (WS support undocumented anywhere), direct WireGuard backhaul (UDP-only vs a TCP CONNECT proxy; wrapping possible but unjustified). frp with documented `transport.proxyURL` is a credible self-host alternative to autossh.
- No public reports of tunl's WS breakage exist; their README doesn't even claim WS.

**Awaiting Nathan's decision:** RackNerd VPS route / test Pinggy first / park.


## tunl WebSocket investigation — verdict Oct 29, 2026, ~11:43pm EDT (deep research, from source)

Full report: `~/workspace/research_notes/tunl-websocket-investigation-20261030-0330/report.md`.

- **Premise correction: there are TWO projects named tunl.** The black40x code (2022–23, protobuf HTTP-message relay; repos now redirect to levskiy1/*) is a PREDECESSOR and does not describe the hosted service. The current tunl.online is **thakurdotdev/tunl** (by Pankaj Thakur; TypeScript + Go SSH daemon; initial commit Jul 18, 2026; MIT). Any earlier "tunl re-models HTTP requests" reasoning (incl. Muse's localhost.run comparison line to Nathan) was based on the wrong codebase and is retracted.
- **Verdict: (a) a bug in an implemented feature — with (c) true in effect** (the advertising is false in practice, apparently believed-shipped rather than deliberate). The current server DOES implement WS proxying (`tunnel-server/internal/httpproxy/websocket.go`: hijack + splice raw bytes over the SSH forwarded-tcpip channel). BUT its first step asserts the ResponseWriter to `http.Hijacker` and returns **HTTP 500 "websocket not supported"** before dialing the origin when the assertion fails.
- **The breaker:** `capture.go`'s `responseCapture` wrapper (implements only WriteHeader/Write — no Hijack/Flush/Unwrap) is ALWAYS in the handler chain via CaptureMiddleware (its metrics client is non-nil), so the assertion fails on every tunneled subdomain. WS handler landed Jul 18, 2026; capture wrapper landed Jul 23, 2026 with the request inspector and silently broke it. No WS tests exist; no fix through HEAD (Oct 29, 2026); **zero GitHub issues filed on either repo** — this failure mode is publicly unreported; we appear to be the first to pin it down. SSE likely broken by the same wrapper (no Flusher) — noted, not proven.
- **Implication: NO client-side workaround exists** (no account, subdomain, CLI, or setting — there is no CLI; all ssh -R variants hit the identical path). Only an upstream fix (add Hijack/Unwrap to responseCapture, or bypass capture for upgrades — a tiny patch) or self-hosting thakurdotdev/tunl with that patch makes tunl carry media. This closes the tunl question definitively; the front-door decision (RackNerd VPS / Pinggy test / localhost.run reserved / park) stands as the path forward.


## Pinggy free-tier WSS test — PASSED Oct 30, 2026, ~9:29am EDT + VM-replacement restore

- Nathan chose Pinggy Oct 30, ~9:25am ("Let's do pinggy I guess"). Free tunnel stood up through the egress proxy (`ssh -p 443 -R 0:127.0.0.1:8001 qr@free.pinggy.io`, PTY wrapper pattern reused from the tunl units); free hostnames: tvodt-…run.pinggy-free.link and yjgqa-…free.pinggy.net.
- **Result: WebSocket upgrade returned HTTP 101 Switching Protocols through BOTH Pinggy free hostnames** (probe path /telnyx/media/pinggy-probe-20261030), and /healthz returned the server's real JSON. Free-tier caveat found by testing: browser-User-Agent requests get Pinggy's "Caution" interstitial screen; bypassed with the `X-Pinggy-No-Screen` header or a non-browser UA (the page itself documents this; API/Telnyx traffic is unaffected). Free tier also: 60-minute tunnel expiry, random hostname per session. Test tunnel killed after the probes.
- **Pinggy is therefore a PROVEN WS-capable front door** (unlike tunl). Standing setup still needs: Pinggy Pro (persistent subdomain, no expiry, no screen — $2.50/mo billed annually or $3/mo monthly, prices as researched Oct 29–30), a persistent tunnel service on this VM, AI_CALLING_PUBLIC_HOST + Telnyx webhook re-point to the Pinggy name, and Nathan's fresh per-call authorization before the qualification re-run.
- **VM REPLACEMENT found Oct 30, ~9:22am EDT:** the overnight runtime replacement wiped the ai-calling systemd units from /etc (live server down, nothing on :8001). Restored by re-running `tools/deploy_linux.sh` (DEPLOY_PASS, 129/129 tests, readyz green). NOTE for future replacements: the deploy script installs only ai-calling-live + ai-calling-proxy-shim; **ai-calling-tunl.service must be installed separately** from `~/workspace/ai-calling/systemd/` (done manually Oct 30). The SMS relay's units survived the same replacement (sms-relay + sms-relay-tunl active, piperelay health 200).


## Pinggy Pro trial ACTIVE Oct 30, 2026, ~9:46am EDT — dashboard recon done

- Nathan signed up himself via browser takeover: **Pinggy Pro free trial active** on nbeitelman1@gmail.com — Pro, 1 seat, started 10/30/2026, **End/Renewal 11/6/2026** (7-day trial; a payment method was saved with Stripe during his signup, so it converts to the $3/mo monthly plan he chose unless cancelled). No paid charge made.
- **Persistent subdomain already assigned: `aqlmfmjrsk.a.pinggy.link`** (format <name>.a.pinggy.link; editable in Dashboard > Domains > Manage domains; shown on Configure Tunnel as the token's persistent URL). Rename to a friendlier name is possible in the same dialog — Nathan's call.
- **Tunnel auth is TOKEN-based only** (no SSH public-key option exists in the dashboard — checked Settings/Manage Tokens/API Keys/Remote Devices/Configure Tunnel). Token lives at Dashboard > Manage Tokens (masked, "Show token" control), also on Configure Tunnel. Token value NOT yet obtained — the vault cannot serve a standalone tunnel process (surrogate limitation, proven Oct 29), so the established path is Nathan pastes it and Muse places it transiently into a 0600 file via a one-time stdin script, as with the Telnyx keys. NEXT: on receipt, build `ai-calling-pinggy` tunnel service (wrapper + unit, installed from the project tree so a VM replacement can restore it), set AI_CALLING_PUBLIC_HOST=aqlmfmjrsk.a.pinggy.link, re-point Telnyx app webhooks, verify readyz + external WSS 101, then qualification call on Nathan's fresh authorization.


- **Billing correction + decision Oct 30, 2026, ~9:53am EDT:** Stripe's trial email showed the trial converts to **$30.00/year (annual)**, not the $3/mo monthly Nathan picked in chat. Surfaced to him immediately; **Nathan decided: KEEP ANNUAL — $30/yr is fine.** So on Nov 6, 2026 the card on file (ending 2346) is charged $30/yr for Pinggy Pro. No plan change to make.


## PINGGY CUTOVER COMPLETE Oct 30, 2026, ~10:28am EDT

- **Token mix-up resolved:** the first value Nathan pasted (22 chars) was the account's **API key** (API Keys page, key named "muse"), NOT the tunnel token. The real **tunnel token is 11 chars** (Manage Tokens → Show token); Nathan pasted it Oct 30 10:03am and it was placed into `.pinggy-token` (0600) via the one-time stdin pattern. The wrong API-key value was deleted from the token slot. Lesson: Pinggy has two lookalike credentials — tunnel token (11) vs API key (22).
- **Tunnel service built:** wrapper `bin/pinggy-calling-tunnel.sh` (token read from the 0600 file at start; ssh to **pro.pinggy.io:443** — a.pinggy.io is the anonymous endpoint and ignores the token, proven by two "You are not authenticated" free-tunnel attempts; the dashboard's own generated command confirms pro.pinggy.io) + unit `systemd/ai-calling-pinggy.service`, installed to /etc/systemd/system and enabled. Journal verified: "You are authenticated as nbeitelman1@gmail.com" + persistent URL **https://aqlmfmjrsk.a.pinggy.link**. On VM replacement: re-run deploy script for the server, then install BOTH the tunl and pinggy units from the project tree.
- **External verification through the Pro name:** /healthz returned the server's real JSON; **WSS upgrade returned 101** on /telnyx/media/* — the capability tunl provably lacks. `AI_CALLING_PUBLIC_HOST` updated to aqlmfmjrsk.a.pinggy.link; ai-calling-live restarted; readyz green locally and publicly.
- **Telnyx re-pointed (browser, Nathan signed in himself via takeover after the Google approval card timed out once):** Call Control app "Nathan AI Assistant (Call Control)" primary webhook changed from https://musecallingthing.tunl.online/telnyx/webhooks to **https://aqlmfmjrsk.a.pinggy.link/telnyx/webhooks**; failover left empty; save verified persisted by re-opening the app. Nothing else changed.
- The tunl tunnel service is still running but now unused by Telnyx; harmless as a fallback.
- **NEXT: qualification call re-run on the Pinggy transport — needs Nathan's fresh per-call authorization.** Then handoff §13 acceptance items 1–10.


## Qualification call #2 Oct 30, 2026, ~10:34am EDT — TRANSPORT PROVEN, STT handshake still broken

- Three pre-dial aborts first (all zero-charge): (1) dialer approval env flag unset; (2) dialer public health gate failed — **Pinggy's HTTP/1.1 response path drops response bodies for urllib clients** (IncompleteRead, 0 of 35 bytes; curl through the same path gets the full body every time) — gate patched to probe the public URL via curl (still requires HTTP 200 + the real health JSON; dialer file only); (3) DIAL 401 — the dialer's telnyx_post fell back to the **vault's stale Telnyx key** (ID KEY01A1889A9FC…, not the validated one) because TELNYX_API_KEY wasn't in its env; fixed by exporting the key from `.provider-credentials` (validated ID KEY01A18ACB148B…) into the dialer env. Standing rule: always run the dialer with TELNYX_API_KEY exported from the credentials file.
- Attempt 48426db5-87c5-4f10-843d-a936c8c126a6: **DIAL 200, ANSWERED in 8.0s, STREAMING_START → 200 ok** (Oct 29 was 422/90046 — the media WebSocket through Pinggy CONNECTED), hangup at 28.6s total. Nathan confirmed: call came through, **he heard silence**.
- Server log at call time: "ws-media event #1 track=inbound" (his audio path reached the server) BUT "Service#0 exception (websockets/client.py:153): invalid Connection header: close" + ErrorFrame — the **Groq STT service's outbound WebSocket handshake died**: the far side's upgrade response carries `Connection: close`, which websockets 17.3 strictly rejects. Same bug class as Oct 29's second test call; the Oct 29 loopback proxy shim (`server/proxy_shim.py`, service ai-calling-proxy-shim :18080) fixed the TTS side, but STT was never routed through it and the scoped patch never landed.
- No transcript/cost record produced (pipeline died at Service#0). NEXT: route the STT connection through the shim (or equivalent pipeline-local patch), verify the handshake without dialing, then qualification call #3 on Nathan's fresh authorization.


## CORRECTION + FIX Oct 30, 2026, ~10:43am EDT — the failing service was TTS, not STT

- **Correction to the entry above:** "Service#0" is the ElevenLabs TTS service (the dynamic `Service` class in `CachedElevenLabsTTSService.create`, live_pipeline.py:614), NOT Groq STT. Pipecat 1.8.0's GroqSTTService opens no WebSocket (segment HTTPS to api.groq.com; preflight green, models endpoint 200). Nathan heard silence because **TTS never connected**.
- **True root cause (wire-proven):** this VM's egress proxy TLS-intercepts the ElevenLabs connection (peer cert issuer "Hatch Sandbox Egress CA") and re-originates the 101 upgrade response with **`Connection: close`** instead of `upgrade`; websockets 17.3 rejects it in check_response. The Oct 29 loopback shim CANNOT fix this — probes showed the bad header is on the 101 inside the tunnel, identical via egress proxy, via shim, and via shim+auth (the CONNECT response itself carries no Connection header).
- **Fix (one file, `server/live_pipeline.py`):** `_CloseHeaderTolerantConnection` in `CachedElevenLabsTTSService.create()` — normalizes the handshake response's Connection header (drop `close`, ensure `upgrade`) just before websockets validates it, passed via a `Service._websocket_connect` override as `create_connection`. Scoped to this service only; no library changes; deleting the class restores stock behavior.
- **Verification (no dial):** stock probe reproduced `InvalidUpgrade` on all paths; the production-Service probe after the fix: HANDSHAKE OK + ping/pong OK. ai-calling-live restarted; readyz green; preflight green; suite 129/129.
- **Still unproven:** real call audio + full STT→Gemini→TTS loop — needs qualification call #3 on Nathan's fresh authorization. NOTE for future diagnosis: transcript-side (STT/LLM) health and voice-side (TTS) health are separate; check which Pipecat "Service#N" failed before naming the culprit.


## Qualification call #3 Oct 30, 2026, ~10:45am EDT — still silent; next layer found

- Attempt 00f4cb50-e576-494e-9600-a14fb8bf08db: DIAL 200, answered 6.0s, STREAMING_START 200, 21.4s, Nathan: **no audio again**; no transcript/cost record.
- Server journal for the whole call window has ONE pipeline entry (10:45:41): **[nltk_data] Error loading punkt_tab — SSRF attempt to restricted IP 198.18.224.216** (NLTK's downloader refuses the egress proxy's address). punkt_tab data exists NOWHERE on disk. Pipecat's sentence tokenizer (`pipecat/utils/string.py`) loads punkt_tab on first use (download or NLTK_DATA) — TTS services aggregate text into sentences with it before synthesis. Leading diagnosis: TTS connects (fixed) but sentence aggregation dies on the missing tokenizer data, so no speech is ever produced. Same error appeared in call #2's window (masked then by the TTS handshake failure) — stacked failures, each visible only after the previous fix.
- Fix work delegated: vendor punkt_tab under the project tree + wire NLTK_DATA, and verify by synthesizing the opening line through the production TTS path WITHOUT dialing. Qualification call #4 waits on that proof + Nathan's fresh authorization.


## punkt_tab FIXED + VERIFIED Oct 30, 2026, ~10:52am EDT — production path produces audio

- **Cause chain proven** (job 32b7d515 timeline from webhook.db): initiated 14:45:29 → answered :33 → streaming :38 → punkt_tab error 14:45:41 → streaming.stopped :41.99 → hangup :44. The opening is queued as an AggregatedTextFrame(SENTENCE); Pipecat's TTS aggregation (match_endofsentence) calls `_sent_tokenizer()`, whose nltk.download dies on the SSRF guard and `sent_tokenize` then raises **LookupError: Resource 'punkt_tab' not found** — reproduced in isolation on the opening line. The raise happens before synthesis, so zero TTSAudioRawFrames were ever produced. It blocks ALL bot speech, every turn.
- **Fix:** official punkt_tab package fetched via curl through the egress proxy (4.3MB, integrity OK), unpacked to **`~/workspace/ai-calling/nltk_data/tokenizers/punkt_tab/`** (chmod go-w — NLTK's path security refuses group-writable data dirs), and **`Environment=NLTK_DATA=/home/hatch/workspace/ai-calling/nltk_data`** added to `systemd/ai-calling-live.service` + installed unit. Service restarted (PID 34022), readyz green, zero punkt errors since. No Python code changed.
- **No-dial verification through the production path:** probe built the TTS service exactly as build_live_pipeline does (fresh phrase-cache, real synthesis) and queued the opening line the production way: "Generating TTS", TTFB 0.281s, **AUDIO_FRAMES=2, AUDIO_BYTES=89,560 (~2.8s of 16kHz PCM), zero error frames**. Suite still 129/129.
- **Only remaining unknown:** outbound frames through the Telnyx serializer on a real call. Qualification call #4 = Nathan's fresh authorization. Deploy note: after a VM replacement, the deploy script restores the server; the NLTK_DATA unit env is in the in-tree unit file — when re-installing units manually (tunl/pinggy lesson), the ai-calling-live unit must come from the tree too so NLTK_DATA is present.

## Qualification call #4 — Oct 30, 2026, 11:55am EDT (attempt 54419e2d, job ec125dc1)
- Nathan authorized in main chat ("Do the phone call" / "To me"). Pre-dial: readyz initially failed on provider_preflight TTL (receipt from the 10:50 restart was 65 min old, TTL 3600s) — service restart refreshed it green; local + public readyz true before dialing. Dialer run with TELNYX_API_KEY exported from .provider-credentials (58 chars), AI_CALLING_PUBLIC_HOST=aqlmfmjrsk.a.pinggy.link, AI_CALLING_DIAL_APPROVED=YES_THIS_TEST_CALL.
- Timeline (webhook.db, EDT): initiated 11:55:02 → answered 11:55:20 → streaming.started 11:55:23 → **streaming.stopped 11:55:27 (~3.8s of media)** → call.dtmf.received 11:56:16 → no hangup recorded.
- **Nathan's account (his words, Oct 30 12:00pm):** it rang, he didn't answer, it went to voicemail, he then answered it from voicemail, heard nothing, and pressed a bunch of numbers. Matches the records exactly: the "answered" leg was voicemail; the DTMF was his keypresses; he heard silence throughout, and no assistant voice was captured on the voicemail either ("No").
- **The dialer process AND the whole stack were then killed by a runtime/VM replacement (~11:56:37)** — systemd units wiped again; journal evidence for why streaming stopped at 3.8s was destroyed with the VM. Cause of the 4-second streaming death: UNKNOWN, evidence lost. Do not guess.
- Scorecard: dial OK, media WebSocket CONNECTED (first time — calls #2/#3 never carried media), audio heard by Nathan: NONE, conversation: none. §13 items remain untested.
- Recovery completed ~11:59am: deploy re-run (129/129, DEPLOY_PASS), ai-calling-live + ai-calling-pinggy units reinstalled/enabled/active, local + public readyz {"ready":true}. Stack sits ready; a re-dial needs Nathan's fresh per-call go-ahead.

## Qualification call #5 — Oct 30, 2026, 12:00pm EDT (attempt 6cf59482, job 3af1eb8f)
- Nathan's fresh authorization ("Go ahead"). Pre-dial: services active, readyz true local + public.
- Dialer: DIAL 200, ANSWERED in 7.0s (Nathan live this time), STREAMING_START 200, MONITOR hangup at 35.0s, FINAL_STATE hangup, DURATION 46.4s, exit 0. Infrastructure held — no restart, clean end.
- **Nathan's report: "No speaking"** — he heard no assistant speech at any point in 46 seconds.
- Dialer: "no transcript/cost record found" (fifth consecutive call with no record). Attempts row: state=hangup; job spent_cents=0, billing_reconciled=0.
- Observability gap: journalctl -u ai-calling-live contains NO entries for the call window on this VM instance (only the 11:59 startup), and no per-call log file was written under logs/. The pipeline's live-session logs are not landing anywhere found yet — itself a defect to fix before further live dials.
- Verdict after 5 calls: dial + media connect are proven; bot speech on a live call has NEVER occurred, though the no-dial production-path probe synthesized real audio (Oct 30, ~10:52am). Next step is bench diagnosis of the live media-session startup (drive the media WebSocket endpoint with a synthetic Telnyx-protocol peer locally; assert the opening is emitted and speech-in produces a turn), NOT another live dial. No further dials until the bench reproduces speech + a recorded turn, and then only on Nathan's fresh authorization.
