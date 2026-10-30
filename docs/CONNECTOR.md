# AI Calling Connector — how Muse triggers, monitors, and loops in

Status: SPECIFICATION ONLY — scaffold, Oct 27, 2026.
`server.py` does NOT exist yet, so none of the endpoints below are live.
Do not present any of them as working until the server is implemented and
a test call has proved them out. See `TEST_PLAN.md` for that proof.

This doc is written in the custom-connector style: it describes the
connection, the operations, the inputs/outputs, and the failure modes.
It does NOT contain secrets, API keys, phone numbers, or account IDs.
Keys are never pasted in chat, never committed to this repo, and never
written into a job, a log, or a transcript file.

Related docs:
- `ARCHITECTURE.md` — design, disclosure line, voice direction, guardrails.
- `TEST_PLAN.md` — the test call and first real call that prove this out.
- `NEED_NATHAN.md` — the only steps that require Nathan himself.
- `../server/call_job.py` — the CallJob schema this doc mirrors.
- `../server/voice_config.json` — voice direction and hard rules.

## What this connector is

A local control plane, not a hosted voice platform. Muse does not speak
on the phone call itself — Muse's chat loop is too slow for live audio.
Instead:

1. Muse writes a call job (objective, bounds, forbidden topics).
2. Nathan approves the exact job (required for all first live calls).
3. The self-hosted Pipecat server executes the call: Twilio/Telnyx media
   stream → streaming STT → fast streaming LLM → streaming TTS.
4. Muse polls the server for transcript, outcome, and cost, and can
   request a loop-in to Nathan at any point.
5. The server logs the transcript and cost locally (SQLite). Nothing
   is sent to a third party except the providers needed to run the call
   itself (carrier, STT, LLM, TTS).

Target in-call round trip: ~700–900 ms. That is a design target from
`ARCHITECTURE.md`, not a measured result — measure it in testing.

## Connection

- Transport: the ai-calling server's HTTP/WebSocket API, on hardware
  Nathan controls (prototype: Muse VM + tunnel; real use: VPS or home
  server with a stable public endpoint — see `NEED_NATHAN.md`).
- Authentication: server-side only. Provider keys (carrier, STT, LLM,
  TTS) live in the provider accounts and are supplied to the server
  through the Secure Vault flow when Muse issues it — never in chat,
  never in a file in this repo, never in a job payload.
- Muse-side credential: none in this repo. If a future helper needs a
  server credential, it is registered as a connection and retrieved
  through the surrogate mechanism, exactly as the custom-connector
  skill prescribes. A helper that cannot authenticate is a blocker,
  not a reason to paste a key.
- First-call proof: the first real operation against any new account
  is a harmless read (e.g. account/number lookup), then the test call
  to Nathan's own phone. See `TEST_PLAN.md`.

## Operations

All endpoints below are PROPOSED. Implement them in `server.py` before
using this doc operationally, and update this doc with the exact
paths, response shapes, and error codes once they are real. Do not
invent a different endpoint at call time.

### 1. Submit a call job — `POST /calls`

Input: a CallJob JSON, matching `server/call_job.py` exactly.

```json
{
  "to": "+1XXXXXXXXXX",
  "business": "Portfolio / GAP claims",
  "objective": "Ask for GAP claim status, confirm packet completeness, ask about any 150%-of-ACV cap",
  "allowed_decisions": ["accept a callback time", "confirm contact details already on file"],
  "forbidden": ["SSN", "card numbers", "payment authorisation", "signing anything"],
  "loop_in_triggers": ["human requested", "decision outside job", "sensitive data requested"],
  "max_minutes": 10,
  "opening": "Hi, this is Nathan's assistant calling on his behalf about his GAP claim status. Is now an okay time?"
}
```

Required fields: `to`, `business`, `objective`.
Defaults applied by the server if omitted: `max_minutes` = 10,
`forbidden` = the full `FORBIDDEN_TOPICS` list in `call_job.py`,
`loop_in_triggers` = the full `LOOP_IN_TRIGGERS` list in `call_job.py`,
`opening` = `OPENING_TEMPLATE` with the objective filled in.

Server-side validation — reject the job (do NOT dial) if any of these fail:
- `to` is not a US/Canada number, or is an emergency-services or
  premium-rate number.
- A forbidden topic appears in the objective, the allowed decisions,
  or the opening — the server must refuse, not silently strip it.
- The monthly wallet cap ($20.00 default, see `call_job.init_db`) is
  already reached, or the estimated call cost would breach it.
- Another call is already in progress (one concurrent call at first).
- The job was not approved by Nathan, for any call in the
  first-live-call phase. Approval is recorded with the job, not assumed
  from a general "go ahead" — see "Approval model" below.

Success response (proposed): a call id, the validated job, and
`status: "queued"`. The call id is the handle for every later operation.
Failure response: an explicit reason (validation, cap, busy, server
down) — never a silent retry. After an uncertain submit, check
`GET /calls/{id}` / the calls table before re-submitting, so a job is
never dialled twice by accident.

### 2. Poll for status, transcript, outcome, and cost

`GET /calls/{id}` — current status, elapsed minutes, and cost so far.
`GET /calls/{id}/transcript` — turn-by-turn transcript so far
(speaker, text, timestamp). This is how Muse "monitors" a live call.
`GET /wallet` — monthly cap, spent this month, remaining, and the
80%-of-cap alert state.

Poll cadence: every 15–30 seconds while a call is live; once at
completion for the final record. Polling does not interrupt the call.

Call statuses (proposed, keep this list in sync with `server.py`):
`queued`, `dialing`, `in_progress`, `loop_in_requested`,
`loop_in_bridged`, `completed`, `voicemail_hung_up`, `failed`,
`cancelled`, `cap_blocked`.

Final record for a completed call — Muse must capture all of it
before reporting to Nathan:
- `outcome`: one of `achieved`, `partial`, `failed`, with a
  one-line reason.
- `commitments`: what the other side actually agreed to (dates,
  times, reference numbers) — read back verbatim from the transcript,
  never summarised into a new number.
- `follow_ups`: anything still owed, by whom, by when.
- `stopping_point`: if the call ended early (loop-in declined,
  refusal, voicemail, cap, confusion), the exact point it stopped.
- `minutes` and `estimated_cost_usd`, broken down where the server
  can supply it (carrier minutes, STT, LLM tokens, TTS characters).
  Local estimates are NOT provider bills — see "Cost" below.
- `transcript_path`: local file/SQLite row id for the full transcript.

Muse's report to Nathan after a call is built from that record only.
If the transcript is missing or truncated, say so — do not reconstruct
the call from memory.

### 3. Loop Nathan in — `POST /calls/{id}/loop-in`

Two ways a loop-in starts, both ending in the same server flow:
- Server-detected trigger: any `LOOP_IN_TRIGGERS` event during the
  call (human requested, asked if AI, refused to talk to an assistant,
  decision outside the job, sensitive data requested, repeated
  misunderstanding, max duration, caller upset, voicemail/phone tree).
- Muse-requested: Muse sees something in the live transcript that
  warrants Nathan, and posts the loop-in with a reason.

Server flow (must be implemented exactly this way):
1. The agent pauses and says, verbatim in substance:
   "Let me bring Nathan in — one moment."
   No call details are spoken to the other side while bridging.
2. The server dials Nathan's phone and plays a private briefing
   (who, business, objective, where the call stands, what decision
   is needed) — audible only to Nathan, not to the other party.
3. Nathan accepts (explicit accept, e.g. voice/DTMF confirm as
   implemented) → the server bridges Nathan into the call. The agent
   steps back; Nathan talks.
4. Nathan declines or does not answer → the agent ends the call
   politely, and the final record notes the exact stopping point.
   No second auto-dial to Nathan for the same trigger unless Nathan
   asks for one.

Muse must also tell Nathan in chat that a loop-in was requested and
why, so a missed bridge is not silent. If Nathan was looped in, the
report says so and names the decision he made on the call, if any.

`POST /calls/{id}/cancel` — ends a live call politely and records
`cancelled` with the stopping point. Use it if Nathan asks to stop a
call, or if a cap/guardrail fires mid-call.

### 4. Voice — how the voice is chosen and changed

Voice direction is fixed in `../server/voice_config.json`:
feminine, warm, confident, slightly sultry in vibe, slightly slower
pacing, mid-low pitch, short spoken sentences. Final voice = a stock
synthetic voice picked by ear by Nathan on the test call, from 2–3
candidates.

Hard rules (from `voice_config.json`, enforced in every job):
- No cloning of Megan Fox, Madison Beer, or any other real person.
- No impersonating Nathan or any real employee.
- If asked if AI: "I'm Nathan's AI assistant, calling for him."
- Opening line: "Hi, this is Nathan's assistant calling on his behalf
  about [objective]. Is now an okay time to talk through that?"
- If asked directly whether it is a person: answer honestly, offer
  to bring Nathan in. Never claim to be human.

Changing the voice means changing the voice id in
`voice_config.json` and re-running the test call — it is never a
mid-call change, and never a clone upload.

## Approval model

- Phase 1 (now, and through the first real business calls): Nathan
  approves the EXACT job in chat — number, business, objective,
  allowed decisions, max minutes — before Muse submits it. A general
  "build it" or "go ahead" is not approval for a specific call.
- Phase 2 (only after test calls prove out, and only if Nathan
  explicitly loosens it): pre-approved job types (e.g. status checks
  with no binding decisions) may be submitted without per-call
  approval. Binding decisions, payments, and anything outside a
  pre-approved type still require per-call approval. Phase 2 is not
  in effect until Nathan says so.
- Every job, in either phase, still runs the server-side validation
  and cap checks above. Approval never overrides a forbidden topic
  or a cap.

## Cost — local ledger vs provider bills

- The server's SQLite wallet (`call_job.init_db`, `wallet` table) is
  a LOCAL prepaid ledger: monthly cap $20.00 default, $5/call soft
  cap, alert at 80% of monthly cap. It records estimated cost per
  call and blocks new calls at the cap.
- Provider balances (Twilio/Telnyx, STT, LLM, TTS) are topped up
  manually by Nathan with a card, in small amounts. There is NO
  bank-account auto-recharge wired into this server, and NO
  auto-drain. Provider-side auto-recharge, if ever wanted, is a
  separate explicit decision on the provider's dashboard, on a
  low-limit card.
- Local estimates will differ from provider bills (rounding,
  minimums, per-provider metering). Reconcile the local ledger
  against provider dashboards after the first few calls and adjust
  the estimating factors. Until reconciled, report costs as
  "estimated, per local ledger" — never as a provider-confirmed bill.
- Muse's Wallet (Shop Pay / Stripe Link) is unrelated and is NOT
  used here.

## Errors and recovery

- Server down / unreachable: report it, do not queue a call "for
  later" unless Nathan explicitly asks for a retry window. A queued
  job that was never dialled must be distinguishable from a dialled
  one.
- Carrier/STT/LLM/TTS provider error mid-call: agent ends politely
  if it can, server records `failed` with the provider error class
  (not the raw provider message if it contains account details),
  Muse reports the failure and the stopping point.
- 401/403 from a provider: do NOT re-paste a key. Check that the
  server is carrying the credential, that the host/scope matches,
  and that the key is still valid in the provider dashboard. Re-issue
  through the Secure Vault flow only if a credentialed request is
  still rejected.
- Transcript missing, partial, or garbled: report the gap, keep the
  outcome as `partial` at best, and do not fill in commitments from
  guesswork.
- Duplicate submit: check state first. The first request may have
  completed even if its response was lost.

## What this connector must never do

- Never dial without a validated job and (in Phase 1) Nathan's
  exact-job approval.
- Never freelance beyond the job's objective and allowed decisions.
- Never speak, confirm, or enter: SSN, card/bank numbers, one-time
  codes, passwords, background-check consent, legal-attestation
  signatures, or a payment without explicit per-call approval.
- Never claim to be Nathan or a human employee, and never lie when
  asked if it is AI.
- Never bulk-dial, cold-call, or market. One user-directed call at
  a time, US/Canada only at first, no emergency or premium-rate
  numbers.
- Never store a secret in a job, a transcript, a log, or this repo.
- Never present a scaffold endpoint, an estimated cost, or a target
  latency as a measured, live result.
