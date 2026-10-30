# AI Calling Project — State at End of Oct 30, 2026

**Supersedes** `PROJECT_STATE_2026-10-29.md` for current state; that
file remains the pre-audit history. Live branch:
`review/ai-calling-candidate` (this worktree,
`~/ai-calling-candidate`), deployed as systemd units
`ai-calling-live` + `ai-calling-pinggy`.

## Where it stands

The system **works end to end and has proven it live**: the retest
call on Oct 30, 2026 (~3:47pm EDT) completed the first full two-way
conversation — 30 transcript turns, ~1.88 minutes, est. 4.69¢,
attempt terminal state `hangup`, zero unresolved attempts afterwards.
Groq Whisper transcribed the callee, Gemini answered in context
across turns (14 settled requests), ElevenLabs synthesized replies
live (543 settled characters; the opening remains cache-served). The
honest-AI identity answer and the corrected loop-in line
("Nathan will need to take this over. I'll end the call now.") both
fired verbatim under live questioning, including a profanity
stress test.

## The day's chain (each step evidenced in logs/offline-qa/)

1. **Bench diagnosis** of the five silent calls: four stacked
   defects (RC1 silence_policy NameError; RC2 cached-phrase TTS
   audio context; RC3 disclosure start-order race; RC4 five custom
   FrameProcessors missing `super().process_frame()`). Fixed on the
   production branch (`main` @ 5df6523).
2. **Offline audit candidate** (branch `debug/offline-audit`,
   commit b849e45; 31 findings in `docs/OFFLINE_AUDIT.md`) verified
   in isolation: 173/173 suite, synthetic session reproduced,
   status PREPARED_NOT_DEPLOYED.
3. **Database blockers cleared**: five media jobs settled at zero
   actuals; six attempts closed through a new operator-attested
   resolution mechanism (audit table `attempt_resolutions`; bases
   `carrier_refused`, `not_a_carrier_attempt`,
   `carrier_record_verified` — call #4 closed to `hangup` on Telnyx
   CDR evidence). Final: hangup 14, closed_unsubmitted 5, zero
   unresolved.
4. **Activation** with four approved corrections over the audit
   handoff: shared-DB pin in the drop-in (the candidate would
   otherwise open a fresh local DB), fast-pool order
   `gemini-3.1-flash-lite,gemini-3.5-flash-lite`,
   `AI_CALLING_ALLOW_CLOSE_HEADER_WORKAROUND=1`, and a full unit
   install (a VM replacement had wiped `/etc/systemd/system`).
5. **Test call 1**: signed media upgrade accepted (Pinggy forwards
   both Telnyx signature headers on upgrades — the applied hangup
   webhook carries them), opening heard, but all three Groq STT
   requests died with the SDK's opaque "Connection error."
6. **Probe-isolated root cause — two stacked egress-proxy defects**:
   httpx cannot parse the bare `::1` in `NO_PROXY`
   (`InvalidURL: Invalid port: ':1]'`), and certifi lacks the proxy
   CA (`CERTIFICATE_VERIFY_FAILED`). Preflight (aiohttp) had passed
   throughout, which is why readiness stayed green. Fix: an
   env file read last in the unit's EnvironmentFile chain
   (`systemd/service-env-fix.env`) — systemd reads env files at
   spawn, overriding `Environment=` lines, which is why the first
   fix attempt only half-took.
7. **Budget cap trip**: the retest first refused in `prepare()` —
   20 October jobs × 100¢ gross reservations = the $20 monthly cap,
   reservations never released at settlement (recorded spend $0).
   One-time operator-approved manual release (backup
   `~/ai-calling-backups/20261030T194623Z`, note in
   `logs/offline-qa/budget-reservation-release-20261030.md`), then
   the durable code fix below.
8. **VM replacement #3 of the day** (19:44:45Z) wiped the units
   mid-operation again; the stack was restored from this worktree
   and the retest dialed under the standing approval.

## Build-out (this commit series, Oct 30 evening)

- **Budget release in code**: every terminal attempt transition
  (hangup event, operator resolution, stop-before-submit, born-
  cancelled prepare) converts the job's reservation to recorded
  spend in the same transaction; `reconcile_bill` records the
  actual into both fields. Tests: `tests/test_budget_reservation.py`.
- **Outcome label split**: a session record whose signed
  `call.hangup` is on record now reads `hangup` (with
  `carrier_reconciliation.hangup_source` — callee/caller — from the
  signed event); only a genuine mid-call media drop keeps
  `media_disconnected`. Adapter `reconcile_call_record` now returns
  `hangup_source`. Tests: `tests/test_disconnect_outcome.py` plus a
  hangup variant of the synthetic session.
- **Observability**: media-upgrade signature verification now logs
  both acceptance and rejection (type only); the STT service's
  `on_error` handler logs the full exception chain, so the journal
  names the real cause instead of "Connection error."
- **`tools/place_call.py`** (+ `tools/call.sh`): the single
  callable entry point — allowlist + E.164 validation, stack /
  readyz / receipt / gate / budget preflight, the
  `AI_CALLING_DIAL_APPROVED=YES_THIS_TEST_CALL` approval gate,
  `--dry-run`, `--auto-restore`, named-stage nonzero exits, no
  retries. The Oct 29 qualification dialer is untouched and still
  works. Gate logic unit-tested (`tests/test_place_call.py`);
  dry-run proven against the live stack.
- **`tools/restore_stack.sh`**: idempotent install + verify from
  the repo's canonical unit sources (`systemd/ai-calling-*.service`,
  `systemd/90-audit-candidate.conf`, `systemd/service-env-fix.env`).
  Proven: healthy-stack no-op (no restart), install-over, and full
  recovery from deleted units.

## Test state

- Application suite: **219/219** (offline_verify; 7 loopback
  connects, 0 external).
- Reference pack: **57/57**; mutations **3/3 caught**; manifest
  **verified**.

## Known remaining work (for later improvement rounds)

- Reservations still linger for a job whose bill is never
  reconciled *and* whose attempt never reaches a terminal state —
  by design (it may still be live); the gate, not the budget, owns
  that case.
- The session record's `media_disconnected` label is assigned at
  socket-close time and corrected at record time; the watcher's
  provisional label still appears in the stop reason string.
- Gemini 3.5's earlier raw-curl hangs through the proxy did not
  reproduce through the SDK path (both models answered the probe in
  ~1s); the pool order stays 3.1-first on the live evidence.
- Trial limits: one verified number; webhook resend paywalled.
  A paid Telnyx plan removes both.
- The stranded Telnyx API key (…AC719…) from Oct 29 still sits
  Active on the account; cleanup is Nathan's call.
- Push/merge state: see the git log — `review/ai-calling-candidate`
  carries the working system; `main` carries the Oct 30 morning
  bench fixes (5df6523) and, if the merge landed, this build-out.
