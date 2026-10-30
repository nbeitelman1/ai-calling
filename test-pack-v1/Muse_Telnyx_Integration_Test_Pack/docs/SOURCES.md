# Primary source receipts

Checked for this pack on October 29, 2026. Vendor docs can change. No account API was queried. No third-party SDK source is redistributed here.

1. Dial: https://developers.telnyx.com/api-reference/call-commands/dial
   Required connection_id/from/to; client_state and response call identifiers.
2. Hangup: https://developers.telnyx.com/api-reference/call-commands/hangup-call
   POST action, result acknowledgment, and command_id scope.
3. Voice events: https://developers.telnyx.com/docs/voice/programmable-voice/voice-api-webhooks
   Voice v2 envelope, resource identifiers, lifecycle events, acknowledgment behavior.
4. Delivery: https://developers.telnyx.com/docs/development/api-fundamentals/webhooks/receiving-webhooks
   Exact raw body, signatures, durable acceptance, duplicate and out-of-order delivery.
5. Python SDK: https://github.com/team-telnyx/telnyx-python
   Inspected commit: 269609c283e4ecd9774127a1686c41f695654708
   Commit timestamp: 2026-10-28T09:51:54+00:00.
   Helper: src/telnyx/lib/webhook_verification.py
   SHA-256: 2c5135b23b772c86af79c9ab621f17b67f55228a40e33bc520db2c2c2d5e4bed
   This helper explicitly documents Base64 public-key bytes, timestamp-pipe-payload signing, and 300-second tolerance. resources/webhooks.py was also inspected.

`sdk_compare.py` accepts only the exact inspected helper hash. With optional requirements-sdk-check.txt installed, an operator can supply that reviewed file and rerun the seven-case comparison. The file is imported/executed only after its hash matches; the script does not download anything.

`evidence/sdk_parity.json` records actual offline comparison of cryptography verification against the SDK's PyNaCl helper. It is a bounded signature test, not full SDK/API integration or proof of a Telnyx-originating webhook. Fixture signatures come from a public test key.
