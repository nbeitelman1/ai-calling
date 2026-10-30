# Wiring prep — Telnyx adapter reconciliation, voice pipeline, webhook + app plan

Date: Oct 29, 2026. Phase: WIRING PREP ONLY, per Nathan's "go ahead" on
wiring up the test setup. This phase dials NOTHING, funds NOTHING, buys
NOTHING, opens NO tunnel, creates NO provider account, and invents NO
API key. A test call is drafted at the end of this document for Nathan's
exact approval; it is NOT approved by this phase.

Live state at the start of this phase (verified Oct 29, 2026):
- Telnyx account: Business Trial. Number +1 321 497 8649 (local,
  Melbourne, FL, voice-enabled) is Active and the only number on the
  account. Balance $3.90. No payment method on file.
- No Call Control Application, webhook, or voice wiring existed yet.

## 1. Adapter reconciliation — ONE canonical adapter

Two adapters existed. Both were re-run in this phase (see §5); neither
was deleted. The archived one is kept intact as reference.

### Canonical: `server/telnyx_adapter/` (ai-calling tree)
Chosen because:
- It lives alongside the scaffold it must serve (`server.py`,
  `call_job.py`) instead of in a scratch work tree.
- Credential hygiene is strictest: config holds credential
  *references only* (`api_credential_ref`, `webhook_public_key_ref`),
  and `live_enabled` defaults False with the live transport raising
  by design until explicitly enabled with references present.
- Approval is an explicit per-job `Approval` object, checked against
  the exact destination, max_minutes, and estimated cost — approval is
  never inferred from a job, attempt id, or a boolean flag.
- Widest own suite: 37 tests (wire 5, signatures 3, adapter recovery
  21, approval/budget 4, loopback HTTP 4), including session-id-alone
  never correlates, dropped-response leaves UNKNOWN with no redial,
  stop-before-job-exists is remembered, and hangup-is-not-a-bill.
- It already has `list_quarantined()`, `reconcile_bill()` (requires a
  hangup event first; overage reported, never silently absorbed), and
  a status doc with SHA-256 pins (`docs/TELNYX_ADAPTER_STATUS.md`).

### Archived reference: `telnyx-adapter-work/adapter/` (DO NOT DELETE)
Kept intact. Its own suite (30 tests) also passes. What was *ported
in spirit* from it into the canonical wiring (not by editing the
canonical adapter's code, so its 37-test suite is untouched):
- Job-script guard: disclosure opening required + SSN/card-pattern
  scan — implemented as `validate_job_script()` in the pipeline layer
  (`server/voice_pipeline.py`), as a second check on top of the
  canonical adapter's binding Approval gate.
- Webhook HTTP endpoint shape — implemented as `server/telnyx_webhook.py`
  wrapping the canonical adapter (see §3), rather than reusing the
  archived tree's `webhook_server.py` directly.
- Per-call duration bound idea — carried as `max_minutes` on the job
  plus the pipeline's cost guard; a running-call watchdog timer is
  still an open gap in BOTH adapters (see §6).

### Safety gates preserved in the canonical adapter (verified by tests)
Persisted attempts + exact payload + single-use claim BEFORE any
network request; timeout/dropped-socket/crash → UNKNOWN, no auto-redial;
webhook signature verified over raw bytes BEFORE parsing, raw bytes +
ALL header occurrences preserved, 64 KiB cap, 300 s window, duplicate
signing headers rejected; durable acceptance separate from idempotent
processing; correlation binds the exact pending attempt AND route,
session-id-only joins never correlate, route/control/leg mismatches →
quarantine, and quarantine NEVER triggers a call; stop intent stored
even with no call id (even before the job exists); one persisted
hangup command per attempt+leg (60 s reuse window, then reconcile);
carrier "ok" = ACK only; hangup event ≠ final bill.

## 2. Voice pipeline skeleton — `server/voice_pipeline.py`

Pipecat-style outline, dependency-free at import (Pipecat guarded
behind `PIPELINE_AVAILABLE`, exactly like `server.py`). The pipeline
body, provider SDK bindings, and Telnyx media-stream transport are
OUTLINES, not implementations — they need the deploy step in §4.

Flow:
  Telnyx Call Control (+1 321 497 8649)
  → local webhook server (canonical adapter)
  → media transport (attach by call_control_id + client_state hint;
    NEVER by session id alone)
  → STT, streaming (Deepgram-class)
  → LIVE BRAIN: fast model (default talker)
  → on stuck trigger: FRONTIER BRAIN (bounded try, below)
  → only if frontier also fails: NATHAN LOOP-IN
  → TTS: ElevenLabs female voice, current pick Natasha
    (voice id kCx3Qoh3lfILbbTZftSq — an id, not a secret; direction
    feminine/warm/slightly sultry, stock synthetic, NEVER a clone of
    a real person, never impersonating Nathan).

### Escalation bound (Nathan, Oct 29, 2026)
Ladder: fast model → frontier model (automatic) → Nathan. This
supersedes the first pack's "loop Nathan in directly" for stuck calls.
The frontier try is BOUNDED — whichever limit hits first ends it:
- **FRONTIER_MAX_TURNS = 3** (at most 3 frontier replies), AND
- **FRONTIER_MAX_SECONDS = 120** (at most 2 minutes on the frontier).

Why these numbers: a frontier minute costs several times a fast-model
minute, and the whole test balance is $3.90. Three turns / two minutes
is enough for a frontier model to untangle a misunderstood objective,
a phone-tree branch, or a rep's unexpected question — the realistic
stuck cases — without letting a lost call burn the balance. If the
call is still stuck after that, a human (Nathan) is strictly better
than more model turns. The bound is enforced in code
(`EscalationState`), not in a prompt the model can talk its way past.
Cost guard on top: the first test call has a hard $1.00 budget
(`TEST_CALL_BUDGET_CENTS = 100`); projected spend over it ends the
test call gracefully instead of spending on. The $20/mo ledger cap and
$5/call soft cap from the first pack remain the binding gates for any
live business call.

### What the skeleton proves today (run Oct 29, 2026, no providers)
- Disclosure guard accepts a proper opening, rejects a missing
  opening, rejects an SSN-shaped script.
- Escalation state machine: fast → frontier on a stuck trigger,
  3 frontier replies → stage "nathan"; budget checks: $0.50 test-call
  spend ok, $1.50 test-call spend rejected, $6.00 live spend hits the
  soft cap. All assertions passed; Pipecat correctly reports "not
  installed on this machine" (outline, not a live pipeline).

## 3. Webhook + Telnyx application plan (NOT executed in this phase)

### Local webhook server — `server/telnyx_webhook.py`
Wraps the canonical adapter; stdlib HTTP only (no new dependency).
- `POST /telnyx/webhooks` (primary) and
  `POST /telnyx/webhooks-failover` (failover, same handler, same rules).
- Verify-before-parse → durable store → 200 ack (ack = stored, not
  processed) → separate idempotent processing. Quarantine is a 200
  with a quarantined outcome, never a redelivery loop and never a
  call trigger. Bad signature / stale / oversize / duplicate header
  → 401, nothing stored.
- Smoke-tested Oct 29, 2026 against a locally generated Ed25519 key
  (no real key): valid event → 200 + `quarantined_unknown_or_unclaimed`
  (unknown control id, correctly NOT applied); redelivery → 200 +
  duplicate; tampered body → 401.
- The server refuses to start unless `TELNYX_WEBHOOK_ENABLED=1` and a
  public-key *reference* are set. It never reads a key value from a file.

### Telnyx application settings (exact, for the parent to execute
with Nathan's approval — NOT created here)
- Application/connection: create a **Call Control** application in
  the Telnyx portal. Suggested name: `Nathan AI Assistant (Call Control)`
  (name is cosmetic; Nathan may rename it).
- Webhook URL (primary): `https://<public-host>/telnyx/webhooks`
  where `<public-host>` is the prototype tunnel host (Muse VM + tunnel
  first, per the Oct 27 decision; the relay already uses a tunl host,
  but a SEPARATE tunnel/hostname for calling is recommended so relay
  and calling do not share fate — parent to confirm at deploy time).
- Webhook failover URL: `https://<public-host>/telnyx/webhooks-failover`
  (same host for the prototype — a stated weakness: a host outage
  takes both URLs down; a second independent host is the production fix).
- Webhook API version: **`2`** (the canonical adapter requires the v2
  envelope and rejects anything else).
- Number assignment: assign **+1 321 497 8649** to this application.
- Outbound: no SMS/MMS profile, no messaging profile (voice only).

### Exact ordered steps to create it (parent executes, Nathan approves)
1. Resolve credential refs via Secure Vault (see §4) — values never
   in chat/files/logs. Set `TELNYX_WEBHOOK_PUBLIC_KEY_REF`.
2. Start the local webhook server on the Muse VM
   (`TELNYX_WEBHOOK_ENABLED=1`, local port, e.g. 8001 — keep it off
   the relay's port).
3. Open the prototype tunnel to that port. Record the public host.
   (NOT done in this phase.)
4. In the Telnyx portal, create the Call Control application with the
   settings above. Record the `connection_id`.
5. Assign +1 321 497 8649 to the application.
6. Set the adapter config: `connection_id` (from step 4),
   `from_number` = +1 321 497 8649, `webhook_public_key_ref`,
   `api_credential_ref`, `live_enabled` only when steps 1–5 verify.
7. Re-run the signed-webhook smoke test against the PUBLIC URL with
   a Telnyx test event before any dial (fake-sender test first, then
   a portal-sent test if Telnyx offers one — do not guess at one).
8. Present the exact test-call job (§7) to Nathan for approval. No
   dial happens before that approval.

## 4. Credentials / inputs still needed from Nathan (references only)
No values are in this document, in code, or in chat. Each is supplied
via Secure Vault (or portal session) at deploy time:
1. **Telnyx API key** (Secure Vault ref) — for live Call Control dials.
2. **Telnyx webhook public key** (Secure Vault ref) — for
   verify-before-parse. (Different from the API key.)
3. **Telnyx connection_id** — produced by step 4 in §3, not a secret
   but required before any dial.
4. **ElevenLabs API key** (Secure Vault ref) — TTS voice (Natasha id
   is already on file; the key is not).
5. **Fast-model LLM API key** (Secure Vault ref) — live brain.
   Provider/model chosen at deploy time (fast, streaming-capable);
   no model is invented here.
6. **Frontier-model LLM API key** (Secure Vault ref) — escalation
   brain. May be the same provider as (5) with a different model;
   listed separately because its spend is bounded separately.
7. **Deepgram API key** (Secure Vault ref) — streaming STT.
8. **Nathan's own phone number for the test call** — held by the
   parent from prior context, NOT reprinted here; filled in at
   approval time. (Also the loop-in number for live business calls.)
9. **Tunnel host decision** — separate calling tunnel vs reusing the
   relay tunnel host (recommendation in §3; Nathan/parent confirm).

## 5. Tests re-run in this phase (all offline, loopback only)
- Reference pack: **57/57 PASS**, mutations **3/3 caught**
  (re-run Oct 29, 2026 with the archived tree's venv; outputs in /tmp,
  ephemeral — receipts on file in `ai-calling/logs/` are the Oct 29
  originals and are unchanged).
- Canonical adapter (ai-calling): **37/37 PASS** (0.35 s).
- Archived adapter (telnyx-adapter-work): **30/30 PASS** (26 s).
- New skeleton checks (this phase): guard + escalation + budget
  assertions all passed; webhook smoke test passed (200/duplicate/401
  as designed). Pipecat is NOT installed here, so the pipeline body
  remains an outline — see §6.

## 6. Open gaps (not papered over)
- Pipecat pipeline body, provider SDK bindings, and Telnyx media
  streaming are OUTLINES. First live run must install pinned
  requirements on the deploy machine and verify imports (the Oct 27
  skeleton notes the same caveat; module paths moved across Pipecat
  releases).
- No running-call watchdog timer in either adapter (max_minutes is
  gated at approval, not enforced mid-call). Add before business calls.
- Hold/unhold event ordering is unimplemented in both adapters.
- Quarantine has a list function, not an operator UI or reviewed
  reprocessing path. Quarantined events stay unresolved until an
  operator reviews them.
- Failover URL on the same prototype host is a single point of
  failure (see §3).
- Voice quality, Florida all-party-consent / TCPA artificial-voice
  disclosure review, and the first-pack live gate (Phase A test call
  to Nathan's own phone passing in full) are still open.
- The archived adapter's SQLite store is unencrypted and test-only;
  the canonical adapter's store inherits the same caveat for any
  production deployment (isolation, retention, redacted logging).

## 7. Draft test-call job — FOR NATHAN'S APPROVAL (not approved yet)
- Destination type: **Nathan's own phone** (Phase A test, per the
  first pack — the callee IS Nathan, so "loop-in" on this test means
  he is already on the line; the escalation ladder is exercised by
  asking the assistant a question outside the job and watching it
  hand up to the frontier bound, not by looping in a third party).
- Objective: verify the full chain end to end — disclosure opening
  spoken verbatim, ElevenLabs Natasha voice naturalness, barge-in
  (Nathan interrupts mid-sentence; the assistant must stop),
  frontier escalation on an out-of-scope question (max 3 turns /
  120 s), graceful end, transcript + cost log written.
- Disclosure opening (forced): "Hi, this is Nathan's assistant
  calling on his behalf about a test of the calling setup. Is now an
  okay time to talk through that?"
- Allowed decisions: NONE except ending the call. No business
  decision, no payment, no personal data.
- Max duration: 10 minutes (job cap); expected actual: ~5 minutes.
- Success criteria: (1) disclosure verbatim; (2) voice judged
  natural by Nathan's ear; (3) barge-in works at least once;
  (4) out-of-scope question triggers frontier try, then a clean end
  (no loop-in spiral, no hang); (5) transcript + measured cost saved.
- Cost estimate (published-rate arithmetic, NOT a quote; measured
  rates at call time govern): 5 min × Telnyx outbound ~$0.007 =
  $0.04; STT streaming ~$0.008/min = $0.04; ElevenLabs fast TTS
  ~$0.04/1k chars × ~1,900 chars of AI speech = $0.08; fast LLM
  ~$0.02; frontier escalation if triggered (≤2 min) ~$0.10–0.30.
  **Total ≈ $0.25–0.50, inside the $1.00 test budget, the $5/call
  soft cap, the $20/mo ledger cap, and the $3.90 Telnyx balance.**
  If the estimate at deploy time exceeds $1.00, the test call does
  NOT proceed without a fresh approval.
- Hard stops: no dial before Nathan approves THIS exact job; no
  second call, no business destination, no funding, no tunnel left
  running after the test without a fresh decision.
