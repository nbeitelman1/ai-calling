# WP-01 — explicit turn stack and client-seam verification

**Status: IMPLEMENTED AND OFFLINE-VERIFIED; pending operator review and branch publication. Not deployed, merged, or live-call verified.**

## 1. Scope, branch, and decision

- Branch: `agile/wp-01-turn-stack`.
- Reviewed base: `ffaf4ccf4450359d3a87728906521316f8e562d4` on `review/ai-calling-candidate`.
- Initial inventory checkpoint: `d9fe3dc13c847beccff361502809f074bf96ae54`.
- The exact delivered HEAD is recorded in the checkpoint's `CHECKPOINT.json` and `HANDOFF.md`; use `git rev-parse HEAD` after restoring its Git bundle.
- Controlling work order: `docs/AGILE_IMPROVEMENT_PLAN.md`, emailed October 31, 2026. Repository plan SHA-256: `aedce725395490346720ebc57d4d63ab62bc894c218fa30c7226c9008eb71a80`. Attachment SHA-256: `dc49e2e0b9953c82cc63cdead0b1cb0b6248b73c22b8573f67cb029ba9fab14d`; content matches after newline normalization.
- Sandbox installation: Python 3.12.3, Pipecat 1.8.0, google-genai 2.34.0, groq 1.7.0, onnxruntime 1.24.4. The existing hash-locked dependency set is unchanged; `pip check` passed. This is not inspection of the running Muse environment.

**Decision:** retain `legacy` as the default until review. The `explicit` candidate is available as an opt-in. Keep semantic-completeness filtering off by default, even in explicit mode. It is a scoped experiment, not a demonstrated latency or recognition improvement.

No live calls, provider API calls, deployment, systemd changes, live-database changes, push, or merge were performed in this package. A secure authenticated publishing session is still needed. No previously posted token was reused.

## 2. What changed

### Production mechanics

1. Added `server/turn_policy.py`: version-scoped configuration for explicit native aggregator VAD, Smart Turn v3, opening mute, MinWords interruption filtering, and an optional appointment-only completeness gate. Effective configuration is included in the session receipt.
2. Added `server/turn_start.py`: a small subclass of native `VADUserTurnStartStrategy`. It starts turns from VAD while the bot is idle, but defers to native MinWords during bot speech. MinWords-only wiring introduced an avoidable STT fallback wait; the conditional VAD path avoids that measured regression.
3. Updated `server/live_pipeline.py`: explicit mode moves VAD into the aggregator and removes the standalone VAD processor. Legacy keeps its standalone detector. The activity/debug tap observes upstream VAD in explicit mode and downstream VAD in legacy mode, exactly once.
4. Protected the opening with native `MuteUntilFirstBotCompleteUserMuteStrategy`, including the pre-audio period. Native contract tests cover release after the first stop and a pre-audio error.
5. Set explicit-mode transport output to 8 kHz PCM before packetization. TTS and input/VAD remain 16 kHz. This prevents the serializer's streaming downsampler from suppressing writes and thereby skipping transport pacing ticks in the measured PCMU path.
6. Explicitly retained empty-transcript suppression in real Groq STT. In the normal explicit stack, the existing `SilencePolicy` remains the sole conversational idle-reprompt owner; empty recognition does not fabricate an LLM turn. The exact newer `empty_user_turn` API is unavailable in 1.8.0 and was not invented or vendored.

The controller, persona/memory modules, literal honesty lines, provider fallback implementation, requirements, and live data stores were not rewritten. Five existing custom frame processors continue to await their parent lifecycle method. No second active VAD detector or regular idle prompter was added.

### Verification mechanics

- `tools/client_seam_bench.py` exercises the real media handler, FastAPI WebSocket transport, Telnyx serializer, Silero VAD, Smart Turn, real Groq STT, real Google LLM/failover wrapper, and actual ElevenLabs TTS subclass.
- Doubles exist at provider client boundaries: `google.genai.Client`, the OpenAI-compatible client used by Groq's Whisper service, and ElevenLabs' WebSocket connection. The call-state adapter and media peer are synthetic; temporary stores are discarded.
- New tests: 10 policy tests and 10 real-service client-seam scenarios, alongside the 11 native contracts from the earlier checkpoint.
- `tools/benchmark_turn_stack.py` runs repeated sequential/interleaved variants and retains every timing sample plus source hashes.
- `tools/verify_turn_mutations.py` is a portable negative-control runner. It changes disposable copies only, runs audio scenarios sequentially, and requires the intended assertion to fail with no harness error or external connection attempt.
- `tools/offline_verify.py` now accepts `--test` for focused tests while retaining its network guard.
- Eleven checked-in WAV fixtures are generated locally with FFmpeg/libflite. Tests do not require FFmpeg unless regenerating those files.

## 3. Evidence and exact counts

Current receipts are in `docs/evidence/wp01-final/`. Raw local logs and historical attempts are retained in the delivered checkpoint under `logs/offline-qa/`.

| Check | Observed result |
|---|---:|
| Application battery | **281/281 passed**, 0 failures, 0 errors, 0 skips |
| Independent reference battery | **57/57 passed** |
| Existing reference negative controls | **3/3 caught** |
| Reference byte-integrity manifest | **28/28 files verified** |
| New WP-1 negative controls | **6/6 caught at intended assertions**, 0 harness errors |
| Fresh repeated benchmark runs | **21/21 completed** |
| Python source syntax checks | **74 files parsed** |
| Synthetic speech manifest | **11/11 WAV hashes verified** |
| Benchmark source hashes | Match the verified working source |
| Dependency consistency | `pip check` passed |
| Application socket guard | 7 loopback connections; **0 external connection attempts** |

The full application run took about 165.3 seconds inside the runner. This is test-suite wall time, not conversational latency. The portable mutation wave took about 62.1 seconds and was deliberately sequential. The earlier parallel regression workers were deterministic subprocess checks, not autonomous LLM agents.

### What would catch the old failures

All six mutations were rerun using the portable, checked-in runner; the working source hashes remained unchanged.

| Deliberate break | Test / observed failure |
|---|---|
| Lower interruption threshold to one word | `test_short_backchannel_does_not_cancel_tts_or_run_llm_again`: unexpected wire `clear` during reply |
| Remove 8 kHz transport pacing correction | `test_output_clock_covers_queued_phone_audio_duration`: bot-speaking state ends much earlier than queued audio duration |
| Remove opening mute | `test_disclosure_survives_scripted_overtalk`: unexpected `clear` during opening |
| Remove idle VAD start | `test_normal_turn_uses_real_stt_llm_and_tts_clients`: fixed-delay synthetic response exceeds the 0.8-second regression ceiling |
| Bypass completeness strategy | `test_appointment_completeness_trial_suppresses_premature_tuesday`: speaks Tuesday and then Thursday |
| Observe wrong VAD direction | `test_native_aggregator_vad_reaches_real_batch_stt_upstream`: activity callbacks are 0/0 instead of 1/1 |

Additional passing scenarios cover late opening overtalk, a genuine two-word interjection, empty recognition during a reply, and idle empty recognition. The idle-empty case produces one `Are you still there?` through the existing policy, with zero LLM requests. The normal-turn STT capture contains 29,312 samples at 16 kHz; its captured/input PCM energy ratio is 0.9999999675, with one start and one stop activity callback. This is evidence of preservation in that fixture, not universal audio-quality equivalence.

## 4. Measured before/after behavior

### Output clock: the strongest finding

In the latest three normal-turn runs per variant:

| Opening measurement | Legacy | Explicit candidate |
|---|---:|---:|
| Local bot-speaking duration | approximately 1.441 s | approximately 3.481 s |
| Serialized PCMU duration queued by first bot stop | 3.50575 s | 3.520 s |

The legacy event releases the local speaking state roughly two seconds before the queued audio duration has elapsed. Explicit-mode conversion before packetization restores local pacing in the tested path. This matters to opening mute and interruption decisions.

**Boundary:** serialized PCMU byte duration is not carrier playout. No handset or Telnyx playback acknowledgment was measured. Codec paths other than the tested 8 kHz PCMU path are not certified here.

### Normal turn and short date correction

Definition: last injected utterance-packet end to first wire media after the matching TTS request. Each provider client has a scripted 20 ms delay. Each row has only three observations. Percentiles use inclusive linear interpolation and are descriptive sample statistics, not estimates of production p95.

| Scenario / variant | Raw samples, ms | Sample p50, ms | Sample p95, ms |
|---|---|---:|---:|
| Normal, legacy | 337.234, 340.658, 338.062 | 338.062 | 340.398 |
| Normal, explicit | 276.195, 350.009, 274.475 | 276.195 | 342.628 |
| Date correction, 0.45 s pause, legacy | 3226.874, 3226.908, 3205.596 | 3226.874 | 3226.905 |
| Date correction, 0.45 s pause, explicit, filter off | 3367.892, 3366.416, 3366.437 | 3366.437 | 3367.746 |
| Date correction, 0.45 s pause, explicit, filter on | 3365.854, 3367.809, 3346.867 | 3365.854 | 3367.613 |

Normal-turn median is lower, but sample p95 is essentially unchanged/slightly higher. **Do not claim a general p95 improvement.** All short-pause variants wait and speak only Thursday. The acoustic turn detector already handles this fixture; it provides no evidence of added benefit from semantic completeness. Short-date response times remain above three seconds and are an unresolved latency observation, not hidden behind the normal-turn result.

### Long date correction: benefit and penalty

A 3.6-second pause exceeds the acoustic fallback and actually exercises the semantic gate:

| Explicit variant | Spoken text, all 3 runs | Raw samples, ms | Sample p50 / p95, ms |
|---|---|---|---:|
| Filter off | `Tuesday.` then `Thursday afternoon.` | 1699.449, 1701.984, 1687.953 | 1699.449 / 1701.730 |
| Filter on | `Thursday afternoon.` only | 3331.591, 3332.161, 3329.627 | 3331.591 / 3332.104 |

The filter suppresses the scripted premature response, but in this fresh sample the corrected response takes roughly 1.6 seconds longer. Earlier retained filter-on samples were 3311.924, 361.681, and 3329.845 ms; that variability is preserved rather than discarded. There is no broad latency-win claim.

The Google client returns deliberately scripted completeness markers. Passing tests establish propagation, suppression and marker stripping through real framework components; they do not establish that a real model correctly judges Tuesday as incomplete.

## 5. Native inventory and version limits

| Item | Installed 1.8.0 evidence / handling |
|---|---|
| Smart Turn | Already the default stop strategy; candidate makes it explicit, using bundled `smart-turn-v3.2-cpu.onnx` |
| VAD defaults | Confidence 0.7, start 0.2 s, stop 0.2 s, minimum volume 0.6; no threshold retuning |
| Turn start | Conditional idle VAD plus native MinWords during bot speech; defaults are replaced, not appended |
| Groq | Batch STT supplies no interim recognition; `use_interim=False` avoids implying streaming barge-in |
| Opening mute | Available native `MuteUntilFirstBotCompleteUserMuteStrategy`; stricter than first-speech-only muting |
| Empty turn | `EmptyUserTurnConfig` / `empty_user_turn` unavailable; existing silence-reprompt policy retained |
| Filter incomplete | Available, appointment-only opt-in experiment; normal default remains off |
| Idle ownership | Aggregator conversational idle timeout remains 0; regular application silence policy remains owner |
| Missing newer features | No copied `ClassifierUserTurnStrategies`, `ServiceMetricsObserver`, eager-turn or speculation APIs |

Native timer constants include 3 s maximum Smart Turn silence, Groq's 1.54 s library STT fallback constant, and a 5 s user-turn stop backstop. They are not blindly additive and are not measured Muse latency. Current API signatures and source hashes are recorded in `turn-inventory.json`.

## 6. Flags and rollback

Configuration is evaluated when constructing each new media pipeline:

```bash
# Existing behavior remains the default; also the immediate candidate rollback.
AI_CALLING_TURN_STACK=legacy

# Opt-in candidate, without experimental semantic completeness.
AI_CALLING_TURN_STACK=explicit
AI_CALLING_FILTER_INCOMPLETE_TURNS=0
AI_CALLING_MIN_INTERRUPT_WORDS=2

# Experimental appointment_scheduling playbook trial only.
AI_CALLING_TURN_STACK=explicit
AI_CALLING_FILTER_INCOMPLETE_TURNS=1
```

Rollback affects newly created pipelines, not an already-running session. No flag was set on Muse. Invalid mode/threshold values are validated by the policy tests; consult `server/turn_policy.py` for the exact contract. Do not infer that all one-word utterances are expendable: batch timing, one-word idle answers and safety-language handling need the existing controller and further conversational coverage, not threshold inflation.

## 7. Reproduce offline

Use a Python 3.12 environment installed from `server/requirements.lock`. The commands below do not dial, deploy or call provider APIs. Run from the repository root. The WAVs are already checked in.

```bash
python tools/offline_verify.py --output logs/offline-qa/recheck-application.json
python tools/turn_stack_inventory.py --output logs/offline-qa/recheck-inventory.json
python test-pack-v1/Muse_Telnyx_Integration_Test_Pack/run_tests.py --output logs/offline-qa/recheck-reference.json
python test-pack-v1/Muse_Telnyx_Integration_Test_Pack/run_mutations.py --output logs/offline-qa/recheck-reference-mutations.json
python tools/verify_reference_manifest.py test-pack-v1/Muse_Telnyx_Integration_Test_Pack
python tools/verify_turn_mutations.py --output logs/offline-qa/recheck-mutations
python tools/benchmark_turn_stack.py --output logs/offline-qa/recheck-short --repetitions 3
python tools/benchmark_turn_stack.py --output logs/offline-qa/recheck-long --scenario date_correction --explicit-only --correction-pause 3.6 --repetitions 3
python -m pip check
```

The existing reference runner, mutation checks and manifest verifier remain required; their measured results and worker timings are preserved in `wave-summary.json`. Choose new output directories to preserve earlier receipts. The negative-control runner refuses a nonempty output directory. Audio-heavy benchmarks and mutations should run sequentially, not in competing timing workers.

## 8. Cut, deferred and not established

- **BLOCKED-BY-VERSION:** the exact newer native empty-turn constructor API. No dependency upgrade is authorized or necessary for this checkpoint.
- **Experimental/off:** completeness filtering. Marker stripping and scripted suppression pass; filter-specific error/failover races, interaction with its native reengagement timer, and broader conversational quality are not certified. Existing failover regressions pass but are not a substitute for that combined-mode coverage.
- **Not measured:** actual STT entity/recognition accuracy, provider model reasoning, voice quality, handset playback, production percentiles, live token/cost deltas, or current Muse behavior.
- **Further robustness coverage:** a recorded conversational corpus, delayed batch transcripts around bot-state transitions, repeated/disordered provider turn events, and full client-seam one-word idle-answer coverage. Native unit contracts cover ordinary one-word idle behavior, not every timing race.
- Synthetic `yeah` and `Tuesday` fixtures were slowed to reliably cross the unchanged VAD threshold. The unmodified Tuesday waveform and earlier failures are retained. A multi-sentence reply fixture was reduced to one sentence to match the actual ResponseGate segmentation. These are fixture corrections, not claims of production fixes or real-world recognition improvement.
- No provider spend was incurred in these tests; no live cost reduction was measured.
- No branch was published. The delivered bundle preserves the local commits without credentials.

## 9. Next package and integration gate

WP-2 is the single-owner CLOSING state machine. It is not implemented in this checkpoint. Its package branch must follow the work order's reviewed-base rule. Do not silently merge or stack WP-1 into the review branch. If WP-2 needs the new turn interfaces, obtain an explicit reviewed integration/branch-composition decision first; independent preparatory analysis can proceed without changing review or main.

## 10. Primary-source traceability

Official Pipecat documentation was refreshed on October 31, 2026 for comparison; the installed 1.8.0 wheel and tests control compatibility:

- https://docs.pipecat.ai/api-reference/server/utilities/turn-management/user-turn-strategies
- https://docs.pipecat.ai/api-reference/server/utilities/turn-management/user-mute-strategies
- https://docs.pipecat.ai/api-reference/server/utilities/turn-management/filter-incomplete-turns
- https://docs.pipecat.ai/api-reference/server/utilities/turn-detection/smart-turn-overview

The current web docs contain additional newer APIs. Nothing in those pages overrides the pinned-package source receipts.
