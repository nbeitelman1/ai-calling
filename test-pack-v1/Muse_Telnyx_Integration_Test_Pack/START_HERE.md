# Telnyx Integration Test Pack v1

Companion to the Muse Build & Acceptance Pack. Authored and tested in A-GILE, not by Muse. This is an OFFLINE reference/test pack, not a deployed caller or a certified Telnyx integration. The original pack is unchanged.

## What is real here

- Genuine Ed25519 signatures and verification over exact raw bytes.
- SQLite persistence, duplicate-event handling, and concurrent submission claims.
- Real loopback HTTP requests, including a socket closed AFTER the fake carrier accepts a create request.
- Offline parity checks against a pinned official Telnyx SDK helper.

## What is synthetic or absent

All numbers, call IDs, events, and signing keys are public fixtures. The carrier is fake. No Telnyx account, live phone, Muse runtime, voice pipeline, public webhook, TLS deployment, transfer, or billing reconciliation was exercised. No real authorization keys are read. There is no external carrier client or live mode.

## Run locally (Python 3.10+)

```bash
python -m pip install -r requirements.txt
python verify_manifest.py
python run_tests.py --output my-tests.json
python run_mutations.py --output my-mutations.json
```

Dependency installation uses the package registry. Tests use only local connections. The runner's Python socket guard is an extra check, not an OS security sandbox. Review code before execution. Output files use exclusive creation; choose new filenames for repeated runs.

Read `MUSE_HANDOFF.md` next. `docs/CONTRACT.md` separates Telnyx's contract from this pack's stricter policies. `docs/SOURCES.md` records primary sources and the SDK commit. Receipts are under `evidence/`; synthetic vectors are under `fixtures/`.

The first pack's human approvals, exact destination allowlist, budget reservations, and duration limits must remain upstream of this adapter. A new attempt ID is not permission to call again.
