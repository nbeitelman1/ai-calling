# Investigator Handoff — Self-Hosted AI Calling Test Calls (Nathan Beitelman)

**Date:** Thursday, October 29, 2026 (evening EDT)
**From:** Pip (Muse), Nathan's personal assistant — written for Nathan's investigator (human or agent)
**Subject:** Everything wrong so far with the self-hosted Telnyx → Pipecat → OpenAI/ElevenLabs voice pipeline, what is proven, what is not, and what to do next
**Status at time of writing:** The bot can speak and be heard, but it has never once heard or answered the callee. One live call (the "measurement call") is still needed to pick the correct voice-detection settings from real data. UPDATE 4:46pm EDT: the measurement + inbound-filter fix has now been applied, verified offline, and the live service restarted with it — the measurement call is ready to run on Nathan's go-ahead (see §7, step 1, updated).

**CORRECTION — 4:53pm EDT, from the investigator's verdict (see `docs/INVESTIGATOR_VERDICT_2026-10-29.md`): the P11 inbound filter below is under question and the measurement dial is PAUSED until it is resolved. The deployed filter drops media events whose track is not `None`/`"inbound_track"` — but Telnyx's WebSocket `media.track` value is documented as `"inbound"` (the `inbound_track` form is the Call Control *request* parameter, not the wire event value), and the project's `"inbound_track"` wire value comes only from self-invented fixtures, never from an observed wire event. If the wire value is `"inbound"`, the current filter drops ALL caller audio on the next call. Do not dial until the filter accepts the verified wire value (proposed: accept both `"inbound"` and `"inbound_track"`, with drop logging). The investigator also reproduced a Pipecat 1.8.0 buffer-boundary VAD behavior (see P10 note): confirmation happens only after a whole input buffer is processed, so a 6-window passing run can be reset by a failing window in the same buffer — "6 consecutive chunks" alone is an incomplete explanation.**

## How to use this document

- §1 is the whole story in one page. §2–§3 are the evidence. §4 is the problem register — work through it top to bottom. §5 is what is proven vs. merely believed. §6 is outside research that bears on the open problem. §7 is the ordered next-steps list. §8 is the constraints ledger — do not violate it without Nathan's explicit approval.
- **No secrets are in this document.** API keys, tokens, and proxy-auth material are never written down anywhere in this project. Keys live only in Muse's Secure Vault and are resolved into process memory at server startup. The internal media-job token lives only in `~/workspace/ai-calling/.internal-token` (0600). If you need a credential, Nathan creates it and stores it through the vault — never ask for one in chat.
- **Do not dial without Nathan's go-ahead.** Test calls so far run under his standing approvals (see §8). A new dial, a funding step, or a Telnyx tier change each need his fresh approval.

## 1. TL;DR — current state

- **Outbound audio works.** Since the Oct 29 ~4:06pm call, the callee hears the disclosure verbatim, and Nathan (listening live) confirms the Jessica voice "sounded great."
- **Inbound audio is the entire remaining problem.** The callee has spoken at length on two calls. The transcript has come back empty every single time. No conversational turn has ever completed: no barge-in, no understanding, no escalation test has ever been reached.
- **The delivery chain is proven end to end on the latest call (4:31pm):** Telnyx forwards inbound media (2,500+ media events), frames reach the pipeline (500+ frames at 16 kHz), and the voice detector still never fires.
- **Best current explanation:** Silero VAD confidence on narrowband phone speech hovers near the 0.7 trigger and flaps, while the state machine demands 6 consecutive passing chunks (192 ms) and resets on any single failing chunk. A second, real aggravator: the media stream carries both directions and the pipeline was mixing them into one audio stream (an inbound-only filter is now written to disk, pending service restart).
- **What is NOT the problem anymore (ruled out with evidence):** Telnyx not forwarding media, the media serializer rejecting Telnyx's format, the transport not being attached, a crashed receive loop, the VAD sample-rate pinning bug (fixed), Smart Turn interference, or the callee staying silent.
- **Two honest mistakes in this investigation are recorded in §4 (P9, P12).** The most important one: an earlier claim that "no audio arrived" was based on a logging instrument that was itself broken. Treat any single-instrument conclusion in this project as suspect until cross-checked.

## 2. System map

- **Telephony:** Telnyx Call Control API v2, Business Trial account. Number +1 321 497 8649 (Melbourne, FL, voice-enabled). App "Nathan AI Assistant (Call Control)", connection `3081546725312169326`, webhook API v2, outbound voice profile "Default" (`3080348738397406661`). Callee +1 321 367 7796 (Lauren) is a verified number on the account (trial accounts may only call verified numbers).
- **Public reachability:** localhost.run anonymous tunnel (`/tmp/start_tunnel.sh`, `ssh -R 80:127.0.0.1:8001 nokey@localhost.run`). Hosts live roughly 15–30 minutes and churn without warning; every dial requires re-verifying the host and re-pointing the Telnyx app webhook. The webhook failover URL currently points at a dead host (minor — primary is what matters, but fix it when convenient).
- **Live server:** FastAPI on 127.0.0.1:8001 (`server/live_server.py`, `server/start_live_server.py`), systemd unit `ai-calling-live.service` (master copy in `~/workspace/ai-calling/systemd/`). Endpoints: `GET /healthz`, `POST /telnyx/webhooks` (+ `-failover`), `WS /telnyx/media/{attempt_id}`, `POST /internal/register-media-job` (token-gated; wrong/missing token returns 404; registration is in-memory and lost on every service restart).
- **Voice pipeline:** Pipecat 1.8.0 (`server/live_pipeline.py`), deploy venv `~/workspace/ai-calling/.deploy-venv` (system Python lacks `cryptography` — always run dial/adapter scripts under the deploy venv). Pipeline order: transport input → frame tap → VADProcessor (Silero) → VAD tap → OpenAI Realtime STT → TranscriptTap → LLM aggregator → ElevenLabs TTS → transport output. Pipeline audio rate 16 kHz.
- **Models:** STT `gpt-4o-mini-transcribe`, LLM fast `gpt-4o-mini` / frontier `gpt-5` (dynamic selection, frontier capped at 3 turns / 120 s), TTS ElevenLabs Jessica `cgSgspJ2msm6clMCkdW9` (premade, female, American — stock synthetic only; never a clone of a real person).
- **Egress proxy:** VM egress intercepts direct TLS. A local proxy shim (`server/proxy_shim.py`, `ai-calling-proxy-shim.service`, 127.0.0.1:18080) rewrites CONNECT responses so websocket clients survive; the live unit sets http/https proxy vars to the shim.
- **Call control adapter:** `server/telnyx_adapter/` — canonical adapter, its own suite 37/37, full tree 81/81 at build. Single-use claim model: prepare → claim_submission → one dial with adapter-encoded base64 `client_state` (never the raw attempt id). Attempt ids are single-use; never reuse a consumed one (list in §3).
- **Tests:** `tests/test_live_pipeline.py` — 44/44 green throughout all of today's work.
- **Key files:** `~/workspace/ai-calling/server/live_pipeline.py`, `live_server.py`, `telnyx_media.py`, `model_select.py`, `voice_config.json`, `telnyx_adapter/`; logs in `~/workspace/ai-calling/logs/` (`webhook.db`, `test-call-2026-10-29-*.jsonl`, `tunnel*.log`); journal via `journalctl -u ai-calling-live.service`.
- **Reference material (not authored by Muse):** the Telnyx adapter test pack `~/workspace/user/files/Muse_Telnyx_Integration_Test_Pack_v1.zip` (28 files, manifest PASS, reference suite 57/57, mutations 3/3 caught); archived copy in `~/workspace/telnyx-adapter-work/`.

## 3. Complete call log — Oct 29, 2026 (all times EDT)

Test brief (approved by Nathan): call Lauren, Nathan present listening, max 10 min (expected ~5), hard cap $1.00. Disclosure verbatim first: "Hi, this is Nathan's assistant calling on his behalf." Allowed decisions: none except ending the call. Forbidden: SSN, card/bank numbers, codes, passwords, background-check consent, legal signatures, payments, promises.

| # | Time | Attempt id | Result |
|---|---|---|---|
| 1 | 2:41pm | `1ee9a900-…` | Dial REFUSED, no call. Telnyx 403: trial accounts may only call verified numbers. Fixed by verifying Lauren's number in portal (2:48pm). |
| 2 | 2:49pm | `5718bf54-…` | Connected, 45.1s, NO audio either direction. Both provider websockets failed TLS (`WRONG_VERSION_NUMBER`) — VM egress interception. |
| 3 | 2:59pm | `d74c6a0e-…` | Connected, 35.1s, NO audio. Both websockets failed "invalid Connection header: close" (websockets 17.3 strict validation vs. egress proxy). |
| 4 | 3:12pm | `6d2652ed-…` | Connected, 38.2s. STT connected and ready (proxy/shim proven). TTS failed: voice id "Natasha" does not exist on this account (1008). No speech. |
| 5 | 3:18pm | `a9dd234e-…` | Answered, hangup 0.3s after answer — Nathan accidentally tapped hangup as the phone rang. NOT a rejection by Lauren. Jessica untested. |
| 6 | 3:20pm | `af532f19-…` | Connected, ~29s, TTS websocket clean — but Nathan heard NO voice. Root cause P5 below. |
| — | ~4:04pm | (none) | Dial ABORTED: tunnel host died between verification and dial. No charge, no attempt consumed. |
| 7 | 4:06pm | `aed6cff3-…` | Answered 28.5s, callee hung up. Disclosure SPOKEN (journal + Nathan confirm) — first audio ever heard by callee. Lauren said "who is this", got ~21s silence, transcript []. |
| — | ~4:18pm | (none) | Dial ABORTED: tunnel host died between re-point and dial. No charge, no attempt consumed. |
| 8 | 4:20pm | `5a7c7650-…` | Answered ~35s monitor time. Disclosure spoken, Nathan: "sounded great" (voice quality confirmed by ear). Still no reply, transcript []. Instrument blind (P9) — this call's data is ambiguous. |
| 9 | 4:31pm | `05777655-…` | Answered, hangup ~40s monitor time (51s from dial). Disclosure spoken 20:31:17–20:31:21Z. Lauren "had a mouthful to say" (Nathan). Instruments working: ws-media #2500+, inbound frames #500+, ZERO VAD transitions, transcript []. |

Cost: Telnyx balance trail $3.90 → $3.82 across the day, then $3.84 after call 7 — inconsistent, so do NOT derive per-call cost from balance deltas. Only "well under the $1 cap" is solid for these short calls. Record files carry pipeline-estimated cents (e.g. 0.71, 0.86, 0.92) — those are estimates, not bills, and the `minutes` field on older records is pipeline lifetime, not call length. Use webhook event times for call length.

## 4. Problem register

### P1. Trial-account dial restriction — FIXED
Symptom: first dial refused 403. Cause: Telnyx trial accounts may only call verified numbers. Fix: Lauren's number verified in portal (SMS code relayed by Nathan). Evidence it held: all later dials returned 200. Residual: any NEW business number dialed later will hit the same 403 until verified or the account tier is upgraded (upgrade needs funding — Nathan cancelled the $20 funding Oct 29; fresh approval required).

### P2. Egress TLS interception — FIXED
Symptom: provider websockets failed TLS directly. Cause: VM egress transparently intercepts direct TLS. Fix: local proxy shim (127.0.0.1:18080) + proxy vars in the live unit. Proven: keyless handshakes passed TLS after the shim; STT connected live on call 4.

### P3. Strict websocket `Connection` header validation — FIXED
Symptom: both provider websockets failed with "invalid Connection header: close" (call 3). Cause: websockets 17.3 strictly rejects the egress proxy's CONNECT response / Cloudflare's `Connection: close` on a valid 101. Fix: in-memory patch in `live_pipeline.py` wrapping `ClientProtocol.process_response` — accepts ONLY status 101 + exactly one `websocket` Upgrade token + valid `Sec-WebSocket-Accept`; everything else re-raises. Synthetic cases 8/8; STT connected live thereafter. No venv library edits.

### P4. Nonexistent voice id — FIXED (by replacement)
The "Natasha" voice id (`kCx3Qoh3lfILbbTZftSq`) does not exist on Nathan's ElevenLabs account (proven by listing the account's 24 voices). Nathan picked Jessica (`cgSgspJ2msm6clMCkdW9`, premade, "Playful, Bright, Warm"). Do not use the Natasha id again. Professional + cloned voices on the account are EXCLUDED by Nathan's standing rule (stock synthetic only).

### P5. Sentence-aggregator lookahead trap — FIXED, proven live
Symptom (call 6): TTS connected cleanly, yet zero speech. Root cause, proven in Pipecat 1.8.0 code and offline repro: the disclosure was queued as a plain `TextFrame`; `SimpleTextAggregator` in SENTENCE mode buffers a sentence ending in punctuation until non-whitespace lookahead arrives, and nothing follows the lone disclosure — so it was held forever (302s in the live journal) and nothing reached synthesis. The "send more text" workaround is dead (NLTK `punkt_tab` download is blocked by the proxy SSRF guard). Fix: queue the disclosure as `AggregatedTextFrame(SENTENCE)` with byte-identical text. Proven live on calls 7–9 (TTS "Generating TTS [disclosure]" + bot speaking in journal, confirmed by ear).

### P6. Delayed / wrong call records — FIXED
Symptom: the call-6 record appeared minutes late with pipeline-lifetime minutes (5.103) and cost (7.65¢) instead of call facts. Causes: (a) no disconnect handler — FastAPIWebsocketTransport pushes no EndFrame on disconnect, so `await runner_task` only resolved at idle-timeout teardown; (b) `asyncio.CancelledError` inherits from `BaseException`, not `Exception`, so teardown bypassed the plain `except Exception`. Fix: race the runner against a transport-disconnect watcher (0.25s poll + adapter hangup check), write the record in a `finally` path, price from first observed hangup/disconnect, catch `(CancelledError, Exception)` explicitly. Records now write promptly on hangup (calls 7–9). Note: the adapter attempt row carries no callee-hangup timestamp, so watcher time (~0.25s accuracy) is the call-end timing.

### P7. Tunnel churn — OPEN, structural
localhost.run anonymous hosts die every ~15–30 minutes, repeatedly killing dials between verification and dial (two aborted attempts Oct 29, plus several earlier in the day). Mitigations in use: parent (only) restarts the tunnel, re-points the app via browser task, reads the value back, and dials immediately in the same motion — this worked for calls 7–9. The durable fix (a reserved tunnel address) is PARKED at Nathan's direction — do not pursue it unless he raises it. Also note: dial operators must never touch the tunnel themselves.

### P8. VAD sample-rate pinning — FIXED in code, live effect unproven
Symptom (call 7): STT session ready, zero VAD events, zero transcription. Cause: pipeline runs 16 kHz, but `SileroVADAnalyzer(sample_rate=8000)` was constructed, and `VADAnalyzer.set_sample_rate()` keeps the init rate when truthy — so the analyzer stayed at 8 kHz with 256-sample chunks, consuming 16 kHz audio at half speed (offline: max confidence 0.427, never leaves QUIET). Fix: `SileroVADAnalyzer()` with no rate arg, so pipeline setup applies 16,000 Hz (512-sample chunks). Offline repro after fix: rate 16000, max confidence 0.823, leaves QUIET. BUT: the very next live call (call 9) still showed zero VAD transitions — because of P10/P11, not because this fix is wrong. The rate fix is code-correct and the old pinning bug is genuinely gone; it simply was not sufficient.

### P9. Blind logging instrument — FIXED, with a correction on the record
Symptom (call 8): the frame-count tap logged nothing, which was reported to Nathan as "no audio arrived." That inference was WRONG. Cause: the tap logged via stdlib `logging` INFO, but the live server runs uvicorn at warning level with no root-logger config, so INFO is silently dropped. The tap passed frames through fine — it just could not log. Proven offline (frame passes, zero lines emitted without a logging config; lines appear with one) and by journal history (0 tap lines ever). Fix: tap switched to loguru (Pipecat's channel, proven to reach the journal), both instances; a `_CountingTelnyxSerializer` now also loguru-logs inbound media events (first + every 500th). Lesson: cross-check any single-instrument conclusion in this project.

### P10. VAD confidence fragility on narrowband speech — OPEN, the live problem
Symptom (call 9, instruments working): Telnyx forwards media (ws-media #2500+), frames reach the pipeline (#500+ at 16 kHz, 3,032 bytes), callee speaks at length, yet ZERO VAD transitions and transcript []. Mechanism (code + offline repro): Silero confidence on narrowband phone speech (8 kHz-origin μ-law, soxr-upconverted to 16 kHz) hovers near the 0.7 gate and flaps, while the state machine demands 6 consecutive passing chunks (start_secs 0.2 / 32 ms chunks) and resets STARTING→QUIET on any single failing chunk. Offline full-live-path arm: only 1% of chunks ≥0.7, best passing run 0. Volume is almost certainly NOT the blocker (smoothed volume 0.73–0.95 in all offline arms vs. 0.6 gate), but live audio level is unmeasured. Nuance: zero transitions prove never-SPEAKING only, not never-STARTING (STARTING is internal and unlogged — VAD may have blipped repeatedly with zero journal evidence). No synthetic signal has ever reached SPEAKING offline at either rate, so the gate theory is a strong lead, not a closed proof. Pipecat's own docs say the VAD defaults "generally work well" and to adjust them only after extensive testing with your specific audio conditions — which is why gate changes must wait for live measurement (§7), not be guessed now.

### P11. Dual-track interleave corruption — FIX (written, pending restart)
The live streams run `stream_track: "both"`, but `TelnyxFrameSerializer.deserialize` IGNORES the track field — every media event is decoded and forwarded. Message/frame rates (~83 media msgs/s, ~17.7 frames/s vs. ~50/~10 for one track) support both tracks flowing. Outbound-track packets (silence/bot echo) therefore get concatenated into the same resampler stream as inbound audio, chopping VAD input into alternating 20 ms slices; the offline interleave arm degraded confidence to 0.00. This is an aggravator, not the sole cause (the single-track full path already fails offline). Fix written to disk: `_CountingTelnyxSerializer` drops media events whose track is not `None`/`"inbound_track"` before delegating (mirroring the project's own `telnyx_media.py:214` rule, which is NOT in the pipeline path). Clearly correct regardless of the main cause. Pending service restart (§7).

### P12. Balance / record inconsistencies — OPEN, minor
Telnyx balance figures across the day do not reconcile to per-call costs (e.g. $3.82 before call 7, $3.84 after it). Do not price calls from balance deltas. Use webhook event times for duration and the record file's estimate only as an estimate. Separately, the post-teardown serializer auto-hangup POST bypasses the proxy shim (direct-TLS SSL failure) — harmless on callee hangup (nothing depended on that POST), but fix it before any call where a parent-side hangup matters.

## 5. Proven vs. unproven

PROVEN (live evidence):
- Dial, answer, streaming start, and webhook event flow work reliably once the tunnel/app are current.
- Disclosure TTS reaches the callee verbatim; Jessica's voice quality is confirmed good by ear (Nathan).
- Telnyx forwards inbound media; frames reach the pipeline at 16 kHz.
- The record-on-hangup fix writes promptly; tests stay 44/44.
- Trial-account 403 behavior, proxy/shim behavior, STT handshake behavior, and the Natasha nonexistence are all settled.

UNPROVEN / OPEN:
- Whether Silero VAD reaches SPEAKING on real phone speech at ANY gate setting.
- The live per-chunk confidence and volume values (the measurement call will supply these).
- Whether outbound-track packets actually flow during bot silence (track logging on the next call will settle it).
- Exact per-call Telnyx charges (only "well under $1" is solid).
- Whether the VAD rate fix (P8) matters live once P10/P11 are addressed (it is code-correct but its live effect is masked by P10).
- Barge-in, ordinary understanding, escalation behavior, and graceful multi-turn conversation — none have ever been exercised, because no turn has ever completed.

## 6. Outside research notes (checked Oct 29, 2026)

- **Pipecat VAD defaults** (Pipecat speech-input docs): `start_secs` 0.2 ("how long a user must speak before VAD confirms speech has started"), `stop_secs` 0.2, `confidence` and `min_volume` "generally work well with defaults — only adjust after extensive testing with your specific audio conditions." This supports the measurement-first plan: do not retune gates blind. Source: https://docs.pipecat.ai/pipecat/learn/speech-input
- **OpenAI Realtime STT turn detection** (Pipecat reference): local VAD is the default — `turn_detection=False` disables server-side VAD and the service commits the audio buffer when it receives `VADUserStoppedSpeakingFrame`. Server-side VAD (`turn_detection={"type": "server_vad"}`) makes the server propose turn boundaries, but requires a model that supports turn detection — `gpt-transcribe` does; the default `gpt-realtime-whisper` does not — and the docs say NOT to use a separate VAD processor in that mode. Our STT model is `gpt-4o-mini-transcribe`; whether it supports server_vad is UNVERIFIED and must be checked before anyone proposes switching. Note also: Pipecat sends audio to OpenAI as 24 kHz PCM, resampled from the pipeline rate internally. Source: https://reference-server.pipecat.ai/en/latest/api/pipecat.services.openai.stt.html
- **Telnyx media streaming tracks** (Telnyx docs): `stream_track` options are `inbound_track` (default), `outbound_track`, `both_tracks`. Our streams use `both_tracks`. Switching the stream itself to `inbound_track` at the Telnyx command level is an alternative to the in-pipeline filter in P11 — but note the pipeline also needs outbound audio for playback, which travels over the same websocket in bidirectional mode; verify how Pipecat's transport uses the stream before changing the track at the source. Source: https://developers.telnyx.com/docs/voice/programmable-voice/media-streaming
- **Strategic alternative worth evaluating (not a recommendation):** because OpenAI's Realtime API natively accepts G.711 μ-law at 8 kHz, an architecture that feeds Telnyx PCMU frames straight into the Realtime session with server-side VAD would bypass both the resampling step and the local Silero VAD entirely. That removes two of the three suspects in P10/P11 at the cost of a different turn-detection model and a different pipeline shape. Evaluate it only after the measurement call — if the measurement shows confidence is the binding constraint, this alternative becomes much more attractive than gate-tuning.

## 7. What to do next (in order)

1. **Confirm the pending restart — DONE 4:46pm EDT.** The measurement + inbound-filter fix (per-chunk confidence/volume logging, state-transition logging, track logging, inbound-only filter) was applied, verified offline (44/44 tests green), and the live service restarted with it. Before any dial, still: re-verify the tunnel host is alive, re-point the Telnyx app if it changed, and re-register the media job (the restart cleared the in-memory registry). Use a fresh attempt id under the $1 cap.
2. **Run ONE measurement re-dial** (needs Nathan's go-ahead; same test brief, $1 cap): fresh attempt id, re-verify tunnel host, re-point app, re-register the media job (restart clears it). Lauren speaks at length. Then read the journal IN THIS ORDER: `ws-media` counts (did Telnyx send?) → `inbound-audio` frames (did frames arrive?) → per-chunk confidence/volume values (which gate binds?) → VAD transitions → transcript. Also read the `track` values logged.
3. **Set gates from the measured data only.** Candidates, in order of safety: `start_secs` 0.2→0.1 (3 chunks instead of 6), `confidence` 0.7→0.5, `min_volume` 0.6→0.5 — pick from the data, and expect false-trigger risk to rise as gates loosen. Do not change all three at once; change one, re-measure.
4. **If VAD stays unreliable, decide the STT fallback with Nathan's explicit approval** (it changes STT behavior): server-side VAD on the OpenAI session (first verify `gpt-4o-mini-transcribe` supports `server_vad`; if not, evaluate a model change) or a silence-timeout commit. Do not bundle this silently with gate changes.
5. **Decide the stable tunnel question with Nathan.** The localhost.run churn tax (restart + re-point + re-dial race) has now killed three dial attempts. The reserved-tunnel option (tunl second subdomain) is parked at his direction; reopening it is his call. Note the relay already occupies the one reserved tunl subdomain (`piperelay.tunl.online`) — a second tunnel cannot share it.
6. **Clean up the small items:** point the webhook failover URL at a live host; route the serializer's auto-hangup POST through the proxy shim; reconcile the Telnyx balance against actual call records instead of deltas.
7. **Only after a full turn completes:** run the original 7 test checks end to end (disclosure, voice naturalness by ear, barge-in, ordinary understanding, escalation via an out-of-scope question, graceful end, transcript + cost log), then audition Jessica against 1–2 other stock feminine voices and review the Florida all-party-consent/TCPA disclosure before any business call.

## 8. Constraints and approvals ledger (do not break without Nathan)

- Test scope: calls to Lauren (+1 321 367 7796) only, Nathan present listening, max 10 min, $1.00 hard cap per test call. $20/mo ledger cap and $5/call soft cap remain binding for any future live business calls.
- No funding, no purchases, no tier upgrades without Nathan's fresh approval (he cancelled the $20 Telnyx funding Oct 29 — "test it out with that first").
- Approvals stay literal to their exact brief (two Oct 28 turns were flagged as boundary overreach): a go-ahead for one call does not stretch into a submission or a second step; a go-ahead for account setup does not authorize a live call.
- Disclosure opening verbatim: "Hi, this is Nathan's assistant calling on his behalf." Forbidden on calls: SSN, card/bank numbers, codes, passwords, background-check consent, legal signatures, payments without per-call approval, promises.
- Voice rule: stock synthetic only, never a clone of a real person. Jessica is the current pick; Natasha id is dead.
- Deepgram is DROPPED (two independently created keys probed 401 through the vault path; the vault cannot deliver a key embedded in a larger header value). Do not re-attempt it through that path. STT is OpenAI Realtime.
- tunl second-subdomain reservation is PARKED at Nathan's direction.
- Escalation ladder (approved Oct 29): fast model → frontier auto-escalation (max 3 frontier turns AND max 120 s) → Nathan. Loop-in bridging is NOT implemented (pipeline speaks a loop-in line, ends gracefully, records `loop_in_requested`).
- Watchdog timer for max call length does NOT exist yet (max_minutes is gated at approval, not mid-call). Hold/unhold ordering is unimplemented. Test-store SQLite is unencrypted (test-only).
- Known non-alarms (do not re-raise): Netflix changes = Mom; Oct 29 Telnyx/ElevenLabs/OpenAI sign-in alerts = Nathan's own walkthrough; Oct 28 Fidelity password change = Nathan's 401(k) change.

## Appendix: consumed attempt ids (never reuse)

`1ee9a900-e013-45ed-a5d8-ca6673d51929`, `5718bf54-68d1-4f27-866e-78c7a537329a`, `d74c6a0e-8021-4909-8c05-94ca780ebe70`, `6d2652ed-4806-4faa-9ff4-ae2031f1d7d4`, `a9dd234e-461a-4453-af6d-fb45ecdc7780`, `af532f19-f5e2-464f-89b2-aa2e9a13f3c1`, `aed6cff3-8d28-448c-9bea-62f4c7035fc6`, `5a7c7650-1c74-42e8-b25f-a07afc5d7da8`, `05777655-bd5f-4e3f-99ff-97f256be31bf`.

## Appendix: repro scripts (note: /tmp is ephemeral — these may be gone)

`/tmp/repro_disclosure.py` (aggregator trap), `/tmp/repro_hangup.py` (record-on-hangup), `/tmp/repro_inbound.py` (VAD rate), `/tmp/repro_vad_fix.py` (rate fix), `/tmp/repro_tap_loguru.py` (loguru tap), `/tmp/repro_inbound_transport.py` (full transport path), `/tmp/repro_vad_phone.py`, `/tmp/repro_vad_compare.py`, `/tmp/repro_vad_jittery.py`, `/tmp/repro_vad_decomp.py`, `/tmp/repro_vad_soxr.py`, `/tmp/repro_vad_bandlimit.py` (VAD-on-phone-audio series). Dial scripts: `/tmp/dial_test_call_jessica*.py` (jessica7 was the last used; each points at a dead tunnel host and must have its host + log path updated before any reuse — never rerun one unchanged).
