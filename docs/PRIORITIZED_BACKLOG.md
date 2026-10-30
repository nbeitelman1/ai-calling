# Prioritized plan and acceptance checklist

**Policy:** Finish P0 before broad calling. Change one subsystem per subsequent iteration. Preserve the exact commit, configuration fingerprint, attempt ID, failed assertion and source receipt.

## Completed in this candidate

- [x] Reconcile the GitHub checkout with all eleven embedded ZIP source files.
- [x] Install and inspect the exact hash-locked runtime.
- [x] Reproduce the original 129-test passing baseline and its coverage gaps.
- [x] Repair processor lifecycle, cache context/turn handling and exact-once history.
- [x] Add startup/first-audio deadlines, owned-task cleanup and emergency failure records.
- [x] Separate media disconnect from carrier call termination.
- [x] Add signed media-upgrade verification and reject terminal/stopped attach.
- [x] Add default-one atomic call-submission concurrency, including unknown calls.
- [x] Tighten job validation, strict JSON, quota validation and settlement.
- [x] Reuse verified dynamic-model selections; paginate/filter Gemini model catalog.
- [x] Unify TTS cache/prewarm/readiness configuration and bundle offline tokenizer data.
- [x] Put TTS character reservation at actual noncached synthesis.
- [x] Restrict the proxy compatibility workaround to explicit opt-in and its known response shape.
- [x] Add opt-out handling, truthful non-transfer language and dialer readiness checks.
- [x] Add read-only feedback collection and a prepare-only venv/systemd handoff.
- [x] Run negative controls and mutation tests; preserve evidence for merge review.

## P0 — required before the next controlled Muse call

| Task | Owner / workstream | Acceptance evidence | Status |
|---|---|---|---|
| Review and import candidate in an isolated branch. | Nathan/Muse + Commander | Correct base commit; clean diff; no production branch overwritten; reference files unchanged. | Ready for operator |
| Prepare the new venv at its permanent path. | Deployment | Hash-locked install, `pip check`, 173 application tests pass on Muse; do not relocate the venv. | Tool supplied; not executed on Muse |
| Preserve existing state and rollback assets. | Deployment | Consistent SQLite backup; previous revision, venv path, unit/drop-ins and private config preserved; rollback procedure reviewed. | Operator |
| Reconcile every historical active/unknown attempt. | Carrier/control owner | Read-only report shows why any claim blocks; carrier/signed evidence resolves it. No guessed state changes, redial or database reset. | Operator / live evidence |
| Match the exact public WSS URL and preserve signature headers. | Network/security | A real Telnyx upgrade validates; changing host/path/query or signature fails. Do not disable auth to make a proxy pass. | Live gate |
| Refresh provider selection and prewarm the changed phrase set. | Provider/config | Fresh fingerprinted receipt; selected models offered; voice/rate/cache match; `/readyz` returns 200. | Live provider checks needed |
| Verify recipient consent and test purpose. | Nathan / compliance | Explicitly consenting owned/test recipient; approved destination and time; recording/transcript rules reviewed. | Before any dial |
| Run exactly one short test, with independent stop access. | Nathan/Muse | Opening first; intelligible two-way speech; one response per turn; no tail clipping; no extra call. | Live gate |
| Confirm actual termination. | Carrier/control owner | Signed `call.hangup` and carrier reconciliation; a hangup-command HTTP 200 alone does not pass. | Live gate |
| Return the safe diagnostic bundle. | Nathan/Muse | Exact revision, package versions, stage timeline, counters, outcome, pending tasks and safe state report. | Procedure supplied |

## P0 — before production/business use

| Task | Why it matters | Acceptance test / implementation direction |
|---|---|---|
| Recipient-consent and suppression policy | Operator approval is not callee consent; one-call opt-out is not future-call suppression. | Durable consent-purpose-destination evidence, opt-out/DNC gate before submission, revocation tests, jurisdiction review. |
| Provider-backed usage and final-bill accounting | Current dollars are partial estimates; zero/missing metrics must not look free. | Capture typed LLM/STT/TTS usage deltas, explicit unknowns, Groq per-segment 10-second billed minimum, prewarm ledger, carrier final cost; reconcile approved test invoices. |
| Provider limit enforcement | Reserving fifty Gemini requests does not itself enforce all retries or actual model requests. | Pre-request counters, bounded retry budgets, token/output caps, 429/5xx policy, transactional reservations and measured settlement. |
| Durable call termination/recovery | A process crash or lost webhook can leave a live/unknown call. | Persist teardown intent; restart reconciliation worker; bounded idempotent hangup replay; never infer failure from timeout; no auto-redial. |
| Safe code/config/venv rollback | Existing `deploy_linux.sh` moves venvs and lacks full code rollback. | Immutable code releases and venv paths; atomic reviewed service switch; restore unit before restarting; persistent shared DB retained; prove rollback on a test host. |
| Provider credential/proxy conformance | REST preflight does not prove streaming WebSockets work. | Independently test provider REST and WebSocket upgrade under the actual Muse credential mechanism; prove TLS and response-auth validation stay enabled. |

## P1 — next engineering wave

| Task | Connection to current work | Acceptance criteria |
|---|---|---|
| Carrier mark/clear-aware playout tracker | Replaces conservative duration waits and separates sent, queued, played and cleared audio. | A clear-acknowledged mark is never counted as heard; hangup waits for the relevant utterance or an explicit bounded timeout; test races. |
| Exact audio preservation / acoustic tests | Byte-volume and non-silence checks do not prove end phonemes or intelligibility. | Known speech fixture, golden waveform/tail-marker comparison, 8kHz↔16kHz assertions, chunk/rate matrix, real handset listening test. |
| Realistic STT/VAD corpus | Synthetic transcription bypasses recognition quality. | Consented noise/accent/short-utterance fixtures, false-activation and missed-speech rates, end-of-turn latency distribution, confidence-free silence behavior. |
| Explicit opening gate | Queuing the opening first does not prove no racing input can trigger early model work. | Callee speaks during startup and disclosure; no model speech precedes disclosure; no sensitive commitments; documented interruption policy. |
| Periodic provider-receipt refresh | Matching receipts intentionally expire after an hour. | Controlled refresh before expiry; atomic publication; mismatched/failed refresh keeps readiness false; running-call behavior specified. |
| Webhook event worker | Idempotent processing on redelivery does not cover every crash-without-redelivery case. | Stored, unprocessed event survives restart and is retried exactly once logically; journal/backlog metrics; 5xx on storage failure rather than misleading client rejection. |
| Media job reservation recovery | Claimed-but-unsettled reservations survive crashes intentionally. | Reconcile with authoritative call/provider state; no silent release of uncertain spend; tests across day/month boundaries. |
| Prompt and action boundary tests | Text heuristics are not a reliable policy engine. | Adversarial transcript suite for secrets, payments, legal consent, indirect prompt injection, split numeric strings and ambiguous intent. No tool/action authorization from model prose. |
| Durable escalation action | The system currently records a request and ends; no real bridge or notification. | Choose callback/notification or approved bridge; acknowledge success only after external evidence; new explicit approval for any second leg. |
| PII / secret / retention audit | Recursive regex redaction is only a backstop. | Typed log allowlist, no token/credential values, encrypted/access-controlled transcript storage, retention jobs safe under invalid configuration, consent-aware audio handling. |
| Observability and SLOs | Current counters locate failures but are not service-quality guarantees. | Correlated attempt/stream/context IDs; p50/p95 startup/STT/model/TTS/turn latency; first-byte vs heard-audio distinction; alert on pending cleanup/unknown calls. |
| Service lifecycle integration | Local helper cancellation tests are not a systemd SIGTERM test. | Shutdown during handshake, synthesis, speaking and hangup; final record and bounded exit; no ongoing provider work; restart recovery. |
| Restrict diagnostic exposure | Existing public health/readiness detail may disclose configuration problems. | Minimal public status; authenticated detailed diagnostics; reverse-proxy route allowlist for `/internal/*`; rate limits and body/frame limits. |
| Dependency security review | Hash locks ensure reproducibility, not absence of CVEs. | SBOM and current advisory scan, reviewed dependency updates, lock renewal in separate branch, same offline/live gates after upgrade. |

## P2 — maintainability and broader resilience

- [ ] Split the large live pipeline into testable session orchestration, provider construction, frame processors, serialization and policy modules without changing behavior.
- [ ] Add CI for Python 3.12 hash-locked tests, static checks, mutation smoke tests, immutable reference integrity and artifact manifests.
- [ ] Add an explicit environment/config schema with units, ranges, defaults, sensitivity and versioning; forbid silently divergent duplicate constants.
- [ ] Make both historical dialers share one canonical audited command path, exact approved destination, evidence capture and readiness gate.
- [ ] Add long-session, repeated-call and resource-leak soak tests; measure peak memory/CPU under realistic VAD and provider traffic.
- [ ] Decide distributed state/concurrency architecture before adding a second independent worker/database.
- [ ] Measure whether a streaming-STT option improves actual end-to-end latency enough to justify provider/operational complexity; do not change provider speculatively.
- [ ] Evaluate sentence-level guarded streaming versus whole-response buffering only after policy tests prevent split-fragment leakage.
- [ ] Replace the temporary proxy-header workaround with a correct reverse/forward proxy configuration where possible.

## Regression gates for every iteration

1. Full application tests and unchanged reference suite pass.
2. Targeted test reproduces the failure before the patch and passes afterward, or clearly label the change as unverified hardening.
3. Relevant broken-control mutant is detected.
4. No production credentials, provider calls or dialing occur in offline tests.
5. Source/config versions and artifact hashes are recorded.
6. Muse executes only the smallest consented live test needed, with a fresh approval and no automatic retry after an unknown outcome.
7. Carrier and provider evidence determines success—not the UI, process uptime, a WebSocket connection or a generated-audio counter alone.
