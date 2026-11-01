# AI Calling — complete new-chat handoff

**Prepared October 31, 2026. Read this file before older status documents.**

> **This branch is a preservation and handoff branch, not a deployment or a claim that all 13 improvement packages are complete.** Its application root remains the verified WP-2 controller checkpoint. WP-1 is preserved separately. Eight later WP-2 files are preserved as incomplete recovery fragments; they are not installed over the runnable root.

## 1. Fast orientation

| Item | Current, evidence-bounded state |
|---|---|
| Public repository | `https://github.com/nbeitelman1/ai-calling` |
| Handoff branch | `handoff/ai-calling-new-chat-2026-10-31` |
| Application root in this branch | WP-2 controller checkpoint `f11207098baa6dd9429f8675068a9ec0d607fc32`, plus handoff documents/artifacts |
| WP-1 saved implementation | `f65fa8e0bf2d7fcff3a38437006e4d86d2ebbd38`, preserved in its checkpoint and source excerpts |
| WP-2 current status | Controller verified; later runtime partially recovered; missing runtime adapter and media-test file must be rebuilt |
| WP-3 through WP-13 | Not implemented in this work sequence |
| Latest reproducible application baseline | 285/285 tests on the restored WP-2 controller source |
| Historical later runtime result | 308/308 was reported before a workspace reset; the full tested runtime snapshot and detailed final receipts were not preserved |
| Live calls / deployment in these improvement passes | None |
| Merges into `main` or the review branch | None |
| Repository visibility | Already public; no visibility change was required |

**The user's latest request was to preserve everything currently available in a public, separate GitHub branch, include the original Markdown files, explain completed and remaining work, and make the project transferable into a new chat. This handoff fulfills that preservation request; it does not silently redefine incomplete engineering as complete.**

### Read in this order

1. This file, especially the current-state table and missing-source section.
2. [Original improvement plan](handoff/original-inputs/A-GILE%20improvement%20plan.md). Its full text is also appended to this file for portability.
3. [WP-1 report](handoff/reports/AI-Calling-WP01-Report.md).
4. [WP-2 controller report](handoff/reports/AI-Calling-WP02-Progress-Report.md).
5. [WP-2 recovery report](handoff/reports/AI-Calling-WP02-Recovery-Report.md).
6. [Original debugging handoff](handoff/original-inputs/debug-handoff/00-START-HERE.md), then its numbered companion files.
7. Actual source and evidence at the paths identified below.

Older files deliberately retain their original bytes. Their statements such as “no live call has ever spoken,” “129 tests,” or “runtime implementation now passes 308 tests” describe different points in time. They must not override this current-state table without inspecting the associated source and receipts.

## 2. What the system is

This is a self-hosted outbound AI-calling application. Its broad architecture is:

1. A controlled call job, authorization/allowlist checks, budget reservation, and one-unresolved-call gate.
2. Telnyx call control and bidirectional media over WebSocket.
3. A FastAPI server hosting a Pipecat 1.8.0 pipeline.
4. Groq Whisper for speech recognition, Google Gemini for dialogue with fast/frontier fallback behavior, and ElevenLabs for speech output.
5. A deterministic controller, playbooks, conversation tracking, profile/memory storage, transcript/cost receipts, and reconciliation with carrier events.

The original investigation concerned calls that connected while the callee heard silence. The original Markdown package records tunnel limitations, proxy-altered WebSocket handshakes, missing NLTK data, service/probe divergence, and logging gaps. Those documents are important provenance, not proof that every historical failure remains unfixed today.

The later improvement plan contains 13 ordered work packages. It aims to improve turn-taking, deterministic closing, observability, carrier features, context efficiency, phone-audio quality, evaluations, provider resilience, memory extraction, commitments, semantic recall, and STT/TTS benchmarking.

## 3. Branch and revision map

| Revision | Role | How it is preserved |
|---|---|---|
| `ffaf4ccf4450359d3a87728906521316f8e562d4` | Reviewed work-order base on `review/ai-calling-candidate` | Ancestor of the application checkpoint; plan is in the repository |
| `d9fe3dc13c847beccff361502809f074bf96ae54` | Earlier WP-1 inventory/native-contract checkpoint | WP-1 bundle history |
| `f65fa8e0bf2d7fcff3a38437006e4d86d2ebbd38` | WP-1 implemented, offline-verified candidate | `handoff/checkpoints/ai-calling-wp01-f65fa8e-checkpoint.zip` |
| `f11207098baa6dd9429f8675068a9ec0d607fc32` | WP-2 pure-controller/native-contract checkpoint | Application root and its original checkpoint |
| `b849e453d4e355c95d3a9af7238c605945604d8e` | Earlier published offline-audit candidate | Existing `debug/offline-audit` branch and earlier release |
| `f1554840907e85c3fa03df502517716bd478d980` | Remote `main` observed before this handoff push | Left unchanged by the handoff operation |

WP-1 and WP-2 were developed as separate package branches from the reviewed base. WP-2 is **not** a stack containing all WP-1 production changes. The handoff does not merge those package branches. A future integrator must review overlapping edits in `server/live_pipeline.py`, especially VAD placement, transport pacing, idle ownership, and closing ownership.

The handoff commit's own SHA is obtained with `git rev-parse HEAD` or from the final publication receipt. It is intentionally not hardcoded into the content being committed, because a commit cannot embed its own final hash without changing it.

## 4. What is available in this branch

### Runnable baseline at the repository root

The root `server/`, `tests/`, `tools/`, playbooks, pinned requirements, and reference pack are from the saved WP-2 controller checkpoint. Handoff publication does not replace the runtime with recovered incomplete files.

Important root files include:

- `server/closing_state.py`: pure closing state and generation-tagged event rules.
- `server/call_controller.py`: `TurnRouter` integration with that pure state.
- `tests/test_closing_state.py`: deterministic controller and bounded ordering tests.
- `tests/test_closing_native_contracts.py`: pinned native API behavior tests.
- `tools/closing_baseline_bench.py`: reproduction of double-goodbye behavior through real services with client doubles.
- `tools/verify_closing_mutations.py`: isolated negative-control runner.
- `tools/offline_verify.py`: the repository's canonical offline application runner.

### Handoff directory

| Path | Contents |
|---|---|
| `handoff/original-inputs/` | The user's original improvement plan and all 11 Markdown files extracted from the original debugging ZIP |
| `handoff/reports/` | Prior audit, Muse, WP-1, WP-2 controller, and recovery reports |
| `handoff/checkpoints/` | Full saved WP-1, WP-2 controller, and recovery ZIPs |
| `handoff/wp01-reviewed-source/` | Browser-readable WP-1 production source excerpts; full tree and tests remain in its checkpoint |
| `handoff/wp02-recovered-source/` | Eight exact later WP-2 source recoveries, deliberately not installed at the application root |
| `handoff/checkpoint-tools/` | Restart-safe working-tree capture utility and its standalone tests |
| `handoff/evidence/` | Source-hash ledgers, saved verification receipts, and recovery records |
| `handoff/original-and-artifact-manifest.json` | Original-source provenance, hashes, sizes, and byte-preservation records |
| `handoff/CURRENT_STATE.json` | Portable, machine-readable status without reliance on this chat's temporary paths |

Do not run test discovery recursively over all archive/recovery directories. Use the canonical root `tools/offline_verify.py` runner. The recovery fragments intentionally contain tests for a currently missing module.

## 5. WP-1 — what was implemented and verified

**State: implementation saved and offline-verified; not deployed and not merged.**

WP-1 made turn-taking explicit while keeping the old path as the default:

- Added a version-scoped `TurnPolicy` and an opt-in explicit turn stack.
- Used native aggregator-side VAD and Smart Turn, removing the separate VAD processor in explicit mode rather than adding a second detector.
- Used native opening mute to protect the initial disclosure.
- Applied a minimum-word interruption rule while the bot speaks.
- Added a small idle-only VAD strategy so normal user turns do not wait unnecessarily for the batch-STT fallback.
- Corrected the observed output-clock problem by converting to 8 kHz before output packetization.
- Preserved an explicit empty-recognition policy rather than inventing an unavailable Pipecat API.
- Added an appointment-scoped semantic-completeness experiment, left off by default.
- Built a real-service media harness whose doubles sit at provider-client boundaries.

### WP-1 evidence

| Check | Saved result |
|---|---:|
| Application battery | 281/281, zero failures/errors/skips |
| Reference battery | 57/57 |
| Reference negative controls | 3/3 caught |
| WP-1 negative controls | 6/6 caught at intended assertions |
| Reference byte integrity | 28/28 files |
| Fresh repeated timing runs | 21 completed |
| Synthetic WAV hashes | 11/11 verified |
| Application external connection attempts | 0 |

The strongest measured finding was the local audio clock. Legacy bot-speaking state ended after roughly 1.44 seconds even though about 3.51 seconds of PCMU audio had been queued. The explicit path's local speaking interval was approximately 3.48 seconds for about 3.52 seconds of queued audio.

That is evidence about local output timing, not a carrier or handset playback measurement. Normal-turn latency samples did not establish a general p95 improvement. Short date-correction samples remained above three seconds. The optional semantic filter suppressed a deliberately scripted premature Tuesday response on the long-pause fixture, but the latest filtered sample added roughly 1.6 seconds. These limitations are retained in the WP-1 report.

### WP-1 flags

```bash
# Existing path remains default and is the candidate rollback.
AI_CALLING_TURN_STACK=legacy

# Opt-in explicit stack; experimental semantic filtering remains off.
AI_CALLING_TURN_STACK=explicit
AI_CALLING_FILTER_INCOMPLETE_TURNS=0
AI_CALLING_MIN_INTERRUPT_WORDS=2
```

No flag was enabled on the live service. Defaults, validation, and exact playbook scoping are defined in the saved source, not in this summary.

## 6. WP-2 — what is safely saved

**State: pure controller is saved; full later runtime is not presently reproducible.**

The saved checkpoint adds deterministic closing rules to the controller:

- Closing can be requested by caller goodbye or objective completion.
- One closing line can be claimed per closing generation.
- Acknowledgements do not create another closing line.
- A genuine interjection can reopen an ordinary closing once.
- The next closing is final; mandatory safety/limit endings do not reopen.
- Playback events carry a generation token so an old stop cannot close a newer generation.
- A clear/interruption is not counted as completed playback.
- A successful hangup claim requires a matching start and stop; failure escape paths remain explicit.

The checkpoint also established native Pipecat 1.8.0 contracts and reproduced the runtime defect. In the reproduced baseline, two scripted caller goodbyes produced two LLM requests and two spoken goodbyes, without natural termination. The real STT, LLM, TTS, transport, and serializer classes executed; recognition and model answers were scripted at their client boundaries.

### Revalidated saved baseline

| Check | Recovery verification |
|---|---:|
| Application tests | 285/285 |
| Controller tests within that suite | 28 |
| Native API tests within that suite | 7 |
| Bounded controller event-order sequences | 4,096 |
| Reference tests | 57/57 |
| Controller negative controls | 6/6 caught |
| Reference negative controls | 3/3 caught |
| Reference manifest | 28 files passed |
| Application socket guard | 7 loopback connections; 0 external attempts |
| Separate checkpoint-tool tests | 9/9 |

These counts apply to the restored `f112070` application source. A documentation-only handoff commit does not convert those controller tests into full runtime acceptance.

## 7. What happened to the later WP-2 runtime

A later implementation integrated the controller into the real media pipeline and was reported as passing a 308-test application run plus its reference and negative-control checks. Before it was committed and packaged, the temporary sandbox reset.

The saved `f112070` checkpoint survived. The complete uncommitted runtime tree and detailed final runtime receipts did not. The earlier 308-pass result is therefore **historical evidence about a lost working tree**, not a reusable certificate for the current repository or a new rebuild.

This was a checkpointing failure: the verified working tree should have been saved before reporting completion. The recovery workflow now preserves current working bytes, including explicitly selected untracked files, in addition to committed Git history.

### Eight exact recoveries

Complete retained edits/source blocks were recovered and checked against their previously recorded SHA-256 values:

| File under `handoff/wp02-recovered-source/` | SHA-256 |
|---|---|
| `server/live_pipeline.py` | `c72f30377e0d16cc4dcd3bde2cf8762afe215869bd6170142861086dea8fd51d` |
| `server/live_server.py` | `f66124301edea9c5e3e6aae73c4f7bd17669b285f494a1cba0ae53434939993b` |
| `server/closing_state.py` | `4efc26923afbf87a917bf04af0753e3d12d904529045cebb5d446389a1c6994b` |
| `server/prewarm_phrases.py` | `6708ddf8b07c0596d90d3c70ca812746e343044fe384f1d72fd5aafb8ef788c1` |
| `tests/test_closing_runtime.py` | `1b4a7450f289361d973adbba08193b46f570cc7a2a5fc9eb36fa675edb0c329c` |
| `tools/offline_verify.py` | `896f006cbe78ffff9da090a22b40e71c91f89390cb50282d47fcac3b98b15c40` |
| `tools/generate_turn_fixtures.py` | `4dfb45f4e0969d4a3cb4f4e1a52bb03e57406fc4c683875d4db2f36f1b2fd4c3` |
| `tools/verify_closing_runtime_mutations.py` | `8efc2b538155ebe573cb6e9d86b2695c16aa546e34b96b00f4b42c392b936322` |

Matching these hashes establishes exact recovery of those files—not successful operation of the incomplete combination.

### Still missing or requiring regeneration

1. `server/closing_runtime.py`: runtime owner, guards, cached playback, idle events, interruption barrier, and hangup coordination.
2. `tests/test_closing_media.py`: complete media test helper and eleven final integration scenarios.
3. New generated speech fixture bytes and the final fixture manifest.
4. Detailed final runtime receipts and some final documentation/inventory changes.

The missing runtime file previously had SHA-256 `313011621b73fc62ea2b13e6a6565a4106fe728b978453d5202136511ceb467f`. A hash cannot reconstruct its contents. Do not invent the missing file and label it exact recovery.

## 8. Exact next engineering task

Build a new runtime adapter and media-test helper against the **recovered interfaces**, the actual saved controller, and the installed Pipecat 1.8.0 source. Then combine the recovered fragments with that new adapter in an isolated implementation branch and rerun acceptance.

The recovered pipeline expects the runtime integration to support:

- `ClosingRuntime`, `ClosingInputGate`, `ClosingContextGate`, `ClosingOutputGate`, and `closing_enabled`.
- Pure, fast rule-based routing; no model or network request inside the decision itself.
- A generation/epoch boundary so old model output cannot leak after reopening.
- One cached line per closing generation; no new LLM or TTS-provider request for the ending.
- Native idle callbacks and explicit handling of the pinned-version empty-turn rearm gap.
- A transport acknowledgement of an interruption before replacement audio starts.
- Playback start/stop, duration, generation and clear/interruption checks.
- One hangup dispatch across normal completion, overlapping causes, failures, cancellation, and unexpected runner exit.
- Correct preservation of existing reason codes, including cost/limit outcomes.
- Cached lines excluded from LLM request accounting.

Inspect the recovered `tests/test_closing_runtime.py` before writing the new adapter. It preserves twelve deterministic unit contracts. Inspect the recovered mutation runner to see the deliberate breaks that should be caught. Do not restore the modified `live_server.py` or `live_pipeline.py` alone: both refer to the missing module.

### Required media acceptance scenarios

1. Two caller goodbyes: one cached closing line and one hangup.
2. One real question reopens; the next closing is final.
3. Objective completion ends without an extra LLM turn.
4. Human request emits the verbatim cached hand-back line.
5. Missing closing cache is an explicit failure, not silent live synthesis.
6. Native idle: one cached reprompt followed by cached closing.
7. Empty transcript rearms the one native idle owner without creating an LLM turn.
8. Duration limit enters the closing owner.
9. Overlapping duration, cost and repeated closing signals dispatch once.
10. An in-flight old model response cannot be spoken after closing.
11. Unexpected pipeline end still terminates the call leg exactly once.

### Known traps from the lost runtime investigation

- A short sleep is not an interruption-processing barrier. A clear can still be travelling through the framework when the next cached line starts.
- Calling a frame processor's parent interruption handler before deciding to suppress the interruption may already clear that processor's queued audio.
- Disabling serializer auto-hangup creates a responsibility to terminate the leg even when the pipeline runner exits unexpectedly.
- Do not remap existing budget reason codes to invented vocabulary that the pure state does not accept.
- A cached assistant transcript entry is not a model request and must not inflate model request accounting.
- Synthetic audio can be split differently when 20 ms packets enter 32 ms VAD windows at different offsets. Stabilize and document fixture timing; do not treat fixture edits as evidence of real ASR improvement.
- Some control-flow tests use a stable speech waveform with a scripted domain transcript at the STT client boundary. Label that accurately; it does not measure recognition accuracy.

Do not chase the number 308 as an acceptance target. A new implementation requires a new source-bound pass for the required behaviors, even if its test count differs.

## 9. Full 13-package roadmap

The appended original plan is authoritative for detailed steps and acceptance criteria. This table is a current progress map, not a replacement specification.

| WP | Scope | Current state | Remaining work / main dependency |
|---|---|---|---|
| 1 | Pipecat turn stack | Saved, offline-verified | Review and deliberate integration with later packages; live claims remain unverified |
| 2 | Closing state machine | Controller saved; runtime partially recovered | Rebuild missing adapter/helper, integrate recovered files, regenerate fixtures, rerun checks, checkpoint |
| 3 | Observers and latency | Not started | Per-stage/per-turn telemetry, sample counts, error/fallback visibility; use WP-1/WP-2 events |
| 4 | Telnyx AMD/recording/second transcript | Not started | Flagged payloads and webhook state handling, offline tests; live portal/consent actions remain owner decisions |
| 5 | Context hygiene | Not started | Scoped prompts, frozen-prefix caching and supported summarization without losing rules or tool pairing |
| 6 | Phone-chain TTS/cache | Not started | Tune supported 8 kHz output/settings and routine clips; verify wrapper APIs before use |
| 7 | Evaluation harness | Not started | Scenario assertions, simulated callers/judging and scorecard; no live-call layer execution |
| 8 | Cross-provider resilience | Not started | Non-Google leg, deadlines and circuit breaker; preserve existing failure-boundary semantics |
| 9 | Post-call extraction | Not started | Schema-constrained extraction, validated memory merge and review queue |
| 10 | Commitment ledger | Not started | Lifecycle, overdue sweep, evidence of fulfillment and next-call surfacing |
| 11 | Semantic memory | Not started | Local embeddings/sqlite-vec, deduplication, repetition signal and hybrid retrieval |
| 12 | Streaming STT experiment | Not started | Real-corpus benchmark and recommendation; do not fabricate recorded-call coverage or silently switch providers |
| 13 | Phone-chain TTS experiment | Not started | Blind 8 kHz listening/latency/entity/cost comparison and recommendation; no automatic provider swap |

The final program scorecard requires evidence for task completion, zero double goodbye, reduced repetition, latency targets, forced-outage recovery, and a two-call memory/commitment round trip. Those are future acceptance targets. They have not been established by this handoff.

Some experimental acceptance work requires real recordings, provider access, or owner decisions. Prepare runnable tools when possible, but mark unavailable measurement work blocked rather than inventing results. Exact provider names, APIs, and price figures in the old plan require revalidation at experiment time.

## 10. Non-negotiable operational boundaries

The user requested implementation and handoff, not a new live call or deployment. Keep these controls:

- No live phone calls. Each real call needs fresh, specific approval; this handoff is not that approval.
- No deployment, systemd changes, live database migration, or service restart.
- No merge into `main` or `review/ai-calling-candidate` without the owner's reviewed decision.
- Preserve per-call/monthly budgets, duration controls, reservations/settlement behavior and the unresolved-call gate.
- Never request or publish credentials, authentication files, personal access tokens, or private browser-login state.
- Do not supply sensitive identity data, consent to background checks, sign legal attestations, or guess a date of birth.
- Answer identity questions honestly; asking whether the assistant is AI is not itself a hand-back request.
- Test real services at their provider-client boundaries. Whole-service doubles may be used only for clearly labeled unit tests, not end-to-end claims.
- Keep Pipecat pinned to 1.8.0 unless a later explicit instruction changes the plan. Verify symbols in the installed package.

### Literal honesty lines

Opening:

> Hi, this is Nathan's assistant calling on his behalf.

When asked whether it is AI:

> I'm Nathan's AI assistant, calling for him. I can keep going, or I can bring Nathan in if you'd prefer to talk to him directly.

On decline or human request:

> Nathan will need to take this over. I'll end the call now.

These are source-level requirements, not suggestions for stylistic rewriting.

## 11. Offline setup and baseline verification

Use a suitable Linux/Python environment. Installation downloads packages; the verification commands below are offline application checks and do not place calls.

```bash
git clone --branch handoff/ai-calling-new-chat-2026-10-31 \
  https://github.com/nbeitelman1/ai-calling.git
cd ai-calling

python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r server/requirements.lock
python -m pip check

python tools/offline_verify.py --output logs/offline-qa/new-chat-baseline.json
python test-pack-v1/Muse_Telnyx_Integration_Test_Pack/run_tests.py \
  --output logs/offline-qa/new-chat-reference.json
python test-pack-v1/Muse_Telnyx_Integration_Test_Pack/run_mutations.py \
  --output logs/offline-qa/new-chat-reference-mutations.json
python tools/verify_reference_manifest.py test-pack-v1/Muse_Telnyx_Integration_Test_Pack
python tools/verify_closing_mutations.py --output logs/offline-qa/new-chat-controller-mutations
```

The expected saved root baseline is 285 tests, not 281 and not 308. The counts differ because these are different package snapshots. Investigate any new failure at its causal boundary rather than weakening an assertion or attributing everything to the environment.

For WP-1, open its checkpoint in a separate directory, inspect `HANDOFF.md` and `CHECKPOINT.json`, and restore its bundle/source independently. Its saved root expects 281 tests. Do not overwrite the WP-2 root to run WP-1 tests.

Temporary paths printed in older reports are historical sandbox locations. Use paths in this Git checkout or the checkpoint archives, not assumptions that an old `/tmp` directory still exists.

## 12. Checkpointing and new-chat continuity

A Git bundle captures refs and committed history, not every uncommitted working file. `git archive HEAD` likewise captures a committed tree. That distinction caused the gap between the historical test pass and the available source.

The included utility captures committed history plus exact working-file bytes and explicitly selected untracked files:

```bash
python handoff/checkpoint-tools/checkpoint_worktree.py \
  --repo . \
  --include server/new_runtime_module.py \
  --include tests/new_runtime_tests.py \
  --output /persistent/location/ai-calling-checkpoint-unique-name.zip
```

List every new non-ignored file. Omit `--include` options when there are no untracked files. The tool refuses unreviewed untracked files, obvious credential paths/patterns, symlinks, overwrites and oversized snapshots; it detects changes during capture and verifies its archive. It does not certify application behavior or perform a commit, push, merge, deployment or call.

Its scan is bounded, not exhaustive DLP. It does not recreate index staging, permission changes, ignored files, repository configuration, hooks or stashes. Preserve test receipts with source hashes, and save immediately after meaningful edits and verification—before reporting completion or changing tasks.

## 13. Original Markdown documents included

All twelve documents below are copied byte-for-byte and listed with their original-source hashes in the artifact manifest:

1. `handoff/original-inputs/A-GILE improvement plan.md`
2. `handoff/original-inputs/debug-handoff/00-START-HERE.md`
3. `handoff/original-inputs/debug-handoff/01-PROBLEM-TIMELINE.md`
4. `handoff/original-inputs/debug-handoff/02-ARCHITECTURE.md`
5. `handoff/original-inputs/debug-handoff/03-ENVIRONMENT-AND-DEPLOY.md`
6. `handoff/original-inputs/debug-handoff/04-SOURCE-live_pipeline.py.md`
7. `handoff/original-inputs/debug-handoff/05-SOURCE-live_server.py.md`
8. `handoff/original-inputs/debug-handoff/06a-SOURCE-telnyx_media.py.md`
9. `handoff/original-inputs/debug-handoff/06b-SOURCE-telnyx_adapter-and-support.md`
10. `handoff/original-inputs/debug-handoff/07-EVIDENCE.md`
11. `handoff/original-inputs/debug-handoff/08-SUSPECTS-AND-OPEN-QUESTIONS.md`
12. `handoff/original-inputs/debug-handoff/09-FULL-PROJECT-STATE.md`

Full raw conversation transcripts, email exports, browser authorization files and credentials are intentionally excluded from this public branch.

## 14. Suggested prompt for the next chat

```text
Continue the AI Calling project from the public repository:
https://github.com/nbeitelman1/ai-calling
Branch: handoff/ai-calling-new-chat-2026-10-31

First read START_HERE_NEW_CHAT.md, handoff/CURRENT_STATE.json, the original
improvement plan, and the WP-1/WP-2 recovery reports. Verify the source
revision and distinguish the runnable f112070 controller baseline from
WP-1's separate checkpoint and the eight incomplete WP-2 recovery files.

Immediate task: rebuild the missing closing_runtime.py and complete
media-test helper against the recovered interfaces and pinned Pipecat 1.8.0,
then integrate and test WP-2. The old 308-test result is historical and does
not certify this rebuild. Do not blindly overlay incomplete fragments.

Preserve the source and test receipts in a durable checkpoint before any
completion claim. Then continue the 13-package plan in dependency order.
Do not dial, deploy, merge, change live databases, or enable carrier features
without the specific approvals required by the plan. Use real provider
services with client-boundary doubles for offline end-to-end tests. Report
measurement gaps and version blockers honestly.
```

## 15. Source and publication notes

The current state in sections 1–14 is a synthesis of saved source, delivered reports, source-hash ledgers and verification receipts. The next appendix is the original work order, included verbatim at the text level for portability. Its historical statements and planned targets are not new measurements.

Useful official references retained by the project include Git's bundle/archive/status manuals, Pipecat's turn-management/idle/serializer documentation, and Telnyx's media-streaming/call-control documentation. For implementation compatibility, installed source takes precedence over documentation for newer releases.

A successful publication means this separate branch and its artifacts are reachable on GitHub. It does not mean the missing runtime has been rebuilt, all packages are complete, or the application is deployed. The final publication receipt records the pushed commit and unchanged remote `main`.

---

# Appendix A — Original A-GILE improvement plan

The standalone original remains at `handoff/original-inputs/A-GILE improvement plan.md` with its original bytes and SHA-256. The following is its text, appended without rewriting its requirements.

---

# A-GILE Improvement Plan — AI Calling Assistant

**Prepared:** Oct 30, 2026 (evening, EDT) · **For:** A-GILE (executing agent)
**Source research:** `docs/RESEARCH_IMPROVEMENTS_2026-10-31.md` (committed
alongside this plan — read it; every work package below cites it)
**Design spec for the intelligence layer:** `docs/PERSONA_MEMORY_SPEC.md`
**Current state:** `PROJECT_STATE_2026-10-30.md` (read it first)

This is a complete work order. Execute the packages in order. Where a
package says "verify against the installed package," do exactly that
before writing code — several research items were written against
Pipecat's newest docs and our deployment is one minor behind.

---

## 0. Coordinates and current reality

- **Repo:** `nbeitelman1/ai-calling` (GitHub, public by Nathan's choice).
- **Work from:** branch `review/ai-calling-candidate` (HEAD `34fcdba` at
  plan time; this plan lands on top of it). Working tree:
  `~/ai-calling-candidate`. Reference venv:
  `/home/hatch/.venvs/ai-calling-candidate-b849e45`.
- **`main` is OFF LIMITS** — read-only reference only. It carries an
  older, diverged implementation of the same fixes. Merging the
  candidate into `main` is a separate, future, human-reviewed project.
  Do not merge, rebase, cherry-pick, or push to `main`.
- **Deployed stack (verified Oct 30, 2026 against the venv, not docs):**
  - **Pipecat `pipecat-ai` 1.8.0** on Python 3.12.3. google-genai 2.34.0,
    groq 1.7.0. This resolves the research's open question: we are on
    1.x (not 0.0.x), but **not** on the newest 1.x — see Ground Rule 8.
  - Pipeline (`server/live_pipeline.py`, `build_live_pipeline()`):
    Groq Whisper STT (batch, one request per local-VAD speech segment)
    → `FailoverGoogleLLMService` (`server/llm_failover.py`
    `LLMFailoverMixin` over `GoogleLLMService`; chain = fast pool
    `gemini-3.1-flash-lite, gemini-3.5-flash-lite` → frontier
    `gemini-3.8-flash`; pre-first-chunk failover on 5xx/429/408/409/404/
    transport/timeout; sticky; SDK gets 1 attempt per model)
    → `CachedElevenLabsTTSService` (ElevenLabs `eleven_flash_v2_5`,
    premade voice "Jessica").
  - VAD today is a pipeline `VADProcessor` wrapping
    `_LoggingSileroVADAnalyzer` — **not** aggregator-side. No explicit
    turn-stop strategy is configured; no observers are wired.
  - Intelligence layer (live): `server/persona.py` (Carnegie-derived
    persona + verbatim honesty lines), `server/call_controller.py`
    (frozen `CallBrief` via `build_brief()`, pure-rule `TurnRouter`,
    `ControllerDecider` seam — `RuleBasedDecider` live,
    `HostedJevDecider` an inert stub), `server/profile_store.py`
    (SQLite temporal store: entities/aliases, facts with
    `valid_from`/`valid_to` + `superseded_by`, episodes, commitments;
    live DB `~/workspace/ai-calling/logs/profile.db`),
    `server/conversation_state.py` (`ConversationTracker` —
    token-similarity repetition guard), `server/playbooks.py` +
    `playbooks/*.json` (appointment_scheduling, information_gathering,
    follow_up_status).
  - Telephony: `tools/place_call.py` / `tools/call.sh` are the only
    sanctioned dial path; the Telnyx Call Control dial payload is built
    in `server/telnyx_adapter/wire.py` (`dial_request`), carried by
    `server/telnyx_adapter/adapter.py` / `transport.py`; webhooks land
    in `server/telnyx_webhook.py`; media in `server/live_server.py`.
  - **Test state at plan time:** candidate suite **250/250**, reference
    pack (`test-pack-v1/`) **57/57**, mutations **3/3 caught**, manifest
    verified. Runner: `tools/offline_verify.py` (unittest, socket
    egress guard, credentials stripped — loopback only).
  - The system is **live and working**: it holds real two-way
    conversations with Nathan. Treat every change as surgery on a
    working system, not a greenfield build.

## 1. Ground rules (non-negotiable)

1. **NO LIVE PHONE CALLS.** Not one. Nathan's approval model is a fresh
   per-call word for a single call to a single number, and this plan
   carries no such approval. All verification is offline: the
   `tools/offline_verify.py` battery, the bench media-peer harness,
   and — where a package explicitly says so — real-provider API-level
   bench runs through the production media handler with a fake peer
   (the pattern used for the Oct 30 failover proof). **Never** run
   `tools/place_call.py` or `tools/call.sh` without `--dry-run`.
   Never invoke the Telnyx dial endpoint.
2. **Tests fake at the provider-client layer, never the whole
   service.** Precedent: `tests/test_llm_failover.py` fakes
   `google.genai.Client`, so the real `GoogleLLMService`, its error
   path, the aggregators and the response gate all execute. The
   counterexample is named and dated: the intelligence-pass evals
   faked the whole LLM service, a real Gemini 503 shipped through
   that blind spot, and the Oct 30, 5:04pm EDT retest call ran 63
   seconds of silence. Any test that claims to cover the turn path
   and replaces the whole LLM/STT/TTS service is invalid — write it
   at the client seam or label it a pure unit test.
3. **Secret-scan every diff before every commit.** The repo is
   public. Nathan's own numbers already appear in docs by his
   explicit choice — do not add any *new* personal data (no dates of
   birth, no additional phone numbers, no addresses beyond what the
   repo already contains), and never commit credential values, API
   keys, or `.provider-credentials` content. Credentials live outside
   the repo; tests run credential-free by design.
4. **The honesty lines are verbatim and untouchable.** Opening:
   "Hi, this is Nathan's assistant calling on his behalf." Asked if
   it is AI: "I'm Nathan's AI assistant, calling for him. I can keep
   going, or I can bring Nathan in if you'd prefer to talk to him
   directly." On decline / human request: "Nathan will need to take
   this over. I'll end the call now." Never make the assistant claim
   to be human. Asking "are you AI" is an identity question answered
   honestly while the call *continues* — it is not a hand-back
   trigger.
5. **Caps and safety semantics are unchanged** unless a package says
   otherwise: $1.00 per call, $20/month, 10-minute default call cap;
   budget reservations convert to spend at settlement; the one-call
   gate (zero unresolved attempts) stays. Standing no-go rules: no
   SSN entry, no background-check consent, no legal-attestation
   signatures in Nathan's or Lauren's name; never guess or supply a
   date of birth (the appointment playbook hands back instead).
6. **Branching and commits.** One new branch per work package, off
   `review/ai-calling-candidate`, named `agile/wp-<nn>-<slug>`.
   Logical commits within the branch. Push each branch to origin.
   **Do not merge** any package branch into
   `review/ai-calling-candidate` or `main` — merging is a reviewed
   step Nathan controls. Report per package (format in §4) before
   starting the next package.
7. **No deployment to the live service.** Do not run
   `tools/restore_stack.sh`, do not restart `ai-calling-live`, do not
   alter systemd units or the live databases. (For context only:
   redeploys are `restore_stack.sh` + an *explicit*
   `systemctl restart ai-calling-live` + verifying the service start
   timestamp postdates the HEAD commit — restore alone does not
   restart when units are unchanged.) If a package's verification
   appears to require the live service, **stop and flag it** as a
   decision for Nathan in your package report instead.
8. **Version discipline — target the installed Pipecat 1.8.0.** The
   research cites current docs; some cited features postdate 1.8.0.
   Before using any framework symbol named in a package, prove it
   exists in the installed package (`python -c "from pipecat... import
   X"` in the reference venv, or `inspect` the module). If a symbol
   is absent: do **not** upgrade Pipecat, do **not** vendor a clone —
   implement the smallest local equivalent inside our own modules,
   or mark that sub-item `BLOCKED-BY-VERSION` in the report and move
   on. Known at plan time: `EagerUserTurnStopStrategy` +
   SpeculationGate are Pipecat ≥1.9 per the research — out of scope
   (see WP-12); the Backchannel extension needs ≥1.6, so it *should*
   be available in 1.8.0 — verify, don't assume.
9. **The in-call controller stays pure, fast, and fail-open.**
   `TurnRouter` decisions are rule-based, no I/O, measured at
   ~0.04 ms/turn on real transcripts. Nothing you add to the in-call
   path may block on a network call or a model; anything heavier
   goes pre-call (frozen brief) or post-call (extraction), or runs
   async with a timeout that fails open to today's behaviour.
10. **Environment landmines (from production scars; full list in the
    repo README + `PROJECT_STATE_2026-10-30.md`).** The egress proxy
    breaks naive httpx use (bare `::1` in `NO_PROXY`; certifi lacks
    the proxy CA) — the fix lives in `systemd/service-env-fix.env`
    and only works because systemd `EnvironmentFile` values beat
    `Environment=` at spawn. Preflight passing proves little (it
    uses aiohttp). If a bench run shows opaque SDK "Connection
    error"s, suspect this first and probe under the service's exact
    environment. Hosted tunl cannot carry media WebSockets; Pinggy
    can and does. Never print credential values anywhere, including
    logs and commit messages.

---

## 2. Work packages (execute in this order)

Effort key (from the research): **S** = hours–2 days · **M** = 3
days–2 weeks · **L** = >2 weeks / new infrastructure. Line numbers
are as of `34fcdba` — re-grep before editing; treat them as signposts,
not contracts.

### WP-1 — Pipecat 1.8.0 turn-stack audit and configuration (S, $0)
*Research rec #1, §1a/§1b/§1c, §8. Branch: `agile/wp-01-turn-stack`.*

**Goal.** Make turn-taking explicit and current: one silence arbiter,
aggregator-side VAD, backchannels that can't barge in, and a
deliberate empty-turn policy — replacing whatever defaults the
pipeline currently inherits by accident.

**Starting points.** `server/live_pipeline.py`: `build_live_pipeline()`
(~line 1039), `_LoggingSileroVADAnalyzer` (~979), the `VADProcessor`
in the processor list (~1216), the `llm_response_universal`
aggregator construction. `server/conversation_state.py` (existing
tracker interplay).

**Steps.**
1. Inventory first: write down which turn machinery 1.8.0 applies by
   default in our construction (stop strategy, VAD placement, timer
   stack) vs what we set explicitly. Check for duplicated/stacked
   endpointing timers (transport + processor + aggregator) — the
   classic self-inflicted dead-air bug. Put the inventory in the
   package report.
2. Verify-in-venv, then adopt where present in 1.8.0:
   `LocalSmartTurnAnalyzerV3` as the (only) turn-stop arbiter via
   `TurnAnalyzerUserTurnStopStrategy`; move VAD configuration onto
   the user aggregator (`LLMUserAggregatorParams(vad_analyzer=
   SileroVADAnalyzer(params=VADParams(stop_secs=0.2)))`) and remove
   the now-redundant pipeline `VADProcessor` *only if* the bench
   harness proves equivalent VAD event flow (the logging subclass's
   observability must be preserved — port its logging).
3. `MinWordsUserTurnStartStrategy(min_words=2..3, use_interim=True)`
   so "uh-huh / yeah / okay" no longer cancels speech. Note: interim
   transcripts require a streaming STT — Groq Whisper is batch, so
   if `use_interim` has nothing to consume, configure the closest
   1.8.0-supported equivalent and record the limitation for WP-12.
4. Set `empty_user_turn` behaviour on the user aggregator
   deliberately (recover-and-resume vs re-prompt), and protect the
   verbatim disclosure line with a mute strategy
   (`FirstSpeechUserMuteStrategy` or 1.8.0 equivalent) so the opening
   cannot be talked over.
5. Trial a turn-completeness stop strategy
   (`FilterIncompleteUserTurnStrategies`, classifier variant if the
   filter taxes the main model) behind a config flag, enabled only
   for the appointment_scheduling playbook (long, number-heavy
   turns), and measure its added latency on the bench harness.

**Acceptance.** Full battery green (candidate + reference +
mutations + manifest) with new tests at the client seam for: a
backchannel utterance not cancelling TTS; a two-part user turn
("Tuesday… no wait, Thursday") not being cut at the pause (bench,
scripted); the disclosure line completing under scripted over-talk.
Bench before/after: user-end → first-audio timings on the standard
bench script, reported as p50/p95. Inventory (step 1) in the report.

**Risks/unknowns.** Aggregator-side VAD changes event ordering for
the custom processors (the RC4 lesson: every custom FrameProcessor
must call `super().process_frame()` — re-audit all five after any
pipeline recomposition). If Smart Turn v3 is not the 1.8.0 default
in our construction, say so plainly rather than assuming the docs.

### WP-2 — CLOSING state machine: kill the double-goodbye (S, $0)
*Research rec #2, §1e. Branch: `agile/wp-02-closing-state`.*

**Goal.** Closing becomes a controller state, not an LLM behaviour:
one closing line, then hangup — cancellable exactly once by a real
interjection, immune to goodbye ping-pong.

**Starting points.** `server/call_controller.py` (`TurnRouter`,
`RouteDecision`), `server/live_pipeline.py` (terminal-discipline
wiring: `loop_in_requested` handling, `TranscriptTap`, the hangup
path and `TelnyxFrameSerializer` `auto_hang_up`), playbook end
conditions in `playbooks/*.json`.

**Steps.**
1. Add `CLOSING` to the controller's state model: entered when the
   objective completes, when the caller expresses goodbye intent
   (cheap keyword/intent check, not an LLM turn), or when the
   hand-back line fires (existing terminal path becomes a CLOSING
   entry with its own verbatim line).
2. In `CLOSING`: the LLM is not run for new turns. Exactly one
   closing line is spoken — from the phrase cache, not live TTS
   (WP-6 grows the cache; a single hardcoded cached line is fine
   here). Hangup arms on bot-stopped-speaking / playback-ended,
   never on text generation.
3. A caller interjection during `CLOSING` cancels the pending
   hangup **once** and reopens the conversation; a second closing
   is final. Mere acknowledgements ("bye", "thanks") do not cancel.
4. Safety nets independent of the LLM: user-silence handling via
   `LLMUserAggregatorParams(user_idle_timeout=…)` /
   `on_user_turn_idle` (verify the 1.8.0 surface) — 1–2
   re-engagement prompts, then end; hard max-duration end
   (the existing 10-minute cap must terminate through CLOSING too).

**Acceptance.** Scripted bench conversations: (a) objective-complete
call ends with exactly one closing line and a hangup, zero extra
assistant turns after the caller's goodbye; (b) caller interjects
mid-goodbye with a real question → conversation reopens once;
(c) double-goodbye scenario from the Oct 30 retest transcript
(caller says goodbye twice) produces exactly one closing line.
Outcome/reason strings land in the call record. Full battery green.

**Risks/unknowns.** Hangup idempotency (one EndFrame → one Telnyx
hangup) — test a duplicate-trigger path. Do not regress the
existing `loop_in_requested` outcome semantics; records must stay
comparable.

### WP-3 — Instrumentation: observers and per-stage latency (S, ~$0)
*Research rec #3, §2, §5 metrics. Branch: `agile/wp-03-observers`.*

**Goal.** Measure before optimising: framework observers wired into
the live pipeline construction, per-stage p50/p95 in the call
record, and fallback/error rates visible per call.

**Starting points.** `build_live_pipeline()` (worker/pipeline
construction, `PipelineParams`), the session-record writer in
`server/live_pipeline.py`, `server/llm_failover.py` (existing
`llm_failover` record block), `tools/muse_readiness_report.py`
(reporting surface).

**Steps.**
1. Verify-in-venv and wire what's present in 1.8.0:
   `UserBotLatencyObserver`, `TurnTrackingObserver`,
   `ServiceMetricsObserver`, `StartupTimingObserver`; enable
   built-in per-service metrics (TTFB).
2. Extend the call record with an `observability` block: per-turn
   user-end→first-audio ms, per-stage TTFB (STT/LLM/TTS), turn
   count, interruption count, fallback events (join with the
   existing `llm_failover` block), repetition-guard and CLOSING
   events (WP-1/WP-2 hooks).
3. A small offline tool (`tools/latency_report.py`) that aggregates
   records from `logs/` into p50/p95 per stage across calls — this
   is the baseline every later package reports against.

**Acceptance.** Bench run produces a record whose observability
block is fully populated; `tools/latency_report.py` renders p50/p95
from ≥3 bench records; no measurable added latency on the bench
script (observers must be passive). Full battery green.

**Risks/unknowns.** Observer APIs moved between Pipecat versions —
if a named observer doesn't exist in 1.8.0, implement the metric
from frames we already tap (BotStartedSpeaking etc.) and mark the
substitution in the report.

### WP-4 — Telnyx code-side wins: AMD, dual-channel recording, second transcript (S; Premium AMD $0.0065/answered call at use time — portal-verified price pending, see Appendix)
*Research recs #8 + #17, §6a/§6c/§6e. Branch: `agile/wp-04-telnyx-amd-record`.*

**Goal.** The carrier stops being a dumb pipe: answering-machine
detection with correct greeting handling, dual-channel recordings
as measurement/eval infrastructure, and Telnyx's own transcription
as an independent second transcript. **Code and offline tests only**
— enabling carrier features on the live Call Control app is a
portal action for Nathan (Appendix); nothing here changes live
behaviour until he flips it.

**Starting points.** `server/telnyx_adapter/wire.py` (`dial_request`
payload), `server/telnyx_adapter/adapter.py`, `server/telnyx_webhook.py`
(event handling), `tools/place_call.py` (flag plumbing),
`server/call_job.py` (job fields).

**Steps.**
1. Add `answering_machine_detection` to the dial payload behind a
   job flag (`--amd premium|standard|off`, default off until Nathan
   enables it in the portal). Handle `call.machine.premium.
   detection.ended` and `call.machine.premium.greeting.ended`
   (incl. `beep_detected`) in the webhook/adapter layer: on
   `machine`, wait for greeting-ended/beep, then either hang up
   (log a voicemail episode to the profile store) or drop a
   **pre-recorded** cached voicemail message — never live-TTS into
   a beep. On `silence`: one scripted "hello?" then hang up.
   Handle the `premium_ios_call_screening_detection` events as a
   documented stub (log + treat as human-pending) — full screened
   flow is future work.
2. Add `record-from-answer`, dual channel, behind a job flag
   (default off; ~$0.002/min when on). Store recording references
   on the call record; recordings are the raw material for WP-12's
   STT benchmark and WP-7's production scoring.
3. Add `transcription_start` behind a job flag; persist the Telnyx
   transcript alongside ours on the record, flagged as the
   independent cross-check source for WP-9 extraction (disagreement
   on names/dates → review queue).
4. Webhook hygiene audit: every outbound command carries a
   `command_id` (idempotent), per-call state rides in
   `client_state`; document (don't configure) the failover webhook
   URL in the Appendix checklist.

**Acceptance.** Unit tests over the fake carrier in
`server/telnyx_adapter` (it has a fixed-payload fake — extend it):
dial payloads byte-checked for each flag combination; scripted
webhook sequences for machine+greeting+beep, silence, and iOS
screening produce the specified actions and episode writes; a
dry-run (`--dry-run`) shows the new flags. Full battery green.

**Risks/unknowns.** AMD/recording prices in the research trace to
secondary sources — the plan treats them as unverified until
Nathan checks the portal (Appendix). Trial-plan limits may gate
some features; code must degrade gracefully if Telnyx rejects a
flag (log, continue the call without it).

### WP-5 — Context hygiene: summarisation, prompt caching, scoped prompts (S, saves LLM cost)
*Research rec #10, §2c, §8. Branch: `agile/wp-05-context-hygiene`.*

**Goal.** Long calls stop paying full-history TTFT every turn.

**Starting points.** Aggregator construction in
`build_live_pipeline()`, `server/call_controller.py` `CallBrief`
(frozen brief = the cacheable prefix), `server/persona.py`
`build_system_prompt`, `server/playbooks.py`.

**Steps.**
1. Verify-in-venv `enable_auto_context_summarization` on the
   assistant aggregator (research: off by default; triggers ~8,000
   est. tokens / 20 messages; can run on a dedicated cheap model —
   use a fast-pool model, never the frontier). Enable with the
   dedicated summariser; preserve system + recent + incomplete
   function-call pairs per framework semantics.
2. Gemini prompt caching for the frozen prefix (persona +
   playbook + brief): cache the stable system content per call;
   measure TTFT delta on the bench harness with a 25+-turn script.
3. Playbook-scoped prompt trimming (the Pipecat Flows *pattern*,
   not the framework): each playbook step exposes only its own
   prompt section + slots; the frozen brief stops carrying sections
   the current step can't use. Keep the controller's ownership of
   state — do not adopt Pipecat Flows itself.

**Acceptance.** Bench: a scripted 30-turn conversation shows
summarisation firing and TTFT (WP-3 metrics) flat-to-better in
late turns vs the WP-3 baseline; no loss of the Oct 30 retest
behaviours (identity answer, hand-back) in scripted evals. Full
battery green.

**Risks/unknowns.** Summarisation must never summarise away an
open commitment or an unanswered caller question — add a test with
a commitment made early and checked after summarisation fires.

### WP-6 — TTS phone-chain tuning + filler/backchannel clip cache (S, one-off TTS chars)
*Research recs #6 + #9, §1d, §7a/§7d. Branch: `agile/wp-06-tts-clips`.*

**Goal.** Everything routine is a clip, and live TTS is tuned for
an 8 kHz phone wire, not a browser demo.

**Starting points.** `CachedElevenLabsTTSService`
(`server/live_pipeline.py` ~761), `server/prewarm_phrases.py`, the
phrase-cache layout under the logs dir, `ElevenLabsTTSService.
Settings` construction (~1196).

**Steps.**
1. Settings pass on the live TTS construction: output format
   `ulaw_8000`, `style=0`, `use_speaker_boost=false`,
   `stability≈0.5` / `similarity_boost≈0.75` as the starting point
   (config-driven, not hardcoded). Verify each setting exists in
   the 1.8.0 ElevenLabs service wrapper; where the wrapper doesn't
   expose one, set it at the API-params level the wrapper allows
   or mark it BLOCKED-BY-VERSION.
2. Spoken-form hygiene: a pre-TTS text pass (small FrameProcessor
   or aggregator-side transform — remember the `super().
   process_frame()` contract) that spells dates/times/currency as
   spoken, strips markdown/lists/URLs, and enforces the
   first-sentence rule (≤ ~8 words) at the prompt level via
   playbook/persona wording.
3. Clip cache growth: pre-synthesise and cache — 3–4 rotating
   latency fillers ("let me check that for you" class), the WP-2
   closing line variants, common confirmations, and the WP-8
   failover filler. Fillers fire only on predicted wait
   (>600–800 ms, e.g. tool calls or a failover switch), never
   unconditionally; track trigger rate in the record (WP-3 block).
4. Backchannel evaluation: verify the Pipecat Backchannel
   extension against 1.8.0 (needs ≥1.6); if present, wire it
   behind a flag with the research's tuning (`cooldown_s=2.5`,
   `fire_probability=0.8`, volume 0.6, `prewarm()` at startup) —
   clips on Smart-Turn-incomplete pauses only, **nothing written
   to LLM context**. If absent in 1.8.0, mark BLOCKED-BY-VERSION
   and implement the minimal local equivalent only if it's small;
   otherwise defer with a note.

**Acceptance.** Bench: cached phrases serve with zero live TTS
characters for the scripted fixed lines (record's TTS accounting
shows it); a forced slow turn triggers exactly one filler; judge
all audio through 8 kHz-downsampled bench output files, never
raw 24 kHz. Full battery green.

**Risks/unknowns.** Premade-voice behaviour on domain entities at
8 kHz is the WP-13 benchmark's job — don't voice-shop here.
Filler over-triggering reads as scripted; the trigger threshold
is a config value with the measured rate reported.


### WP-7 — Eval harness foundations (S–M, local judge = $0)
*Research rec #7 (+ #15 preparation only), §5. Branch: `agile/wp-07-eval-harness`. Sequenced here — before the riskier M packages — because every later package's acceptance criteria lean on it.*

**Goal.** The layered test model, layers 1–2 real and runnable in
the offline battery: scripted scenarios with assertions, simulated
callers with an LLM judge, and a single scorecard. Layer 4 (real
Telnyx calls) is **preparation only** under Ground Rule 1 — write
the scenarios; never dial.

**Starting points.** `tests/test_intelligence_eval.py` (existing
scripted-harness pattern), `tests/test_llm_failover.py` (client-
seam faking precedent), `tools/offline_verify.py`.

**Steps.**
1. Verify whether Pipecat Evals exists in the installed 1.8.0. If
   yes, adopt its scenario format for new scenarios. If no, extend
   our existing harness to the same shape (`turns:` assertions:
   text-contains, tool-call + args, `within_ms` budgets, semantic
   eval) — do not upgrade Pipecat for this.
2. Scenario set v1 (each run ≥3× — agents are non-deterministic;
   a single pass proves nothing): the Oct 30 regression suite —
   repetition loop, identity question mid-call, hand-back on
   decline, double-goodbye, provider-503 mid-call (client-seam
   fake), booking flow with a date correction ("Tuesday — no,
   Thursday"), voicemail/AMD sequence (WP-4 fakes). **Rule:** every
   future production failure graduates into a scenario the same
   day it's diagnosed.
3. Simulated callers: persona + goal + success criteria (rushed,
   confused, rambling, hostile-from-turn-3), judged by a local
   Ollama model if available in the environment, else the frontier
   model at API level in an explicitly-offline tool (not in the
   socket-guarded battery).
4. Scorecard (one page, generated per run): **task completion is
   the gate** (target >85%; <70% is critical) — WER is a diagnostic
   only, with entity error rate (names/dates/times/numbers)
   reported separately; TTFA p50/p95 (target <1.7 s); barge-in
   recovery (>90%); repetition incidents/call; double-goodbye
   count (must be 0); provider fallback rate.

**Acceptance.** The harness runs in the offline battery; the v1
scenario set passes on the current code (any failure is a finding
— report it, don't tune scenarios to pass); scorecard renders
from a bench run. Full battery green.

**Risks/unknowns.** Judge quality: a weak local judge makes layer
2 decorative — validate the judge against 5 hand-scored
transcripts and report agreement before trusting its scores.

### WP-8 — Cross-provider LLM failover + per-turn deadline + circuit breaker (S–M; cost only on hedged/speculated turns)
*Research rec #4, §3a/§3b/§3c. Branch: `agile/wp-08-cross-provider-failover`.*

**Goal.** A Google-wide outage can no longer silence a call. The
existing chain is Gemini-only (fast pool → frontier, all one
provider); add a cross-provider leg, a per-turn deadline, and a
circuit breaker — while keeping the proven pre-first-chunk,
sticky, never-on-400/403 semantics.

**Starting points.** `server/llm_failover.py` (`LLMFailoverMixin`,
`model_chain`), `server/model_select.py`, the composition site in
`build_live_pipeline()` (~1150–1185), `server/provider_config.py`
(env-driven pools — "dynamic always" is a standing decision).

**Steps.**
1. Add a Groq-hosted OpenAI-compatible leg (GPT-OSS / Llama-class;
   we already have a Groq relationship for STT) as the chain's
   cross-provider fallback, ordered by *measured* p95 TTFT from
   this host (probe at bench time; don't trust headline speed).
   Implementation: extend the mixin's chain abstraction so a leg
   can be a different service class (Pipecat's Groq/OpenAI service)
   behind the same response envelope the aggregators already see —
   the aggregators must still observe exactly one clean stream.
   (The research's LiteLLM alternative was considered: it buys
   routing vocabulary at the cost of a second moving part and a
   known silent-misconfiguration failure mode. Extend the tested
   mixin instead; note the decision in the report.)
2. Per-turn deadline: primary attempt gets a TTFT budget
   (~500–700 ms, config); on timeout or failover-worthy error,
   cancel and switch **before first token** — never serial-retry
   an overloaded model in-call. Hedging (parallel fire, first
   token wins) only on turns whose deadline is already blown.
3. Circuit breaker: ≥3 consecutive failover-worthy failures in
   60 s → route around that provider for a cooldown (120 s
   default, config), half-open probe to close. State is
   per-process; log transitions.
4. All-legs-fail path: exactly one scripted apology (cached clip,
   WP-6) + an offer of a callback, recorded as a commitment in the
   profile store (WP-10 consumes it) — never dead air, never an
   improvised answer.
5. Post-call work gets the **opposite** policy in its own helper:
   bounded exponential backoff + jitter (1s→2s→4s, ≤4 tries),
   then next model, then a dead-letter entry — one code path must
   not serve both regimes.
6. Record `served_by` + fallback rate per call (extend the
   existing `llm_failover` record block); WP-3's report surfaces
   a fallback-rate alert threshold (>a few %).

**Acceptance.** Client-seam tests (the WP-precedent seam, now for
both providers): primary 503 → cross-provider answers, sequence
proves stickiness; primary slow (TTFT > budget, no error) →
deadline switch fires; breaker opens after 3 failures and
half-opens after cooldown; 400 → no failover anywhere; all-legs-
fail → apology + commitment, call ends cleanly. Real-provider
bench through the production media handler with the primary
*forced* to a dead model name: turns answered by the Groq leg.
Full battery green.

**Risks/unknowns.** Groq LLM pricing/limits on our existing key
and GPT-OSS tool-calling fidelity at flash-lite prompt sizes —
measure in the bench; if the Groq leg can't hold the persona
prompt, Cerebras is the documented second choice. Cross-provider
legs change voice-of-answers subtly (same system prompt, different
model) — acceptable for outage survival, and `served_by` makes it
auditable.

### WP-9 — LLM post-call extraction + AUDN merge + review queue (M; fractions of a cent/call)
*Research rec #5, §4a/§4d; AUDN from §4a step 5 (retrieval upgraded later by WP-11). Branch: `agile/wp-09-llm-extraction`.*

**Goal.** Replace the deliberately-conservative rule-based typer
with LLM extraction inside a validation cage — this is the single
biggest memory-quality win available. The `ControllerDecider`
seam exists for exactly this (the hosted-Jev stub occupies the
same seat; this package implements the seat locally).

**Starting points.** `server/call_controller.py`:
`ControllerDecider` (~133), `RuleBasedDecider` (~141),
`extract_items()` (~227), `writeback()` (~294);
`server/profile_store.py`: `extract_and_store()` (~276),
`add_fact()` supersession semantics; `docs/PERSONA_MEMORY_SPEC.md`
(the typing contract).

**Steps.**
1. `LLMDecider(ControllerDecider)`: one Pydantic schema per
   playbook objective — `facts[]` (subject, predicate, value,
   valid_from/valid_until, confidence, **evidence_span** = the
   verbatim supporting quote; a fact without a span is rejected),
   `episode_summary`, `commitments[]` (who owes whom what, by
   when), `outcome`. Gemini structured output
   (`responseMimeType: application/json` + schema / the installed
   SDK's `responseFormat`) constrains shape at decode — **and**
   validate client-side anyway (truncations/refusals return 200
   with schema-violating payloads).
2. Validate→repair loop: on validation error, feed the validator
   message back, ≤2 retries, then park the call in a **review
   queue** (new table + a small CLI to list/drain it) — never
   silently drop an extraction.
3. Second pass for recall: an adversarial "what did you miss?"
   sweep over the transcript with pass-1 output in context,
   restricted to items not already listed.
4. AUDN merge: for each candidate fact, retrieve nearest existing
   facts (this package: same entity + predicate + FTS match —
   WP-11 upgrades retrieval to vector/hybrid) and classify with a
   cheap model call: **A**dd / **U**pdate (invalidate the old
   fact's window via the existing supersession path, insert new)
   / **D**elete / **N**oop. Assistant claims are never stored as
   facts (standing rule from the current typer — preserve it).
5. Model routing: extraction runs on a **stronger** model than
   the in-call turns (frontier pool), on the best transcript
   available (ours; the WP-4 Telnyx transcript when present, with
   entity disagreements routed to the review queue). Retry policy
   = WP-8's post-call policy.
6. `RuleBasedDecider` stays as the fallback when the LLM path
   errors end-to-end, and as the offline-battery default (the
   battery is credential-free — LLMDecider tests fake at the
   genai client seam).
7. Golden set (§4d): hand-label 20–30 real/bench transcripts for
   facts + commitments (start from the Oct 30 call records);
   scoring script reports fact recall/precision + commitment
   recall. Every extraction change is scored against it.

**Acceptance.** Golden-set scores in the package report, with the
rule-based typer's scores on the same set as the baseline (the
LLM path must beat it on recall without losing precision);
review-queue round-trip test (bad payload → queued → drained);
AUDN test: a corrected fact ("Tuesday — no, Thursday") invalidates
rather than duplicates; battery green with the LLMDecider covered
at the client seam.

**Risks/unknowns.** Evidence spans make hallucinations auditable,
not impossible — the review queue is a feature, not a failure.
Extraction cost stays fractions of a cent/call at our volume;
report the measured per-call figure from bench runs.

### WP-10 — Commitment ledger: state machine, overdue sweep, next-call surfacing (M, $0)
*Research rec #12, §4c. Branch: `agile/wp-10-commitment-ledger`.*

**Goal.** Commitments stop being diary entries: a real ledger with
due dates, an overdue sweep, and fulfilment verified against
system evidence — surfaced at the top of the next call to that
counterparty.

**Starting points.** `server/profile_store.py` commitments
(`add_commitment` ~231, `set_commitment_state` ~249,
`open_commitments` ~260 — the state field exists; the lifecycle
does not), `build_brief()` (already priority-orders open
commitments first in the slice — extend, don't rebuild),
`playbooks/*.json`.

**Steps.**
1. Formalise the lifecycle: `OPEN → DUE → FULFILLED | VIOLATED |
   EXPIRED | CANCELLED`, with `owner` (us / counterparty),
   `due_at`, `source_episode_id`, `fulfilment_evidence`. Migrate
   existing rows (the live DB has seeded + written-back
   commitments — migration runs against a **copy** in tests; the
   live DB is touched by nobody under Ground Rule 7 — ship the
   migration as a tool Nathan runs, or flag it).
2. Due-date hardening: the current typer's relative-date capture
   ("by Friday") resolves against the episode date; WP-9's
   extractor emits structured due values — support both.
3. `tools/commitment_sweep.py`: lists OPEN commitments past due
   (runnable on boot / nightly by cron later — wiring the cron is
   Nathan's call, ship the tool), and marks DUE transitions.
4. Next-call surfacing: overdue commitments lead the brief's
   commitment section, and playbooks gain an opener rule —
   acknowledge an overdue commitment before new business.
5. Fulfilment verification: a commitment is FULFILLED only with
   evidence (a tool/system record reference — e.g. a booking ID,
   a sent-confirmation record), never on a transcript claim alone;
   transcript claims set a `claimed` flag that the sweep reports.

**Acceptance.** Lifecycle transition tests incl. illegal
transitions rejected; sweep over a seeded overdue set produces
the operator list; a two-call bench (call 1 creates a commitment
with a due date, call 2's captured system prompt leads with it);
migration dry-run against a copy of the live profile DB.
Full battery green.

**Risks/unknowns.** Over-acknowledging stale commitments is a
real-world annoyance — EXPIRED needs a sensible default horizon
(config, report the chosen value).

### WP-11 — sqlite-vec semantic layer: dedup, semantic repetition, hybrid recall (M, $0 with local embeddings)
*Research rec #11, §4b. Branch: `agile/wp-11-sqlite-vec`.*

**Goal.** Meaning-level memory inside the same SQLite file: no
new service, no graph database. Three uses — extraction dedup,
in-call semantic repetition detection, hybrid recall for the
pre-call brief.

**Starting points.** `server/profile_store.py` (same DB file —
`~/workspace/ai-calling/logs/profile.db` in production; tests use
temp DBs), `server/conversation_state.py` (`ConversationTracker`
token-similarity guard — this package adds the semantic signal
beside it, it does not rip it out), `get_profile_slice()` (~333),
WP-9's AUDN retrieval step.

**Steps.**
1. Add `sqlite-vec` to the store's DB (verify the extension loads
   in the reference venv; it's a loadable extension, not a
   service). Embeddings: local `bge-small-en`-class model on CPU
   (~50 ms/text, self-hosted, $0) — evaluate load weight in the
   venv; Gemini embeddings are the zero-ops fallback (note the
   egress trade-off in the report; Nathan's build is self-hosted
   by principle, so local is the default unless it's impractical).
2. Embed stored facts + candidate facts: cosine ≳0.85 to an
   existing fact routes the candidate to WP-9's AUDN judge
   instead of a blind insert. Embeddings are the bulk filter
   only — the LLM judge decides ("loves X" vs "hates X" embed
   near-identically).
3. In-call repetition: embed each agent utterance; cosine ≳0.85
   against any of the last ~6 agent utterances (and not a
   deliberate read-back/confirmation — those are exempt, marked
   by the controller) flags the tracker: force playbook advance,
   rephrase, or escalate per the existing guard's policy. Keep it
   off the audio path (Ground Rule 9): embeddings of *agent*
   utterances can compute while TTS plays.
4. Hybrid recall in `get_profile_slice`: FTS5 (exact
   names/numbers) + vector (paraphrased needs), fused, within the
   existing budget-capped slice contract.

**Acceptance.** Paraphrase test set: the Oct 30 repeated-question
transcript's *loose* rephrasings (the ones the token guard
misses — they're enumerated in PROJECT_STATE's known-limits)
now flag; deliberate read-backs don't; dedup test files a
paraphrase of an existing fact as one AUDN candidate, not a
second fact; slice-quality spot checks on seeded profiles.
Latency: per-utterance embedding time measured and reported.
Full battery green.

**Risks/unknowns.** Threshold 0.85 is the research's starting
point, not gospel — report the precision/recall of the flag on
the test set at 0.80/0.85/0.90 and ship the best. If the local
embedding model's dependency weight is unreasonable in this
venv, say so and take the Gemini-embeddings branch with the
egress note flagged for Nathan.


### WP-12 — EXPERIMENT: streaming STT benchmark (Deepgram Nova-3 / Flux) vs Groq Whisper (M; STT $0.0007 → ~$0.005/min if adopted)
*Research rec #13 (+ #14 analysis), §2a/§2b. Branch: `agile/wp-12-stt-benchmark`. **Decision gate: this package produces a recommendation, not a swap.***

**Goal.** Groq Whisper is batch — no partials, no eager
end-of-turn — and is the architecture's latency bottleneck.
Benchmark streaming STT on **our own recorded calls** and let
entity accuracy + latency, not vendor WER claims, decide.

**Steps.**
1. Corpus: 30–50 recorded calls. Source material is the bench
   harness's recorded sessions plus — once WP-4 lands and Nathan
   enables it — dual-channel production recordings. If the corpus
   is short, say so and benchmark on what's real; never synthesise
   a corpus and report it as field data.
2. Candidates: Deepgram Nova-3 streaming (interim + final,
   keyword/entity boosting for names/dates/our domain terms) and
   Deepgram Flux (turn detection in-model: `eot_threshold`,
   `eager_eot_threshold`). The `deepgram` SDK is **not** in the
   candidate venv today (the reference pack's `server/server.py`
   imports Pipecat's Deepgram service, but that's the frozen
   reference implementation, not the live env) — add the
   dependency in this branch only, behind the experiment flag.
   Verify how Pipecat 1.8.0's Deepgram service surfaces Flux /
   eager-EOT events; if eager EOT isn't wired in 1.8.0, benchmark
   Nova-3 streaming and mark the Flux path BLOCKED-BY-VERSION.
3. Score per candidate vs the Groq baseline: **entity error rate**
   (names, dates, times, numbers — reported separately from WER),
   headline WER (diagnostic), end-of-speech → final-transcript
   latency p50/p95, interim availability (which unblocks WP-1's
   `use_interim` and any future speculation), and cost/min.
4. Keep Groq Whisper in the design regardless: it's the offline
   transcriber for recordings/extraction cross-checks, where
   batch is a virtue.

**Acceptance / gate.** A benchmark report in `logs/offline-qa/`
with the table above and a clear adopt / don't-adopt
recommendation. **No production swap in this package** — adoption
is a separate, small follow-up package after Nathan's decision.
Known blocker, stated plainly: speculative generation
(`EagerUserTurnStopStrategy` + SpeculationGate) needs Pipecat
≥1.9 per the research and is **out of scope on 1.8.0**; if the
benchmark makes speculation the decisive argument, the report
says so and the Pipecat upgrade becomes its own decision for
Nathan (a version bump on a live phone system is never a
drive-by).

### WP-13 — EXPERIMENT: TTS benchmark through the phone chain (M; candidates at similar or lower per-char cost)
*Research rec #16, §7b/§7c. Branch: `agile/wp-13-tts-benchmark`. **Decision gate: recommendation, not a swap.***

**Goal.** Judge voices the way callees hear them: through 8 kHz.

**Steps.**
1. Candidates: incumbent ElevenLabs Flash v2.5 (baseline, with
   WP-6's tuned settings), ElevenLabs v4 Turbo (one A/B slot),
   Cartesia Sonic-3, Deepgram Aura-2. Prices in the research
   ($15–40 /M chars, trackers disagree) are unverified — record
   the portal price at benchmark time or mark it unverified.
2. Method: one entity-heavy script set (names, dates, times,
   currency, our domain terms) + the standard bench conversation;
   render through each candidate, downsample to `ulaw_8000`,
   and produce blind listening files (labels hidden until
   scored). Score: p50/p95 time-to-first-audio, blind quality
   ranking on the 8 kHz renders, entity mangling count, and
   cost/call at our measured agent-talk ratio (~360 agent
   chars/min ⇒ TTS is the dominant AI cost line — verify against
   real call records, WP-3 data).
3. Integration spikes stay spikes: a candidate that needs a new
   service class gets a throwaway bench wiring behind a flag,
   never a pipeline recomposition.

**Acceptance / gate.** Benchmark report with the table + a
recommendation; no voice/model change ships from this package.
If a challenger wins on quality *and* latency, the follow-up
swap package is small because WP-6 made settings config-driven.

---

## 3. Deferred — explicitly not now (do not start these)

| Item | Why not |
|---|---|
| **Graphiti / Zep memory migration** | Our SQLite store already implements Graphiti's core idea (validity windows, invalidate-don't-overwrite, episodes first-class). Graphiti adds a graph DB (Neo4j/FalkorDB) to operate + a second extraction system, for multi-hop questions this deployment doesn't ask yet. Revisit triggers: >~10–50k facts, genuine multi-entity questions, multiple agents sharing memory. WP-9/WP-11 steal the patterns instead. Zep is hosted-only — against the self-hosted premise. |
| **Letta adoption** | It's an agent framework/OS, not a memory store; adopting it replaces the controller + Pipecat architecture. Out of scope by definition. |
| **Hosted Jev (TypeSafe) for the typing seat** | `HostedJevDecider` stays an inert stub. Switching requires Nathan personally creating the account + API key (Secure Vault flow) and explicitly approving profile/transcript egress off his machine. WP-9 implements the same seat locally. Never sign Nathan up for anything. |
| **mem0 adoption** | The AUDN merge pattern is copied in WP-9 without the dependency; mem0's known weakness (no native temporal reasoning) is our store's strength. Pipecat's `Mem0MemoryService` is the sanctioned trial path if Nathan ever wants an A/B — his call. |
| **SIP / LiveKit transport migration** | PSTN calls are 8 kHz G.711 — there is no wideband to recover, and the bottleneck is STT/turn-taking, not the WebSocket. Revisit only if warm transfers / multi-party become requirements, or turn-gap data shows WebSocket loss. |
| **Pipecat ≥1.9 upgrade** (for EagerUserTurnStopStrategy + SpeculationGate) | A version bump on a live phone system is its own reviewed project with a full battery + controlled retest. WP-12's report may recommend it; nobody performs it under this plan. |
| **Gemini Live / speech-to-speech rewrite** | Throws away a working, observable cascade for an opaque one. Not while the cascade's measured problems are specific and fixable (WP-1–WP-13). |
| **Candidate → `main` merge** | Separate future project (full battery + a fresh controlled call before deploying from a merge). `main` stays untouched. |
| **Live telephony test calls (research layer 4)** | Ground Rule 1. WP-7 prepares the scenarios; dialling them needs Nathan's fresh per-call approvals, outside this plan. |

## 4. Definition of done — the whole program

The program is done when, with evidence in `logs/offline-qa/` and
package reports on file:

1. WP-1–WP-11 are merged-ready on their branches (WP-12/WP-13 end
   in benchmark reports + recommendations — their follow-up swaps
   are separate decisions), each with the full battery green:
   candidate suite, reference pack 57/57, mutations 3/3, manifest
   verified — counts stated, never rounded.
2. The bench scorecard (WP-7) shows, against the WP-3 baseline:
   task completion >85% on the scenario set, double-goodbye count
   0, repetition incidents measurably down (WP-11 semantic flags
   included), TTFA p95 <1.7 s on the bench script, and a forced-
   outage bench (primary model dead) answered end-to-end by the
   cross-provider leg.
3. Memory round-trip proven on bench: call 1 writes back facts +
   a commitment via WP-9 extraction; call 2 to the same
   counterparty opens with the commitment surfaced (WP-10) and
   uses a call-1 fact; a corrected fact supersedes instead of
   duplicating (AUDN).
4. Nothing has been deployed, dialled, or merged. A closing
   summary lists every branch + HEAD, and the deploy decision —
   package by package or all at once — is Nathan's, taken with
   the reports in hand.

**Per-package report format** (mirror what Nathan already
accepts): (1) what changed, in plain mechanics; (2) the evidence
— suite counts, bench transcripts/metrics, the exact tests that
would have caught the old failure; (3) commits + branch HEAD;
(4) anything cut, deferred, or BLOCKED-BY-VERSION, with reasons;
(5) cost deltas actually measured, not list prices; (6) the next
package, or a flag where a decision belongs to Nathan.

## Appendix — Portal checklist (Nathan's hands only; A-GILE: do not attempt)

These are Telnyx-portal actions no agent performs. The code in
WP-4 is inert until they're done:

- [ ] Verify Premium AMD pricing in the portal (research figure
      $0.0065/answered call is secondary-sourced) and Standard
      ($0.002) — then set the default AMD tier.
- [ ] Configure the Call Control app's **failover webhook URL**
      (WP-4 covers the code side: `command_id` idempotency,
      `client_state`).
- [ ] Decide on recording consent posture before enabling WP-4's
      recording flag on real calls (jurisdiction-dependent; the
      research explicitly did not cover the law).
- [ ] Paid Telnyx plan, when ready: removes the trial's
      one-verified-number outbound limit and the webhook-resend
      paywall — the unlock for calling anyone other than Nathan.
- [ ] Stranded Telnyx API key (…AC719…, still Active from Oct 29):
      revoke or keep — Nathan's call, noted here so it isn't lost.
- [ ] Profile-DB migrations shipped by WP-10 run against the live
      `profile.db` only on Nathan's word (backups in
      `~/ai-calling-backups/` first).
