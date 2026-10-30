# Recovery contract and limits

## States

prepared -> submit_unknown -> initiated -> answered -> hangup

Before returning a submission request, claim_submission atomically commits submit_unknown. Only one claimant wins. This deliberately remains unknown if the process dies before actually sending; preventing a duplicate is preferred to guessing that nothing happened.

A signed event may arrive before the create response. It binds only to a known, claimed attempt with matching connection, from/to, and identities. A later response cannot regress a terminal state. Unknown or conflicting events are quarantined, not used to trigger a fresh call.

stop is an independent persisted flag. A stop before claim cancels the local intent. A stop while identity is unknown waits for correlation. Once identity is known, hangup_command returns one persisted command plan. A command ACK records acknowledgment only. Confirmed hangup still requires final usage reconciliation upstream.

## Delivery processing

Signature verification and schema validation happen before durable inbox acceptance. Acknowledgment means stored, not processed. Processing and its processed flag commit in one transaction. Duplicates with the same data ID/data content are harmless even if meta.attempt changes. A conflicting event ID is rejected. Quarantined events require an explicit reviewed reprocessing/reconciliation path in Muse's production service; this pack does not supply that operator UI.

## Not implemented

No live transport, approval UI, budget ledger, duration watchdog, public TLS endpoint, secret injection, provider usage API, retries scheduler, media streaming, transfer, voice, or transcription. The original acceptance pack covers reference approval/budget logic; integration remains Muse's work.

The SQLite store contains synthetic payloads unencrypted. Production needs isolation, retention, access control, and redacted logging. call_control_id is control-bearing: do not publish real values in shared receipts. Freshness uses wall clock; reliable time synchronization is required.

The test runner's Python audit hook is not a security boundary against arbitrary native code. Review dependencies and use supported OS isolation. No arbitrary adapter import or external target option is provided.
