# AI Calling — offline debugging and improvement audit

**Date:** October 30, 2026  
**Decision:** Ready for controlled Muse testing; NOT certified for production calling.  
**Controlling inputs:** GitHub commit `03d21312caf16bda13d57bfeadf55635c50e2d8b` and the emailed `ai-calling-debug-handoff.zip`.  
**ZIP SHA-256:** `5547aa3ea5fdf5ac2a67c78dcd7d2436101cc1e645906dd5ba8d2b03defa3424`

## Executive conclusion

The silent-call investigation was not one codec setting. It crossed source-version drift, Pipecat processor lifecycle, cached TTS context ownership, startup sequencing, local audio buffering, and call termination. The original application suite passed 129 tests while the live media path contained failures it did not exercise.

This candidate incorporates the ZIP fixes, repairs independently reproduced defects, strengthens calling controls, and provides an offline test that runs the production media handler, real Pipecat pipeline/transport/serializer/context aggregators, and real cached-TTS implementation with synthetic provider substitutes. It emits non-silent PCMU, executes a synthetic conversation turn, checks exact-once assistant history, and writes a final call record.

**No carrier call was placed. No production credentials were used. No deployment or GitHub push occurred.** A local Git candidate is supplied for review and later merge. Original project-state documents remain historical inputs, not proof of this revision's readiness.

## How the investigation was run

Commander controlled scope, dependencies, and acceptance gates. Agent Swarm Orchestrator was used in two actual modes: parallel research/tool calls and independent deterministic workers in a Python thread pool. QA owned regression, synthetic, mutation, structural, and artifact-integrity checks. These were not separate autonomous LLM sessions.

Workstreams: source reconciliation; media/audio lifecycle; security/call controls; provider configuration/costs; runtime/deployment; regression; and handoff. Unrelated FORGE manufacturing/dictionary requirements were not applied to this non-DRS codebase.

### Evidence hierarchy

1. Checked-out source and exact package contents installed from `server/requirements.lock` with `--require-hashes`.
2. Executed tests and preserved before/after failures.
3. ZIP embedded code and handoff narrative, separated from measured results.
4. Current official vendor documentation used for corroboration.

Public documentation may move ahead of the pinned runtime. The implementation was checked against installed **Pipecat 1.8.0 / Python 3.12**, not copied from an arbitrary latest-version example.

## Source reconciliation

Eleven embedded source files were compared with the GitHub checkout. Nine matched after removing their Markdown wrappers. Two differed: `server/live_pipeline.py` and `server/live_server.py`.

The ZIP already addressed the undefined `silence_policy`, added a cached-audio context, and added exception visibility/readiness handling. Those changes were not in GitHub. They were integrated before new changes. The ZIP's cache closure and readiness behavior still required correction; its claim that the remaining failure was unresolved was not treated as a diagnosis.

## Findings and candidate changes

| ID | Finding / cross-system connection | Candidate action | Evidence / status |
|---|---|---|---|
| F01 | GitHub referenced an undefined `silence_policy` in session setup. | Incorporated the ZIP's service-owned policy. | Original static undefined-name failure; candidate clean. |
| F02 | Five custom processors forwarded frames without the parent lifecycle call. `StartFrame` could pass while queued data stalled. | Added `await super().process_frame(...)` to all five. | Real processor/worker queue tests; lifecycle mutation detected. |
| F03 | Cached TTS did not follow context creation/closure ownership. | Create context before audio; let the pinned TTS turn lifecycle close it after remaining frames. Use native standalone speech requests. | Cache-context regression and mutation; full media test. |
| F04 | A candidate iteration inserted cached text twice into history. | Removed the extra insertion; rely on Pipecat's sequencer fallback. | Exact-once opening and reply assertions. This was caught during development, not misrepresented as an original defect. |
| F05 | The assistant aggregator was before the guard and TTS. | Move it after output so history receives TTS-side text rather than unguarded model output. | Full synthetic turn and history checks. |
| F06 | A failed startup flush was logged but the opening could still be queued. | Wait for the real pipeline-start event; fail closed on timeout, early disconnect, or failed runner. | Startup timeout / early-runner / cancellation tests. |
| F07 | A started pipeline could remain silent without a near-term outbound-audio gate. | Add first-outbound-audio deadline and serializer counters. | Synthetic no-audio failure ends and records. Counters explicitly do not prove carrier playback. |
| F08 | Short synthetic audio output remained in the buffered audio path. | Enable Pipecat's supported 200 ms pre-stop silence insertion. | Before: 3,032 PCMU bytes for two 250 ms tones. After: 6,064 bytes including padding; minimum 4,000-byte assertion passes. Not a bit-perfect carrier-audio test. |
| F09 | Stale partial model output could survive interruption. | Reset ResponseGate state on interruption/cancel/end. | Stale-text regression. |
| F10 | A triggering transcript could reach inference before the frontier model update. | Send settings update before forwarding the transcript. | Source ordering corrected; real provider/model-switch timing remains a live test. |
| F11 | Synthesis completion was treated as playback completion. | Track upstream transport speaking events; calculate fallback duration using the actual cache rate. | Processor and synthetic runtime tests. Carrier mark/clear acknowledgments remain deferred. |
| F12 | Media disconnect was treated as if the billable call necessarily ended. | Request canonical hangup on media-only disconnect; preserve explicit unresolved-hangup status. | Full synthetic session and failure-path tests. |
| F13 | Task startup/failure and cleanup could escape the final-record boundary. | Own created tasks, bound cleanup waiting, record pending cleanup, and preserve cancellation. Bound canonical hangup HTTP timeouts. | Startup, no-audio, runner-error, cancellation, and cancellation-resistant helper tests. |
| F14 | Pre-build handler failure could silently close the socket without a call record/hangup attempt. | Add sanitized emergency record and canonical hangup attempt for authenticated registered sessions. | Endpoint failure test. |
| F15 | Media URLs/attempt IDs were correlation tokens, not authenticated upgrades. | Verify Telnyx Ed25519 signature over timestamp and exact configured WSS URL, before accept/claim. | Valid, stale, tampered, missing-signature and endpoint tests; signature mutation detected. No unsigned bypass. |
| F16 | Media attach did not reject terminal/stopped attempts. | Reject stopped, terminal and unsupported states while preserving the unbound-ID check. | Attach-state regressions; existing fixtures corrected to real `initiated` state. |
| F17 | Multiple different jobs could claim carrier submission concurrently. | Atomic default-one concurrent-call gate, counting `submit_unknown` too. | Eight simultaneous claims: one succeeds; seven remain prepared. Mutation detected. |
| F18 | Quota inputs allowed nonfinite values and duplicate keys; actual overage could fail settlement. | Validate finite nonnegative inputs/unique keys. Record real overage, then reject later reservations. | NaN/infinity/duplicate/overage regressions. This is not provider-invoice reconciliation. |
| F19 | Duplicate webhook delivery could leave a previously stored event unprocessed. | Re-run idempotent event processing on duplicate delivery. | Endpoint retry test. Crash-without-redelivery worker remains backlog. |
| F20 | Reconciliation could consider quarantined events. | Restrict evidence to accepted processing outcomes. | Source correction and canonical reconciliation regressions. |
| F21 | Preflight-selected fallback models were not reused consistently at call build. | Fingerprint nonsecret configuration and load fresh matching model-selection receipts; paginate Gemini and filter generation-capable models. | Pagination, repeated-token, fallback, stale/future and config-drift tests. |
| F22 | Voice/model/rate values could differ between prewarm, readiness and call build. | Centralize TTS configuration and honor the environment voice default. | Configuration tests; voice-specific live speech still required. |
| F23 | TTS budget accounting missed fixed-phrase cache misses and counted cached/planned text incorrectly. | Reserve provider characters at actual noncached `run_tts`; block synthesis before crossing the character budget. | Zero-budget test proves no provider call; cache hit excluded in full-session test. |
| F24 | First use could attempt an NLTK download. | Bundle the verified English `punkt_tab` data and check it in readiness; offline verifier fixes search path before tests mutate env. | Full suite: zero external connection attempts. Upstream attribution retained. |
| F25 | The WebSocket proxy workaround relaxed more responses than necessary. | Make it explicitly opt-in and restrict it to the observed 101/Upgrade:websocket/Connection:close shape. | Shape tests. TLS and Sec-WebSocket-Accept validation remain with the library. |
| F26 | Raw error detail/diagnostic locals could expose data. | Store exception types, recursively redact persisted text, disable Loguru diagnostic locals, bound inbound WebSocket size/queue. | Redaction/structural checks. Not a complete PII or secret audit. |
| F27 | Job duration and payload shapes were weakly bounded. | Validate duration, E.164 destination and bounded job text/collections; strict internal JSON parsing. | Invalid-duration and existing request tests. |
| F28 | Callers could explicitly ask to stop without a terminal rule. | Add conservative explicit opt-out/end-call rules; avoid forwarding a terminating transcript to inference. | Positive and negated-hangup tests. Durable future-call suppression remains backlog. |
| F29 | The handoff phrase promised an immediate transfer that does not exist. | Use a truthful end-call message and retain `loop_in_requested` for the operator. | Updated prompt/cache/prewarm/readiness. Actual bridge/notification not implemented. |
| F30 | Dialers checked liveness instead of full readiness and imported server code from a fixed old checkout. | Require local `/readyz`; import relative to the candidate checkout; honor the shared database environment path. | Both dialers refuse a mocked not-ready response without carrier calls; source paths reviewed. |
| F31 | Legacy deployment moves virtualenvs and does not roll back code atomically. | Supply a separate prepare-only workflow with a new immutable-path venv and a review-only systemd override. | Rendering and read-only tool tests; no systemd deployment performed. Legacy deploy remains discouraged, not silently declared fixed. |

## Verified results

- Original application baseline: **129/129 passed**, despite missed runtime defects.
- Candidate application suite: **173/173 passed**, including **44 added tests**, with zero failures, errors or skips. **Five additional independent-process repetitions also passed 173/173**, each with zero external connection attempts. Final receipts govern the exact revision and status.
- Unchanged reference suite: **57 tests**; reference manifest covers **28 files**.
- New mutation targets: lifecycle, premature cache close, signature bypass, concurrent-call bypass. All four were caught by tests.
- Existing reference mutations: signature verification, single-use submission claim, terminal-state regression. All three were caught.
- Exact locked dependency installation succeeded; `pip check`, Python syntax, undefined-name checks and `git diff --check` passed.
- Application tests permit Python loopback sockets only and strip provider credential environment variables. The full passing run used seven local test-server connections and attempted no external socket connection. This is an in-process test guard, not an OS sandbox for arbitrary malicious native/subprocess code.
- The complete synthetic media case observed 3,200 inbound PCMU bytes, 6,064 outbound bytes including padding, eight outgoing media frames, one synthetic LLM request and zero pending owned cleanup tasks. It verified non-silent output and exact-once assistant history.

## Important boundaries

1. **No claim of live STT/LLM/TTS success.** Those services are replaced in the offline integration test. Actual credentials, quota entitlements, latency, pronunciation, model behavior and the Muse proxy still need verification.
2. **Serialized audio is not heard audio.** Carrier playback acknowledgments and a human listener are required. Padding volume alone does not establish intelligibility or exact waveform preservation.
3. **No hard all-provider dollar guarantee.** Current costs remain partial estimates. Gemini token/reasoning/cache usage, Groq per-request minimum billing, prewarm usage and the Telnyx final bill must be reconciled. Do not label the existing estimated dollar thresholds as guaranteed invoice caps.
4. **Single-call scope is per shared SQLite database.** Independent deployments/databases would not share that limit. A genuinely unknown historical call intentionally blocks new submission until verified reconciliation.
5. **Cancellation-resistant tasks are reported, not magically killed.** Process supervision/isolation is the final enforcement boundary.
6. **Consent is not operator approval.** A per-call approval token, an allowlisted number and an AI disclosure do not establish the recipient's legally required consent. Use only an explicitly consenting test recipient pending jurisdiction-specific review.
7. **No automatic redial, bridge, payment, or commitment was added.** Opt-out persists as a call outcome, not yet as a global do-not-call registry.
8. **No schema migration was required.** Keep the real database and signed-event history; do not create a fresh database merely to bypass pending-call or quota gates.

## Next action

Follow `MUSE_TEST_HANDOFF.md`, not the historical runbooks. Review `PRIORITIZED_BACKLOG.md` before expanding beyond a single consenting test call. The next iteration should be driven by the safe readiness report, exact candidate revision, and one attempt's timestamped stage evidence—not another speculative audio/configuration rewrite.
