# Tested scope

The included receipt is the source of truth for test counts and code hashes.

| Family | Tests | What it exercises |
|---|---:|---|
| Ed25519 / raw body / timestamp / headers | 15 | Real cryptographic verification of synthetic signatures |
| Correlation / persistence / duplicates / recovery | 23 | SQLite reference oracle and controlled concurrent operations |
| Request and event shapes | 7 | Pure builders/parser, not requests sent to Telnyx |
| Loopback HTTP integration | 6 | Fake create/hangup endpoints and signed local webhook delivery |
| Archive integrity helpers | 6 | Hash/path/extra-file checks |

Total: 57 unique tests. The run observed eight loopback connections and no external socket connection attempts. This is not network certification against arbitrary native code.

Separate evidence:
- Three deliberately broken controls were detected: signature bypass, repeated create claim, and reopening terminal state.
- Seven signature cases matched the pinned official Python SDK helper, using a second Ed25519 implementation (PyNaCl versus cryptography).

No API account, real webhook, PSTN call, Muse runtime, voice model, or real bill was exercised. Generated fixture keys are NOT Telnyx account keys. The application is a test oracle, not Nathan's production service.

Before claiming Muse's integration passes, bind assertions to Muse's actual code behind a credential-free fake transport, rerun, and label that tested target separately. Before any live call, satisfy the first pack's live gate: account/number, consenting endpoints, explicit session budget, and authenticated execution connection.
