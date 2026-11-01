# WP-02 — closing controller: verified intermediate checkpoint

**Status: IN PROGRESS. Pure controller implemented and verified; live-pipeline integration is NOT implemented. The double-goodbye remains reproducible in the current runtime.**

## Scope and controlling revision

- Work order: `docs/AGILE_IMPROVEMENT_PLAN.md`, WP-2, October 31, 2026.
- Branch: `agile/wp-02-closing-state`.
- Parent: reviewed candidate `ffaf4ccf4450359d3a87728906521316f8e562d4`.
- Exact checkpoint HEAD is in the delivered `CHECKPOINT.json`; the bundle restores that commit.
- This branch is not stacked on WP-1 and does not contain its production turn-stack changes. Only reusable test infrastructure and synthetic WAV fixtures were copied from WP-1 `f65fa8e0bf2d7fcff3a38437006e4d86d2ebbd38`, with the adaptations described below.
- Installed reference environment: Python 3.12.3 and Pipecat 1.8.0, unchanged locked requirements.
- No live calls, provider API calls, deployment, live-database changes, push, or merge.

## 1. Observed baseline, not an assumed root cause

The reviewed branch reproduced **250/250 passing application tests**, with zero failures, errors, skips or external connection attempts. However:

1. `TurnRouter` was defined but never instantiated or called by `server/live_pipeline.py`. Its existing slot-completion decision could not end the running media session.
2. `TranscriptTap` emitted EndFrames on post-terminal speech and opt-out, `ResponseGate` could emit an EndFrame on its character cap, and `_finish_and_hangup` emitted another. The terminal/frontier/duration/cost/silence watchers also select shutdown actions.
3. Native `TelnyxFrameSerializer` defaults to `auto_hang_up=True`, so EndFrame or CancelFrame can initiate a carrier hangup independently of the media handler's follow-up hangup path.
4. The first client-seam reproduction's multi-part synthetic goodbye split into an unexpected third STT segment. That attempt is retained as **invalid fixture evidence**, not a passing baseline. A single-word, slowed synthetic goodbye produced a clean two-turn reproduction.

### Real-service double-goodbye reproduction

`tools/closing_baseline_bench.py` invokes the actual media handler, transport, serializer, Silero VAD, real batch Groq service, real Google service, real ResponseGate and actual ElevenLabs subclass. Only provider clients, the call-state adapter and media peer are doubled.

Observed clean run (`wp02-double-goodbye-baseline-v2.json`):

| Item | Observation |
|---|---|
| Caller turns | `Goodbye.` then `Goodbye.` |
| Scripted Google replies | `Goodbye.` then `Goodbye.` |
| LLM client requests | 2 |
| TTS client requests | 2, both `Goodbye.` |
| Natural end before forced test cleanup | False |
| Terminal reason before cleanup | None |
| Unexpected STT calls / fixture errors | 0 / 0 |
| Cleanup pending tasks | 0 |
| Blocked network attempts | 0 |

This is a scripted reproduction of the reported double-goodbye pattern, **not a verbatim replay or independent verification of the October 30 recording**. STT and LLM answers are deliberately scripted, so this does not measure live recognition or model judgment. The subsequent forced peer disconnect is cleanup, not proof that caller goodbye caused a natural hangup.

The diagnostic command returns success when it reproduces the defect; its receipt explicitly says `wp02_pipeline_acceptance_passed: false`. Do not mistake this for a passing new-runtime acceptance test.

## 2. What is implemented

### Pure state model

Added `server/closing_state.py` and integrated it with `TurnRouter` in `server/call_controller.py`:

| State/event | Controller behavior |
|---|---|
| OPEN + recognized whole-utterance goodbye | Enter CLOSING with `caller_goodbye` |
| OPEN + existing playbook slots complete | Enter CLOSING with `objective_complete` |
| CLOSING + duplicate close trigger | Do not claim another line |
| CLOSING + `bye`, `thanks`, other acknowledgements | Suppress; no progress mutation or new turn |
| First ordinary CLOSING + substantive interjection | Reopen once; invalidate old playback/hangup token |
| Second CLOSING + interjection | Suppress; second closing is final |
| CLOSING + matching claimed-line start then stop | Permit one hangup-dispatch claim |
| Text generated or line merely claimed | Do not permit hangup |
| Stale generation or clear/interruption | Do not count as completed playback |
| Cache/playback failure reported by future runtime owner | Explicit failure escape, never relabeled successful playback |

Each closing generation has a token. The runtime must attach the correct token to the actual closing-audio lifecycle; merely receiving any later BotStoppedSpeakingFrame is insufficient.

The controller distinguishes effect claims from completed effects. `ENDED` means termination dispatch has been claimed locally, not that Telnyx acknowledged or billing ended. Records expose that scope explicitly.

### Safety interpretation

Reopening is allowed only for ordinary goodbye/objective completion. Existing hand-back/refusal, opt-out, hard duration/cost limits, faults and repetition guard remain final. This preserves the work order's instruction not to weaken safety/caps or `loop_in_requested` semantics. A mandatory condition upgrades an ordinary pending close without claiming a duplicate line. The first mandatory reason wins if mandatory conditions race.

The exact hand-back string remains `Nathan will need to take this over. I'll end the call now.` Neither live honesty constants nor the AI identity response were changed.

A review caught and fixed a real state-level edge case: a hard-cap reason initially failed to supersede a second, already non-reopenable ordinary closing. The new regression failed for the intended reason before the correction and now passes.

### Intent and objective limits

Goodbye matching is conservative and whole-utterance-based. `Thanks` alone does not end an OPEN conversation; quoted/negated goodbye and `Goodbye, but what time...` do not close it. A substantive `No` can reopen an ordinary closing. This is a rule-based starter vocabulary, not a trained intent classifier.

Objective completion continues to use the existing tracker's keyword-filled slots. That heuristic is **not proof of semantic task success**. The pipeline must not be advertised as having accurate objective-completion detection until that contract is separately reviewed and covered.

## 3. Verification: exact counts and scope

| Check | Result |
|---|---:|
| Reviewed-base application battery | 250/250 |
| New state/router tests | 28/28 |
| New native 1.8 API tests | 7/7 |
| Full WP-2 intermediate application battery | **285/285**, 0 failures/errors/skips |
| Existing reference battery | **57/57** |
| Existing reference mutations | **3/3 caught** |
| Reference integrity manifest | **28 files verified** |
| New controller negative controls | **6/6 caught at intended assertions**, 0 harness errors |
| Bounded state-event combinations | **4,096** sequences of four events, checked within one state test |
| Dependency consistency | `pip check` passed |
| Full-suite socket guard | 7 loopback connections, 0 external attempts |

The 285 count is this independent branch's 250 baseline + 35 new tests. It is not the WP-1 281 count plus these tests, because this branch does not include WP-1's production changes/test suite.

Controller negative controls deliberately removed: one-line claiming, one-reopen limit, playback-before-hangup requirement, generation matching, acknowledgement suppression, and clear-versus-stop discrimination. All six failed the intended tests in disposable copies; the working controller bytes remained unchanged.

Pure negative-control workers ran in parallel: 1.285 s wall time, 3.814 worker-seconds, 2.968x overlap. Full application execution took about 80.06 s including discovery. Neither number is conversational latency.

A separate 5,000-iteration synthetic goodbye-route sample measured p50 2.073 microseconds and nearest-rank p95 2.174 microseconds (maximum 65.619 microseconds), excluding router construction. This bounds only that local rule-path sample, not all playbooks, all hardware, or worst-case latency.

## 4. Native compatibility findings

Installed-source hashes/signatures are recorded in `wp02-inventory.json`.

- `LLMUserAggregatorParams.user_idle_timeout` exists and defaults to 0.
- `on_user_turn_idle` exists and the user aggregator forwards the event without passing through the underlying controller argument.
- Actual native idle-controller tests verify firing after bot stop, cancellation when the bot resumes, and suppression while the user turn is active.
- **Migration hazard:** in this pinned native controller, UserStartedSpeakingFrame followed by UserStoppedSpeakingFrame alone does not rearm the idle timer. An empty transcription/cough path must be covered before replacing the current application silence policy. The test records the installed behavior; it does not endorse indefinite silence.
- `TelnyxFrameSerializer.InputParams(auto_hang_up=False)` exists. Tests prove that true mode claims one native hangup for duplicate End/Cancel frames and false mode claims none. These are native serializer contracts with the hangup method intercepted, not REST/carrier evidence.
- Telnyx marks can also return after `clear`. Any future mark-based drain check must distinguish played versus cleared audio and match the correct closing generation.

## 5. What remains before WP-2 acceptance

**The live media pipeline is byte-identical to the reviewed base at this checkpoint. The following are not implemented:**

1. Instantiate TurnRouter in the media pipeline and make one component own user tracking; do not call `observe_user` twice through both BrainLadder and TurnRouter.
2. Intercept caller goodbye before it can trigger LLM inference. Add a downstream context gate as necessary so queued contexts cannot leak into inference while CLOSING. Suppress stale in-flight LLM output from the previous generation.
3. Route hand-back, repetition, duration, cost, opt-out and silence completion into the same closing owner while preserving outcomes. Special hard-stop cases may skip normal audio only with an explicit failure/safety reason.
4. Claim exactly one cached line; add the generic closing phrase to the prewarm/required-cache contract. Do not silently fall back to live synthesis. Missing cache must be an explicit recorded failure or pre-call readiness failure, not alleged success. No real provider prewarming was run in this checkpoint.
5. Couple the generation token to actual closing playback. Resolve the known legacy output-clock mismatch without silently merging WP-1. A narrow independently reviewed output-pacing change may be needed; WP-1's aggregator/interruption configuration is not imported here.
6. Give the handler sole termination ownership and explicitly disable serializer auto-hangup for that path. Keep one canonical adapter command ID, acknowledgment semantics, error cleanup and duplicate-trigger tests. An EndFrame cannot be queued before the one-time reopening decision because it is a terminal pipeline action.
7. Prevent acknowledgements from clearing the cached closing audio. Preserve incoming audio to batch STT and account for delayed final transcripts when deciding a real mid-goodbye interjection. Do not equate a generic bot-stop or returned clear-mark with a completed goodbye.
8. Implement one idle-reprompt owner: 1–2 fixed reengagement prompts and then CLOSING. Cover empty-transcription rearming with the pinned native API rather than enabling competing timers. Hard duration caps remain independent of the LLM.
9. Execute client-seam acceptance for objective completion, double goodbye, one real reopening, final second closing, hand-back, missing cache, queued/in-flight LLM races, duplicate termination, silence and hard cap. Assert exact record reasons and cleanup.
10. Repeat the full battery, report and scan; publish only the package branch after secure authenticated access is available. Do not start WP-3 or claim merge-ready WP-2 before that gate.

## 6. Reproduction commands

Use the unchanged locked Python 3.12 environment. No command below dials or connects to provider APIs.

```bash
python tools/offline_verify.py --pattern test_closing_state.py --output logs/offline-qa/recheck-state.json
python tools/offline_verify.py --pattern test_closing_native_contracts.py --output logs/offline-qa/recheck-native.json
python tools/offline_verify.py --output logs/offline-qa/recheck-full.json
python tools/verify_closing_mutations.py --output logs/offline-qa/recheck-mutations
python tools/closing_inventory.py --output logs/offline-qa/recheck-inventory.json
python tools/closing_baseline_bench.py --output logs/offline-qa/recheck-baseline-defect.json
python test-pack-v1/Muse_Telnyx_Integration_Test_Pack/run_tests.py --output logs/offline-qa/recheck-reference.json
python test-pack-v1/Muse_Telnyx_Integration_Test_Pack/run_mutations.py --output logs/offline-qa/recheck-reference-mutations.json
python tools/verify_reference_manifest.py test-pack-v1/Muse_Telnyx_Integration_Test_Pack
```

The baseline diagnostic is deliberately tied to the old runtime defect; it should cease confirming that defect after successful runtime integration. Keep its old receipt rather than weakening it to make a new run appear green.

## 7. Provenance, costs and limitations

Test infrastructure copied from WP-1: `tools/client_seam_bench.py`, `tools/generate_turn_fixtures.py`, and synthetic WAV fixtures. The WP-2 generator adds goodbye, closing-line and real-question fixtures; the client-seam TTS fixture mapping adds their corresponding text. Other imported scenario functions still belong to WP-1 experiments and are not claimed as WP-2 acceptance. No production turn policy was copied or merged.

Fixtures are local FFmpeg/libflite speech, not personal recordings. The initial multi-part goodbye segmentation failure and the corrected single-word fixture are both documented. Google responses and recognition results are scripted. No STT accuracy, voice quality, live reasoning, handset playback, or cost-saving claim follows.

Provider spend: zero calls made; no live cost delta measured. Publication is outstanding because no secure GitHub write session was established in this continuation. No old pasted token was used.

Primary-source comparison (read October 31, 2026; installed 1.8.0 source controls compatibility):

- https://docs.pipecat.ai/pipecat/fundamentals/detecting-user-idle
- https://docs.pipecat.ai/api-reference/server/frames/control-frames
- https://docs.pipecat.ai/api-reference/server/services/serializers/telnyx
- https://developers.telnyx.com/docs/voice/programmable-voice/media-streaming
- https://developers.telnyx.com/api-reference/call-commands/hangup-call

**Next: integrate and verify the runtime closing owner. This checkpoint establishes the controller and the failing real-service baseline; it does not claim the phone behavior is fixed.**
