# Muse: implement and qualify the Telnyx adapter

Use this as independent reference/test material alongside the first Muse Build & Acceptance Pack. Own the production implementation and operation yourself. Do not say you authored these reference files.

## Start without spending or calling

1. Verify the manifest, inspect the code, and run the offline suite in your environment. Report exact test counts and failures, not just 'works'.
2. Compare current official Telnyx schemas to `docs/SOURCES.md`; pin the API/SDK versions you use. No broad carrier research is needed again.
3. Implement your own adapter and port these assertions to exercise YOUR service against a local fake carrier. Do not substitute this reference Store and report your service passed.
4. Keep approval and budget gates from the first pack. Persist a stable job/attempt link, exact payload, and single-use submission claim before any network request. A create timeout or process crash leaves UNKNOWN, never an automatic redial.
5. Preserve raw webhook bytes and all signing-header occurrences. Verify against a securely configured account public key before parsing. Durable acceptance precedes acknowledgment; processing is idempotent and separate.
6. Bind correlation to the exact pending attempt and route. Never join call legs by session ID alone. Treat quarantine as an unresolved operator issue.
7. Store stop intent even without a call ID. Once identity is known, schedule a bounded hangup action. A command ACK is not a hangup event, and a hangup event is not a final bill.

No purchases, account changes, public tunnels, real credentials, or calls are authorized by this handoff. Native phone calling and voice quality remain separate live gates. Keep credentials in supported secure provisioning; never copy Muse surrogate tokens to another host.

Return a compact status: source revision, tests actually run, verified capabilities, unresolved gaps, and exact live prerequisites. Keep full logs/transcripts outside the chat context.
