# AI Calling Project - Return Handoff to Muse

**Prepared:** October 29, 2026
**Purpose:** Return the independently engineered and verified source changes to Muse for deployment and controlled live qualification.
**Controlling document:** This file supersedes older implementation instructions in `ai-calling/docs/` and the original Oct 29 state at the top of `PROJECT_STATE_2026-10-29.md`. Historical documents remain included as evidence.

## 1. Executive status

The exported working tree is offline-complete and deployment-ready. The live voice path is now:

```text
Telnyx Call Control
  -> signed FastAPI webhook and durable canonical adapter
  -> inbound-only PCMU media WebSocket
  -> 16 kHz PCM and exact 32 ms framing
  -> local Silero VAD
  -> Groq Whisper segmented STT
  -> Gemini Flash-Lite fast brain
  -> Gemini Flash frontier brain when escalation is required
  -> ElevenLabs Jessica TTS
  -> Telnyx media output
```

No call was placed from this returned package. No provider account was changed, funded, upgraded, or contacted. Real credentials are excluded. Deployment and one controlled live qualification call remain Muse-operated external steps.

Final offline evidence:

- Project tests: **129/129 PASS**
- Independent reference tests: **57/57 PASS**
- Deliberate reference mutations: **3/3 caught**
- Independent reference manifest: **28/28 valid**
- Universal hash-locked dependency installation: **PASS**
- Python AST parse: **27 files valid**
- Secret-pattern scan: **no matches**
- Artifact ship gate: **PASS**
- Final fresh-context verifier: **No code blockers found**

## 2. Package layout and merge map

The ZIP contains the complete return export, including historical evidence. Deploy only the active project files.

| ZIP path | Muse destination | Action |
|---|---|---|
| `ai-calling/server/` | `~/workspace/ai-calling/server/` | Replace/merge active server source |
| `ai-calling/tests/` | `~/workspace/ai-calling/tests/` | Replace/merge active tests |
| `ai-calling/systemd/` | `~/workspace/ai-calling/systemd/` | Replace unit masters |
| `ai-calling/docs/` | `~/workspace/ai-calling/docs/` | Retain as historical and audit material |
| `ai-calling/test-pack-v1/` | `~/workspace/ai-calling/test-pack-v1/` | Keep byte-identical independent test pack |
| top-level `tools/` | `~/workspace/ai-calling/tools/` | Merge deployment, verification, and dial tools |
| `second-adapter/` | archive only | Do not deploy; canonical adapter is `server/telnyx_adapter/` |
| `investigator/` | archive only | Keep as audit evidence |
| `PROJECT_STATE_2026-10-29.md` | project documentation | Read post-export update first |
| this handoff | project root | Keep as the controlling return handoff |

Do not copy any `__pycache__`, temporary virtual environment, generated preflight receipt, phrase-cache audio, webhook database, or credential file from another machine.

## 3. Provider migration

### 3.1 Listening: Groq Whisper

OpenAI Realtime STT is removed from the active live path. Pipecat 1.8.0 `GroqSTTService` now handles request-based Whisper transcription.

- Default pool: `whisper-large-v3-turbo,whisper-large-v3`
- Local Silero VAD remains the speech boundary.
- Pipecat buffers the speech segment, wraps it as WAV, and sends one Groq request at VAD stop.
- Only finalized transcription frames enter the transcript and Gemini context.
- Duplicate finalized frames are ignored by `(user_id, timestamp, text)`.
- Interim frames do not trigger Gemini or escalation.

### 3.2 Thinking: Google Gemini

OpenAI LLM services are removed from the active live path.

- Fast pool default: `gemini-3.5-flash-lite`
- Frontier pool default: `gemini-3.8-flash`
- Runtime model switching uses Pipecat's supported `LLMUpdateSettingsFrame` path.
- Fast replies are limited to one sentence.
- Frontier replies are limited to two sentences.
- Frontier remains bounded by three completed replies and 120 seconds, whichever occurs first.
- Nathan loop-in remains a spoken graceful-end action. Actual bridge-to-Nathan dialing remains unimplemented.

### 3.3 Speaking: ElevenLabs Jessica

Jessica remains the selected stock voice:

```text
voice id: cgSgspJ2msm6clMCkdW9
model: eleven_flash_v2_5
sample rate: 16000 Hz raw PCM
```

The active TTS constructor now uses Pipecat settings instead of deprecated direct `voice_id` and `model` arguments.

## 4. Audio and VAD corrections

### 4.1 Inbound-only stream

`streaming_start_request()` now defaults to `stream_track=inbound_track`. The live serializer still accepts `inbound` and `inbound_track` and rejects outbound media as a defense-in-depth control.

### 4.2 Exact 32 ms VAD input

`InputAudioRechunker` sits between Telnyx input and `VADProcessor`.

- Requires 16 kHz, mono, signed PCM16.
- Emits exactly 1,024 bytes per frame, which is 32 ms at 16 kHz mono.
- Keeps partial bytes until the next frame.
- Clears an incomplete tail at call end rather than padding synthetic audio.

This removes the reproduced Pipecat 1.8.0 buffer-boundary condition where trailing failing windows in one 3,032-byte frame could erase a qualifying VAD start run before SPEAKING confirmation.

### 4.3 Media accounting

Call records include:

- accepted PCMU payload bytes;
- observed sequence gaps;
- duplicate or old sequence events;
- caller-audio frame counts and sample rates in structured logs;
- VAD confidence, volume, and state transitions;
- answered/hangup event reconciliation.

## 5. Fixed phrase cache

Five fixed Jessica phrases are generated once during service prestart and replayed as cached raw PCM:

1. Approved disclosure
2. Silence reprompt
3. Silence goodbye
4. Nathan loop-in line
5. Sensitive-information refusal

Cache entries are keyed by exact text, voice, model, output format, and sample rate. Each entry has a metadata sidecar containing:

- `content_type=audio/pcm`;
- `encoding=pcm_s16le`;
- sample rate;
- channel count;
- byte count;
- duration;
- audio SHA-256;
- source-text SHA-256.

Cache loads fail closed on missing, malformed, mismatched, empty, odd-length, implausibly short, or implausibly long audio. Writes use unique temporary names and atomic replacement.

`prestart.py` calls the ElevenLabs direct synthesis endpoint only for missing phrases. It verifies an `audio/*` response and stores `pcm_16000`. `/readyz` refuses readiness unless all five exact phrases validate.

The opening is now required to equal this exact approved disclosure so a job cannot bypass cache readiness:

```text
Hi, this is Nathan's assistant calling on his behalf.
```

Terminal cached speech waits for internal TTS completion and then drains the known PCM duration plus 250 ms before canonical carrier hangup.

## 6. Conversation behavior

### 6.1 Silence

- Silence timeout default: 12 seconds after bot speech completes.
- First timeout: cached "Are you still there?"
- Second timeout: cached goodbye, drain, then canonical hangup.
- Silence count resets only on a finalized caller transcription.
- The timer pauses while caller or bot is speaking.

### 6.2 IVR and voicemail

Strict early-call terminal patterns cover common menu and voicemail language such as:

- "please press ...";
- "main menu";
- "say billing" or "say sales";
- "enter your extension";
- "choose from the following";
- voicemail/tone prompts.

These patterns apply only before a prior completed conversational turn, reducing false hangups when a human later mentions a keypad or voicemail.

### 6.3 Pronunciation hints

`LiveCallJob.pronunciation_hints` can carry approved word-to-pronunciation mappings. They are included in the Gemini system prompt. Do not place secrets or forbidden identifiers in pronunciation hints.

### 6.4 Spoken and persisted secret guards

Outgoing and persisted transcript text is checked/redacted for:

- SSNs;
- card-like digit runs;
- OTP/verification/security codes;
- routing and account numbers;
- password assignments;
- Authorization header values;
- Google and ElevenLabs key-header values;
- API-key-shaped values;
- Secure Vault surrogate values.

Forbidden outgoing content is replaced by the fixed refusal line. Persisted matches are replaced with `[REDACTED]`.

## 7. Cost and quota controls

### 7.1 Updated estimates

The local monetary estimator includes:

- Telnyx outbound voice: approximately 0.7 cents/minute;
- Telnyx media streaming: approximately 0.35 cents/minute;
- ElevenLabs Flash: approximately 5 cents/1,000 input characters;
- Groq/Gemini monetary estimate: zero while within the configured free tiers.

These remain estimates, not bills.

### 7.2 Per-call gates

- Test-call hard budget: $1.00
- Per-call soft cap: $5.00
- Monthly ledger cap: $20.00
- Default per-call TTS character limit: 4,000
- Carrier `time_limit_secs`: approval `max_minutes * 60`
- Independent server max-duration watcher

### 7.3 Provider quota ledger

SQLite reserves configured worst-case usage during media-job registration:

- Groq audio seconds;
- Gemini requests;
- ElevenLabs characters.

Media-job registration and all quota reservations occur in one transaction. If any quota would exceed its configured ceiling, registration fails and no quota or job is stored.

At call end, reservations settle down to measured usage. Expired, unclaimed jobs release their reservations. Default systemd quota ceilings are conservative placeholders and Muse must compare them with the current provider consoles before enabling the service.

## 8. Durable media jobs and canonical adapter

The in-memory registry is no longer authoritative. Media jobs are stored in the canonical adapter SQLite database.

- Registration is durable across service restart.
- Attempt IDs are permanently single-use.
- Jobs expire after five minutes by default.
- The WebSocket path first peeks the job.
- Telnyx route/control identity is authorized before claim.
- A successful authorization atomically claims the media job.
- A second or expired claim is rejected.

The canonical adapter remains `server/telnyx_adapter/`. The second adapter is archived comparison material only.

## 9. Readiness and provider preflight

### 9.1 Endpoints

`/healthz` is liveness only.

`/readyz` returns HTTP 503 unless all of these pass:

- fixed public hostname configured;
- hostname is not `.lhr.life` and is not URL-shaped;
- all four provider credentials exist;
- Groq/Gemini model pools exist;
- provider quota ceilings are positive;
- provider preflight receipt is successful and less than one hour old;
- all five phrase-cache entries validate;
- SQLite is readable;
- log directory is writable.

Readiness is observational. It does not generate provider traffic or write/delete probe files.

### 9.2 No-generation provider preflight

`server/provider_preflight.py` performs authenticated control-plane GETs only:

- Groq model list;
- Gemini model list using `x-goog-api-key` header;
- ElevenLabs configured voice;
- ElevenLabs model list.

It verifies configured model availability and writes `logs/provider-preflight.json`. It does not submit audio, prompts, or TTS generation.

### 9.3 Prestart

`server/prestart.py`:

1. Uses pre-provisioned environment variables or resolves the approved vault bridge in process memory.
2. Runs provider preflight.
3. Prewarms missing fixed phrases.
4. Deletes JSONL call records older than 30 days.

## 10. Dependencies and deployment

### 10.1 Reproducibility

`server/requirements.lock` is a universal Python 3.12 lock with distribution hashes. A clean Windows verification environment successfully installed it using `--require-hashes`. The Linux deploy script uses the same hash enforcement.

### 10.2 Deployment-local files

Create these outside version control:

```text
~/workspace/ai-calling/.provider-credentials
~/workspace/ai-calling/.webhook-public-key
~/workspace/ai-calling/.internal-token
```

Use `server/provider-credentials.example` as the variable-name template. Never copy values into source, chat, or the return ZIP.

Required provider variables:

```text
GROQ_API_KEY
GEMINI_API_KEY
ELEVENLABS_API_KEY
TELNYX_API_KEY
AI_CALLING_PUBLIC_HOST
ELEVENLABS_VOICE_ID
```

`AI_CALLING_PUBLIC_HOST` must be a fixed hostname only, without `https://`, path, or slash.

Secret files must be owned by `hatch` or `root` and mode 0600 or 0400. The public webhook-key file may also be 0644.

### 10.3 Linux deployment

From the live project root:

```bash
chmod 700 tools/deploy_linux.sh
AI_CALLING_PROJECT=/home/hatch/workspace/ai-calling tools/deploy_linux.sh
```

The script:

- verifies required files and permissions;
- creates a new venv;
- installs the hash-locked dependencies;
- runs all project tests before activation;
- snapshots old venv and systemd units;
- swaps the venv;
- installs both unit masters;
- restarts proxy then live service;
- verifies `/healthz` and `/readyz`;
- rolls back venv and units on failure;
- handles both first deployment and upgrade rollback;
- never dials.

### 10.4 Systemd hardening

Both units run as `hatch` and include:

- `UMask=0077`;
- `NoNewPrivileges=true`;
- private temp/devices;
- read-only home/system protections;
- kernel protections;
- address-family restrictions;
- CPU, memory, and task limits;
- restart burst limits.

The proxy shim also enforces a maximum of 32 simultaneous local connections.

## 11. Stable public hostname

The old localhost.run workflow is no longer accepted as deployment-ready. Known `.lhr.life` values fail readiness.

Muse must provision or select a fixed TLS hostname/tunnel outside this package, then:

1. Set `AI_CALLING_PUBLIC_HOST` to the hostname only.
2. Point Telnyx primary webhook to `https://<host>/telnyx/webhooks`.
3. Point failover webhook to a valid stable endpoint or deliberately remove it.
4. Verify `https://<host>/healthz` and `https://<host>/readyz`.
5. Read back the Telnyx configuration before any dial.

The existing reserved tunl slot decision was previously parked. If Nathan still does not want to repurpose it, Muse must choose another fixed-host mechanism with Nathan before live qualification.

## 12. Test and verification commands

From `~/workspace/ai-calling/` with the deploy venv:

```bash
.deploy-venv/bin/python -m unittest discover -s tests -v
```

Expected: **129 tests, all passing**.

Reference pack:

```bash
cd test-pack-v1/Muse_Telnyx_Integration_Test_Pack
../../.deploy-venv/bin/python run_tests.py --output /tmp/reference-tests.json
../../.deploy-venv/bin/python run_mutations.py --output /tmp/reference-mutations.json
```

Expected:

- 57/57 reference tests passing;
- 3/3 mutations caught.

No-dial provider/deployment verification:

```bash
.deploy-venv/bin/python server/provider_preflight.py
curl --fail http://127.0.0.1:8001/healthz
curl --fail http://127.0.0.1:8001/readyz
```

## 13. Controlled live qualification

Do not dial merely because deployment passes.

Before the first call Muse must have:

1. Nathan's fresh per-call authorization.
2. Exact destination and verified-number status.
3. Maximum minutes and spending cap.
4. Confirmation that the recipient agreed to transcription/recording.
5. Current provider quota checks.
6. Stable public and local readiness both passing.

The measurement dialer requires:

```text
AI_CALLING_PUBLIC_HOST=<fresh fixed host>
AI_CALLING_DIAL_APPROVED=YES_THIS_TEST_CALL
```

First live acceptance sequence:

1. Exact disclosure is first audible speech.
2. Disclosure sounds like Jessica and is not truncated.
3. Caller speech reaches Groq exactly once per completed turn.
4. Gemini fast reply is heard.
5. Sustained narrowband speech reliably reaches SPEAKING.
6. Barge-in interrupts TTS.
7. Fast-to-frontier switching works.
8. Three-turn and 120-second bounds work.
9. Terminal phrase is fully heard before hangup.
10. Silence reprompt then goodbye works.
11. Canonical hangup ACK and `call.hangup` webhook reconcile.
12. Transcript redaction, media counters, quota settlement, and cost estimates are present.

Update `PROJECT_STATE_2026-10-29.md` in place after the result and append the live call to the existing call history. Never rewrite prior call evidence.

## 14. Known external/live-only gates

The returned code has no known offline blocker. These facts cannot be proven without the target VM/accounts or an authorized call:

- Groq, Gemini, ElevenLabs, and Telnyx credentials are valid and enabled.
- Account-specific free-tier quotas match the configured ceilings.
- Fixed hostname TLS and Telnyx webhook delivery work.
- Telnyx emits the expected live media sequence fields.
- Real caller speech quality after PCMU/resampling is acceptable.
- Cached Jessica PCM is audible and naturally paced over PSTN.
- Canonical hangup receives a real Telnyx ACK and hangup webhook.
- Florida/TCPA and recipient-consent requirements are satisfied for the exact call.

## 15. Do not regress these invariants

- Do not restore OpenAI to the live STT/LLM path without a new explicit decision.
- Do not modify installed Pipecat files.
- Do not restore both-tracks media as the default.
- Do not remove exact 32 ms framing before VAD.
- Do not consume a media job before route/control authorization.
- Do not issue direct random-command hangups outside the canonical adapter.
- Do not add automatic redial after an unknown submission.
- Do not place provider keys or vault surrogate values in source/logs.
- Do not treat estimated cost as a provider bill.
- Do not deploy without hash verification, project tests, preflight, and `/readyz`.
- Do not dial without current per-call authorization.

## 16. Files most important to review first

1. `server/live_pipeline.py`
2. `server/live_server.py`
3. `server/telnyx_adapter/adapter.py`
4. `server/telnyx_adapter/wire.py`
5. `server/provider_preflight.py`
6. `server/prewarm_phrases.py`
7. `server/prestart.py`
8. `server/model_select.py`
9. `server/requirements.lock`
10. `systemd/ai-calling-live.service`
11. `systemd/ai-calling-proxy-shim.service`
12. `tools/deploy_linux.sh`
13. `tools/dial_measure_20261029.py`
14. `tests/test_live_pipeline.py`
15. `tests/test_telnyx_adapter.py`
16. `tests/test_live_server.py`
17. `tests/test_audio_replay.py`

---

**Return state:** source and offline verification complete; target deployment, fixed hostname, account credentials, consent, and one controlled live qualification remain with Muse and Nathan.
