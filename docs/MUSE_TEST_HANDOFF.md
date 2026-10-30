# Muse handoff — import, verify, test once, return evidence

**Release status:** offline-validated candidate; no live carrier/provider certification.  
**Do not use the historical `tools/deploy_linux.sh` for this candidate.** It moves virtualenvs and does not provide a complete code/config rollback. Use the prepare-only path below and a reviewed service override.

## 1. What is in the delivery

- `ai-calling-candidate.bundle`: local Git history with branch `debug/offline-audit`; suitable for a later merge.
- `candidate.patch`: binary-capable diff from original GitHub commit `03d21312caf16bda13d57bfeadf55635c50e2d8b`.
- `candidate-source.zip`: complete candidate source, without credentials or virtualenv.
- `MANIFEST.json` and `SHA256SUMS`: actual candidate revision and artifact integrity.
- Audit, backlog, research traceability, source-comparison receipts, passing/failing test evidence and mutation receipts.

The bundle is the preferred import route; do not apply both the bundle and patch. Review the diff before merging. No remote GitHub update has been made.

## 2. Import to an isolated worktree

Assumptions: Muse's existing repository is `/home/hatch/workspace/ai-calling`; delivery is extracted under `$HOME/Downloads/ai-calling-muse-candidate`. Set `AI_CALLING_PROJECT` or `DOWNLOADS` if your locations differ.

```bash
REPO="${AI_CALLING_PROJECT:-/home/hatch/workspace/ai-calling}"
DOWNLOADS="${DOWNLOADS:-$HOME/Downloads}"
BUNDLE="$DOWNLOADS/ai-calling-muse-candidate/ai-calling-candidate.bundle"
test -f "$BUNDLE"
cd "$REPO"
git status --short
git bundle verify "$BUNDLE"
git fetch "$BUNDLE" debug/offline-audit:review/ai-calling-candidate
git diff --stat 03d21312caf16bda13d57bfeadf55635c50e2d8b review/ai-calling-candidate
git worktree add "$HOME/ai-calling-candidate" review/ai-calling-candidate
```

Stop if the review branch/worktree already exists; inspect it instead of forcing or deleting it. Preserve any current Muse changes. If Muse already contains ZIP changes, the bundle shares the original GitHub ancestor so a later normal merge can expose conflicts rather than overwrite them.

Do not run the service or a dialer from an unreviewed merge. The two dialers now import the server code relative to their own checkout so a candidate worktree does not silently use the old production adapter. They still retain historical destination/credential-file defaults: review those before use.

## 3. Prepare without deploying

```bash
cd "$HOME/ai-calling-candidate"
VENV="$HOME/.venvs/ai-calling-candidate-$(git rev-parse --short HEAD)"
python3.12 tools/prepare_muse_candidate.py --venv "$VENV" --install-approved
```

This downloads hash-locked dependencies, checks them and runs the offline application suite. It **does not** resolve production provider credentials, prewarm speech, restart services or dial. It refuses an existing venv destination. Keep the new venv at this path; do not rename or move it.

Expected: **173/173 application tests**, zero failures/errors/skips and zero external connection attempts. Seven loopback connections are expected from the local HTTP tests. The tool writes `candidate-qa.json`, `candidate-preparation.json` and a **review-only** `candidate-systemd.override.conf` in the venv.

Run the reference suite separately with a fresh output path, because the supplied reference runner intentionally refuses to overwrite receipts:

```bash
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p logs/offline-qa
"$VENV/bin/python" test-pack-v1/Muse_Telnyx_Integration_Test_Pack/run_tests.py --output "logs/offline-qa/reference-$RUN_ID.json"
"$VENV/bin/python" test-pack-v1/Muse_Telnyx_Integration_Test_Pack/run_mutations.py --output "logs/offline-qa/reference-mutations-$RUN_ID.json"
"$VENV/bin/python" tools/verify_reference_manifest.py test-pack-v1/Muse_Telnyx_Integration_Test_Pack
```

Expected: 57 reference tests; all three reference mutants caught; 28 reference files byte-identical to their manifest. This proves internal integrity, not independent authenticity of the original reference pack.

## 4. Inspect state and preserve rollback

Use the **existing shared call database**, not a fresh empty one. The default-one concurrency gate deliberately blocks new calls when any previous attempt is `submit_unknown`, `initiated` or `answered`.

```bash
DB="${TELNYX_WEBHOOK_DB:-/home/hatch/workspace/ai-calling/logs/webhook.db}"
"$VENV/bin/python" tools/muse_readiness_report.py --db "$DB" > logs/offline-qa/muse-readiness.json
```

The report is read-only. It emits package versions, configuration-presence booleans and safe state counts/attempt identifiers—not provider secrets, destinations or transcripts. Resolve old attempts through carrier/signed evidence. Never change a state to finished merely to open the concurrency gate.

Before service changes, preserve the prior commit, source changes, actual venv path, systemd unit/drop-ins and private config in an operator-only backup. Use a consistent SQLite backup while handling WAL correctly; do not copy only the main database file from an actively written WAL database. For example, after choosing a private backup directory:

```bash
BACKUP_DIR="$HOME/ai-calling-backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
"$VENV/bin/python" -c 'import sqlite3,sys; source=sqlite3.connect(sys.argv[1]); target=sqlite3.connect(sys.argv[2]); source.backup(target); target.close(); source.close()' "$DB" "$BACKUP_DIR/webhook.db"
```

This is an example local backup command, not a command executed by the audit. Retain original credential provisioning; do not paste private files or raw `systemctl show` environment output into chat.

## 5. Review configuration and the service override

The generated override changes code/venv paths and resets `ExecStartPre`/`ExecStart`; it does not install or activate itself. Review it with the current unit, keeping the real log/database/cache directories and credential environment files.

Required checks:

- `TELNYX_WEBHOOK_PUBLIC_KEY` is the correct public verification key.
- `AI_CALLING_PUBLIC_HOST` is the exact public authority used in the dialer's `wss://.../telnyx/media/...` URL. Scheme, host, optional port, path and query form the signed value. Do not infer it from untrusted forwarded headers.
- The proxy forwards `telnyx-signature-ed25519` and `telnyx-timestamp` on the WebSocket upgrade. There is no unsigned debug bypass.
- `TELNYX_WEBHOOK_DB`, `AI_CALLING_LOG_DIR` and `AI_CALLING_PHRASE_CACHE` point to the shared production state/cache locations, not a new candidate-local database.
- `GEMINI_FAST_MODELS`, `GEMINI_FRONTIER_MODELS`, `GROQ_STT_MODELS`, `ELEVENLABS_TTS_MODELS`, voice and sample rate are intentional and consistent. New preflight receipts include a configuration fingerprint; old-format receipts will not pass.
- Numeric quota limits and `AI_CALLING_TTS_CHAR_LIMIT` are present and intentional. Inspect `/readyz` failures rather than removing its gates.
- `NLTK_DATA` points to the candidate's bundled data. Readiness requires the English data.
- The old immediate-transfer line has changed to: **“Nathan will need to take this over. I'll end the call now.”** Prewarm the new phrase set. There is still no real bridge/notification.
- Defaults: handshake 5 seconds, startup 15 seconds, first outbound audio 8 seconds, silence 12 seconds. Timeout overrides must be finite and positive.
- `AI_CALLING_ALLOW_CLOSE_HEADER_WORKAROUND` is **off by default**. Enable `1` only if the known Muse proxy defect is actually observed: HTTP 101 with `Upgrade: websocket` but `Connection: close`. Prefer correcting the proxy. Other handshake checks and TLS validation are not bypassed.
- Keep the service's log directory access-controlled. Application logging disables diagnostic locals, but debug logs/transcripts can still contain personal data.

The candidate unit supports an optional private `.runtime-config` environment file. A pre-existing installed unit will not gain that line merely because the Git source changed; review the installed unit/drop-ins explicitly. Keep the private file out of Git.

## 6. Activate only after operator review

Do not restart while a call is active. Installing a reviewed override and restarting **will invoke provider preflight and phrase prewarming** through `ExecStartPre`; these use real provider credentials and may incur TTS usage even though no phone call is placed.

Example for the explicitly reviewed, prepared candidate:

```bash
sudo install -d -m 0755 /etc/systemd/system/ai-calling-live.service.d
sudo install -m 0644 "$VENV/candidate-systemd.override.conf" /etc/systemd/system/ai-calling-live.service.d/90-audit-candidate.conf
sudo systemctl daemon-reload
sudo systemctl restart ai-calling-live.service
curl --fail --silent http://127.0.0.1:8001/healthz
curl --fail --silent http://127.0.0.1:8001/readyz
```

The override inherits other service settings; it is not a complete replacement for credential, proxy, persistent-state or hardening configuration. Verify the actual service user can read the candidate/venv paths. Do not change to a new DB to make readiness appear green.

A readiness receipt lasts one hour. Refresh it through the approved provider-preflight path before a test if stale. Permanent scheduled refresh is backlog work, not silently implemented here.

## 7. Controlled live acceptance — one call, no automatic retry

**Before dialing:** verify the historical dialer's exact fixed destination is the currently consenting test recipient; obtain fresh approval; confirm recipient consent, permitted calling time and recording/transcription rules; verify there are no unresolved/active calls and that the same host/database is used by the dialer and service.

Do not permanently export the dial-approval flag. Do not let a task runner re-run a dial command automatically. A timeout after submission is an unknown result until carrier evidence resolves it.

### First test: short, quiet, explicitly consenting recipient

1. Observe the actual opening first, complete and intelligible.
2. Speak one ordinary short sentence; verify one timely reply, not doubled.
3. Check that the last words are not clipped or replayed.
4. Ask the assistant to stop/end the call. It should end, not start a fresh inference.
5. Independently confirm signed `call.hangup` and reconcile the carrier call. HTTP 200 to a hangup command is only command acknowledgment.
6. Verify a final JSONL record, `cleanup_pending_tasks = 0`, positive inbound/outbound counters and no unexpected second call.

Only after that succeeds, separately test barge-in, silence/reprompt, a longer response, model escalation, remote hangup, provider failure, and service restart. Each gets its own approval/attempt; do not combine all failure modes in the first test.

### Stage evidence to look for

- Authenticated WebSocket upgrade and one registered/claimed attempt.
- `media-stage pipeline_started`.
- `media-stage disclosure_queued`.
- `media-stage first_outbound_payload`.
- Incoming media bytes/audio frames, VAD and actual transcription.
- One guarded reply followed by output; model IDs/config fingerprint as expected.
- Terminal reason, canonical hangup evidence, signed carrier terminal event and final record.

A connected socket, `streaming.start` HTTP 200 or local outbound bytes alone is not a pass.

## 8. Return this for the next debugging round

Run the read-only report with the actual per-attempt record selected by its registration/log path:

```bash
"$VENV/bin/python" tools/muse_readiness_report.py --db "$DB" > logs/offline-qa/muse-after-test.json
```

The optional `--record` argument adds only a whitelisted summary of the last JSONL record. Return:

- Candidate/merge revision and `candidate-qa.json`.
- Safe readiness/after-test report.
- Attempt identifier and a short timestamped stage timeline.
- What the listener actually heard, whether replies/tails were correct, and approximate turn delay.
- Whether the signed hangup and carrier terminal state arrived.
- Sanitized exception type/stage if anything failed.

Do **not** send API keys, surrogates, private environment files, raw credential-bearing URLs, full database dumps or unconsented audio. If a transcript is essential, provide the minimum approved/redacted excerpt.

## 9. Roll back and stop conditions

Stop testing on missing/invalid signature, wrong destination, absent consent, duplicate call, unknown previous call, unexplained spend, missing final record, nonzero pending cleanup, or failed readiness. Preserve evidence before editing again.

To roll back, first terminate/reconcile any current call, then restore the prior reviewed unit/drop-ins/code/venv paths, run daemon-reload and restart. Remove only the candidate override you installed—not unrelated drop-ins. Preserve the current shared database and signed-event history; do not roll the database backward to erase new calls or reservations. Use the backup for recovery under a deliberate state-reconciliation plan, not as a routine code rollback.

## 10. Later merge

Once review and controlled tests pass, merge the candidate branch into your intended working branch using your normal review process. Resolve conflicts explicitly if Muse has newer local work. Run the complete offline gates again on the merged result before pushing. The archive's manifest records the audited candidate; a different merge commit needs its own evidence.
