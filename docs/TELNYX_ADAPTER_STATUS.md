# Telnyx adapter — compact status (Oct 29, 2026, offline only)

No live call, purchase, account change, public tunnel, or real
credential was made or used. Everything below is offline / loopback.

## Authorship, stated plainly

- The reference pack (`test-pack-v1/Muse_Telnyx_Integration_Test_Pack/`)
  was authored and tested in A-GILE, not by Muse. Muse did not author
  those reference files.
- The production adapter in `server/telnyx_adapter/` and its tests in
  `tests/test_telnyx_adapter.py` are Muse's own implementation, built
  from the contract in the reference pack and from Telnyx docs checked
  Oct 29, 2026. A pass in the second suite is a pass of Muse's service,
  not a pass of the reference Store.

## Source revision

- Reference pack: manifest `muse-pack-manifest/v1`, scope
  `TELNYX_OFFLINE_TEST_PACK_NOT_PRODUCTION`, 28 files checked,
  0 errors, 0 unlisted Python files, manifest verification PASS
  (re-run Oct 29, 2026 after the own-adapter work; pack untouched).
- Reference receipts on file (unchanged): `evidence/tests.json`
  (57 tests), `evidence/sdk_parity.json` (7 cases),
  `evidence/mutations.json` (3 cases).
- Muse's adapter, SHA-256 (Oct 29, 2026):
  - `server/telnyx_adapter/__init__.py` ff50a1bc7ad28723e1aec9de6f819fbb8ecb3009c1311842b01bb8895cda7e35
  - `server/telnyx_adapter/adapter.py` ac4380422c3d6a9422325ee615938970cb137cb1621ee1c73bf3dd6faca22324
  - `server/telnyx_adapter/config.py` 82ec92f533a7c7d5103b4e923caaac4e368ad5c9a45a580bd2fd0d5056d95498
  - `server/telnyx_adapter/transport.py` 613ee8c7c37194ac69cfe803b3e99a2ea755d0a30157415f1d58cf408d4c5b5d
  - `server/telnyx_adapter/verify.py` 8caba6e904690c949b0194fbbde5779f0f1abdff8b0aeed71c128607ce5ff976
  - `server/telnyx_adapter/wire.py` 2976b561e95f07c97fe33716c42049966126a23571c78ba2178ff503b842ae3a
  - `tests/test_telnyx_adapter.py` 8d1e18e6303ee2df4228eaacead9c87f95d7117869b6c43cc20743746bbce905

## Tests actually run (not just "works")

Reference suite, re-run in this environment:
- `verify_manifest.py`: PASS, 28 files.
- `run_tests.py`: **57 run, 57 pass, 0 fail, 0 skip** (0.431s),
  Python 3.12.3, cryptography 46.0.6, loopback connects 8,
  blocked external connects 0, real provider calls 0.
  Receipt: `logs/ref-tests-2026-10-29.json`.
- `run_mutations.py`: **3/3 broken controls caught**
  (signature bypass, repeated create claim, reopening terminal state).
  Receipt: `logs/ref-mutations-2026-10-29.json`.

Muse's own suite, against Muse's own fake carrier:
- `python -m unittest discover -s tests`: **37 run, 37 pass, 0 fail**
  (0.309s). Log: `logs/own-tests-2026-10-29.txt`.
  Breakdown: wire 5, signatures 3, adapter recovery 21,
  approval/budget 4, loopback HTTP 4.
  The own suite adds, beyond the ported assertions: session-id-alone
  never correlates, dropped-response leaves UNKNOWN with no redial,
  stop-before-job-exists is remembered, and hangup-is-not-a-bill.

## Schema comparison vs docs/SOURCES.md (checked Oct 29, 2026)

Pinned: Telnyx Voice API **v2** (`POST /v2/calls`,
`POST /v2/calls/{call_control_id}/actions/hangup`), webhook v2
envelope, Python 3.12.3, cryptography **46.0.6**. The Telnyx Python
SDK is NOT imported by Muse's adapter; its inspected commit
`269609c283e4ecd9774127a1686c41f695654708` (2026-10-28, helper
`src/telnyx/lib/webhook_verification.py`, SHA-256
`2c5135b23b772c86af79c9ab621f17b67f55228a40e33bc520db2c2c2d5e4bed`)
is a reference pin only.

- Dial: still `connection_id` / `from` (+E.164) / `to` required;
  `client_state` Base64; `command_id` documented as "Telnyx will
  ignore other Dial commands with the same command_id". Matches.
  Telnyx also allows `to` as an array and SIP URIs — Muse's adapter
  deliberately accepts only one +E.164 string. That is a narrower
  application policy, not a Telnyx limit, and Muse does NOT infer
  global create-call idempotence from `command_id`.
- Hangup: still `/v2/calls/:call_control_id/actions/hangup`, with
  `command_id` as the command duplicate scope. Matches.
- Webhooks: still `data.record_type == "event"`, `id`, `event_type`,
  `occurred_at`, payload ids (`call_control_id` / `call_leg_id` /
  `call_session_id`, session shared across legs), `client_state`,
  `meta.attempt`. Matches. Deduplication is on canonical DATA, not
  the envelope, because `meta` can change on redelivery.
- Signatures: still `Telnyx-Signature-Ed25519` +
  `Telnyx-Timestamp` over `{timestamp}|{raw_body}`, Ed25519,
  5-minute (300s) freshness. Matches.

## Verified capabilities (Muse's adapter, offline)

1. Approval + budget gates upstream of dial: per-job Approval
   (job id, exact destination, max minutes, estimate) is required;
   destination must be on the allowlist; estimate over the $5/call
   cap or reservations over the $20/month cap are refused at
   prepare time. Reservation is recorded at prepare, not spent
   at dial.
2. Stable job/attempt link + exact payload + single-use claim
   persisted before any network request. Claim is atomic; a second
   claim fails. A dropped fake response leaves `submit_unknown`
   (UNKNOWN) and the adapter raises `SubmissionUnknown` — no
   automatic redial, no new attempt. Recovery is only by a verified
   create response or a verified signed event.
3. Raw webhook bytes + all header occurrences are stored on
   acceptance; signature is verified over raw bytes BEFORE parsing,
   against the configured public key, with duplicate signing headers
   rejected and a 64 KiB cap and 300s window enforced. Ack means
   stored, not processed; processing and its processed flag commit
   in one transaction. Duplicates (same data id + digest) are
   harmless; an id collision is rejected.
4. Correlation binds the exact pending attempt AND route
   (connection/from/to). Control-id and client_state hint must
   agree when both are present. Session id is never queried alone
   (tested explicitly). Conflicting legs, wrong routes, unknown or
   unclaimed attempts, and invalid correlation are quarantined —
   stored, listed via `list_quarantined()`, never executed.
   Lifecycle is monotonic and hangup is absorbing; late responses
   and out-of-order events cannot reopen a terminal attempt.
5. Stop intent is persisted even with no call id, and even before
   the job exists (pending-stop record). A stop before claim
   cancels without sending; a stop while identity is unknown waits;
   once identity is known, one persisted hangup command is
   produced (same command reused inside a 60s local window; after
   the window, reconciliation is required, no new command invented).
   A hangup ACK sets only the ack flag — it is not a hangup event.
   A hangup event sets state hangup but does NOT reconcile billing;
   `reconcile_bill()` is a separate explicit step after hangup
   (overage is reported, not silently absorbed).

## Unresolved gaps (not papered over)

- Live transport is NOT implemented and is NOT enabled
  (`live_enabled` defaults False; `LiveTransport` raises by design).
  No Telnyx account, number, connection id, API credential, or
  webhook public key is configured in this build.
- SDK parity was NOT re-run for Muse's own verifier: the reference
  evidence (7 cases, cryptography vs the SDK's PyNaCl helper) stands
  for the reference verifier only. Muse's verifier is tested with
  its own cryptography-signed fixtures (valid, tampered, stale,
  future, boundary, duplicate-header, wrong-key, bad-encoding cases).
- No public TLS webhook endpoint, no media streaming, no Pipecat
  voice pipeline, no transfer, no DTMF/phone-tree handling, and no
  duration watchdog timer (max_minutes is stored and gated at
  approval; a running-call timer is not yet wired).
- Quarantine has a list function, not an operator UI or a reviewed
  reprocessing path. Quarantined events stay unresolved until an
  operator reviews them.
- The 60s hangup-command reuse window is a conservative local
  policy, not a measured provider guarantee. Live retry policy must
  still be reconciled against current provider docs and call state.
- Native phone calling and voice quality remain separate live
  gates, untouched by this offline work.

## Exact live prerequisites (none of these are done, none are authorized yet)

1. Nathan decides the paused Telnyx account question (Pretrial /
   funding / upgrade) — the build stays paused on spending until
   he says to pick it up again. No upgrade, funding, or number
   purchase is authorized by this status.
2. A dedicated AI number is bought, and a Call Control application
   / connection_id exists for it.
3. Telnyx API credential and webhook public key are supplied ONLY
   through supported secure provisioning (references, never values
   in chat or files), and `live_enabled` is turned on explicitly
   with those references present.
4. A public, stable HTTPS webhook endpoint exists, with the
   adapter's verify-before-parse path in front of it.
5. Destination allowlist and budget caps are set for the exact
   job, and Nathan approves that exact job (number, objective,
   bounds) before any dial.
6. The first pack's Phase A test call to Nathan's own phone passes
   in full (disclosure, loop-in, forbidden topics, cost/transcript
   log) before any real business call — and the native-calling and
   voice-quality live gates are satisfied separately.

## Files

- Adapter: `server/telnyx_adapter/` (`config.py`, `wire.py`,
  `verify.py`, `adapter.py`, `transport.py`, `__init__.py`)
- Tests: `tests/test_telnyx_adapter.py`
- Logs (outside chat context): `logs/ref-tests-2026-10-29.json`,
  `logs/ref-mutations-2026-10-29.json`, `logs/own-tests-2026-10-29.txt`
- Reference pack (unchanged, not Muse-authored):
  `test-pack-v1/Muse_Telnyx_Integration_Test_Pack/`
