# AI Calling — WP-2 recovery checkpoint

**Status: PARTIAL RECOVERY. NOT A RELEASE CANDIDATE.**

## What happened

The execution sandbox reset before the newer WP-2 runtime working tree was committed or packaged. The earlier downloadable checkpoints survived; the temporary checkout, installed environment, uncommitted runtime module, and detailed final test files did not.

The previous conversation recorded a successful 308-test run. That is a historical result for the lost working tree—not fresh validation of the restored code. It must not be used to certify a rebuilt implementation.

The checkpoint should have been saved immediately after that pass. This recovery adds a tool that captures both committed history and current working files, including explicitly selected untracked source files, before a completion claim.

## Recovered, verified baseline

- Branch: `agile/wp-02-closing-state`.
- Commit: `f11207098baa6dd9429f8675068a9ec0d607fc32`.
- Original checkpoint: `ai-calling-wp02-f112070-checkpoint.zip`.
- Original archive SHA-256: `a9c7f8f177c6c6ee79c963a9e57fc8df0b167e10fd0d21004794acd51317cd44`.
- The 36 original archive-manifest entries were checked before restoration.
- The restored source is stored outside temporary storage at `/workspace/tool_results/ai-calling-wp02`.
- Tests execute from a separate disposable clone at `/tmp/ai-calling-wp02-exec`.
- The exact hashed dependency lock was reinstalled; `pip check` passes.

### Fresh verification

| Check | Result | Applies to |
|---|---:|---|
| Application suite | **285/285**, no failures, errors, or skips | Restored controller checkpoint |
| Reference suite | **57/57** | Restored reference package |
| Reference negative controls | **3/3 caught** | Restored reference package |
| Closing-controller negative controls | **6/6 caught** | Restored pure controller |
| Reference integrity | **28 files passed** | Restored reference package |
| Application socket guard | **7 loopback connections; 0 external attempts** | Fresh application run |
| New checkpoint utility | **9/9 tests passed** | Standalone backup tool, not the application |

The restored baseline contains the pure closing state machine, native API contracts, and the old double-goodbye reproduction. It does **not** contain complete live-pipeline closing integration.

## Eight later files recovered exactly

Complete edits and complete source blocks that remained available were recovered separately, then matched to their previously recorded SHA-256 hashes. No missing source block was filled in from a summary.

| Recovered file | Recovery status |
|---|---|
| `server/live_pipeline.py` | Exact prior hash; 89,908 bytes |
| `server/live_server.py` | Exact prior hash |
| `server/closing_state.py` | Exact prior hash |
| `server/prewarm_phrases.py` | Exact prior hash |
| `tests/test_closing_runtime.py` | Exact prior hash; 12 unit tests retained as source |
| `tools/offline_verify.py` | Exact prior hash |
| `tools/generate_turn_fixtures.py` | Exact prior hash |
| `tools/verify_closing_runtime_mutations.py` | Exact prior hash |

The archive keeps these files under **`recovered-source/`**, separate from the runnable baseline. The complete hash ledger accompanies them.

**Do not overlay these files onto the baseline yet.** They reference a missing runtime module and would make the application incomplete.

## Still missing

1. **`server/closing_runtime.py`** — the key runtime module connecting the pure state machine to frame guards, cached audio, native idle events, interruption acknowledgement, and one-time hangup dispatch.
2. **`tests/test_closing_media.py`** — the full integration-test file, including its session helper and eleven final media scenarios.
3. Generated new speech-fixture bytes/manifest and detailed final runtime receipts. The generator source was recovered, but regenerated files require new hashes and fresh validation.
4. Some final documentation/inventory edits, which can be produced after the rebuilt candidate passes.

The previous runtime module fingerprint was `313011621b73fc62ea2b13e6a6565a4106fe728b978453d5202136511ceb467f`. This fingerprint identifies what is missing; it does not supply its contents.

## Next implementation step

Develop a new runtime adapter and integration-test helper against:

- The controlling `docs/AGILE_IMPROVEMENT_PLAN.md`, WP-2.
- The exact recovered pipeline interfaces and runtime-unit tests.
- The installed Pipecat **1.8.0** source, not newer documentation alone.
- The original synthetic client-boundary harness from the verified baseline.

The runtime must retain:

- Opt-in `AI_CALLING_CLOSING_STACK=controller`; legacy remains rollback.
- One cached closing line per closing generation; no fresh LLM or TTS request for that line.
- One genuine reopening, with the next closing final; hard limits and hand-back cannot be reopened.
- A real interruption-processing acknowledgement before starting replacement audio.
- Matching playback generation, start/stop events, and duration checks; a clear is not successful playback.
- Native idle handling with the Pipecat 1.8.0 empty-turn rearm gap handled explicitly.
- No duplicate hangup request on normal completion, overlapping triggers, errors, or unexpected runner exit.
- Cached lines excluded from LLM request accounting.
- Existing honesty, refusal, authorization, budget, and call-limit controls.

Recreate and rerun the real-service scenarios: double goodbye, one-time reopening, objective completion, verbatim hand-back, missing cache, silence, empty transcript, duration limit, overlapping duration/cost triggers, stale in-flight model output, and unexpected pipeline end. Provider clients may be doubled; entire STT/LLM/TTS services must not be replaced.

**Do not target a test count as the objective.** The new implementation needs evidence for every acceptance behavior. A new pass belongs to the new source snapshot, even if the count differs from the historical 308.

WP-3 remains queued because its observability requirements consume WP-1/WP-2 events. No WP-3 runtime changes were started during recovery.

## Restart-safe checkpoint procedure

The included `checkpoint_tools/checkpoint_worktree.py` creates a local archive containing:

- A clonable Git bundle for the current branch.
- Exact current working-file bytes, not just the latest commit.
- Explicitly included untracked files and a record of deleted tracked files.
- Git status and a SHA-256 manifest.

It refuses unreviewed untracked files, obvious credential paths/patterns, symlinks, oversized snapshots, and overwriting an existing archive. It detects source changes during capture and verifies the archive before publishing it. It does not commit, merge, push, deploy, run calls, or certify application tests.

```bash
python checkpoint_tools/checkpoint_worktree.py \
  --repo /path/to/working-repo \
  --include server/new_runtime_module.py \
  --include tests/new_runtime_tests.py \
  --output /persistent/location/checkpoint-unique-name.zip
```

List every new, non-ignored file with `--include`; omit those arguments when the tree has no untracked files. Inspect the manifest and keep test receipts bound to the captured source. Save a snapshot after meaningful edits and immediately after verification—before moving to another task or reporting completion.

Limits: the tool does not recreate Git index staging, permission changes, ignored files, hooks, repository configuration, or stashes. Its credential scan is a bounded pattern check, not exhaustive data-loss prevention. Backups do not replace code review.

This separation matters because a Git bundle preserves refs and committed objects, not the uncommitted working tree. Likewise, `git archive HEAD` archives the named committed tree rather than all local edits. References: [Git bundle](https://git-scm.com/docs/git-bundle), [Git archive](https://git-scm.com/docs/git-archive), [Git status](https://git-scm.com/docs/git-status).

## Safety and delivery state

No phone calls, provider API requests, deployments, pushes, or merges were performed. WP-1's saved checkpoint is intact. The runnable WP-2 baseline remains at `f112070`; the recovered fragments are deliberately not installed into it. The recovery archive is an engineering handoff—not a deployable closing-runtime release.
