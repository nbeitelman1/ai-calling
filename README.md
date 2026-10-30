# AI Calling — Nathan's self-hosted AI phone assistant

A self-hosted system that places real phone calls and holds a two-way
spoken conversation: it dials, introduces itself honestly, listens,
thinks, answers, and hands the call back to Nathan when it should.

**Status: working end to end.** First full live two-way conversation
on **Oct 30, 2026** — 30 transcript turns, ~1.9 minutes: the callee
asked questions, got contextual spoken answers, tested the assistant's
identity ("So what's your name?" → the honest-AI answer below), and
the call closed cleanly with a signed hangup and a written record.
See `PROJECT_STATE_2026-10-30.md` for the day's full evidence trail.

**Intelligence pass (Oct 30, 2026, evening):** a Carnegie-derived
persona core, a temporal profile memory (facts with validity
windows, episodes, commitments — `docs/PERSONA_MEMORY_SPEC.md`),
a fast rule-based control plane that freezes a per-call brief,
guards against repeated questions, enforces one-terminal-line
discipline, and writes each call's facts and commitments back to
memory, plus data-file playbooks (appointment scheduling,
information gathering, follow-up) selectable via
`place_call.py --playbook/--counterparty`. The brain fails over
across the model pool (fast pool, then frontier) when a provider
model errors — one model's 503 no longer silences a call
(`server/llm_failover.py`; call records carry an `llm_failover`
block). Suite: 250/250 offline.

## Architecture

```
place_call.py / dialer          operator side: gates, prepare, claim, dial
        |
Telnyx Call Control  ── webhooks (Ed25519-signed) ──►  live_server (FastAPI)
        │                                                    │
        └── media WebSocket (signed upgrade) ──►  live_pipeline (Pipecat)
                                                     ├─ Groq Whisper  — ears (STT)
                                                     ├─ Google Gemini — brain (LLM)
                                                     └─ ElevenLabs    — voice (TTS)
Pinggy tunnel: the public wss/https front door to the local server.
SQLite (webhook.db): durable jobs, attempts, events, quotas, budget.
```

- **Adapter** (`server/telnyx_adapter/`): durable job/attempt state
  machine, single-use claim (a dial outcome is never guessed and
  never auto-redialed), idempotent signed-webhook processing,
  budget reservations, operator-attested resolution for attempts
  that can never produce evidence.
- **Pipeline** (`server/live_pipeline.py`): Telnyx media serializer,
  local VAD, Groq STT per speech segment, Gemini with a fast →
  frontier escalation ladder, ElevenLabs TTS with a pre-synthesized
  fixed-phrase cache (opening, loop-in, safety lines), silence and
  cost watchers, and a session record written for every call.
- **Front door**: a Pinggy tunnel with a persistent hostname. Telnyx
  signs both webhook POSTs and the media WebSocket upgrade; the
  server verifies before accepting either. There is no unsigned
  bypass, by design.

## Safety model

- **Disclosure, always first:** every call opens with
  *"Hi, this is Nathan's assistant calling on his behalf."*
- **Honest if asked:** *"I'm Nathan's AI assistant, calling for him.
  I can keep going, or I can bring Nathan in if you'd prefer to talk
  to him directly."* It never pretends to be human.
- **Per-call approval:** a dial requires
  `AI_CALLING_DIAL_APPROVED=YES_THIS_TEST_CALL` in the environment,
  set by the operator only after Nathan's fresh approval for that
  exact call. One approval, one call.
- **Allowlist:** destinations must be explicitly allowlisted
  (`AI_CALLING_AUTHORIZED_DESTINATIONS`), independent of approvals.
- **Caps:** $1.00 per-call reservation by default against a $20.00
  monthly cap; reservations convert to recorded spend when a call
  settles, so the cap tracks reality.
- **One-call gate:** a new dial refuses while any attempt is
  unresolved (`submit_unknown` / `initiated` / `answered`).
- **Signed media only:** media upgrades are Ed25519-verified against
  the exact public stream URL before accept.
- **Loop-in, not lock-in:** on refusal, confusion bounds, or Nathan
  being needed, the assistant says *"Nathan will need to take this
  over. I'll end the call now."* and ends the call.

## Quickstart

Restore or verify the whole stack (idempotent; also the recovery
path after a VM replacement wipes `/etc/systemd/system`):

```bash
bash tools/restore_stack.sh
```

Place a call (dry run first — every preflight check, no dial):

```bash
tools/call.sh --to +13218315278 --dry-run
AI_CALLING_DIAL_APPROVED=YES_THIS_TEST_CALL \
  tools/call.sh --to +13218315278 --objective "confirm the appointment"
```

`call.sh` is a thin wrapper that loads the production environment
into `tools/place_call.py`. Options: `--objective`, `--business`,
`--max-minutes` (default 10), `--cap-cents` (default 100),
`--auto-restore`, `--host`, `--log-name`. Exit codes name the failing
stage; nothing retries automatically.

**Trial-plan limits (current Telnyx account):** calls only complete
to *verified* numbers on the trial (exactly one is verified), and the
portal's webhook-resend tooling is paywalled. Both constraints are
the trial's, not the code's.

## Configuration landmines (learned the expensive way, Oct 30)

- **Egress proxy vs httpx:** this VM's proxy env carries a
  `NO_PROXY` list with a bare `::1` entry that httpx cannot parse
  (`InvalidURL: Invalid port: ':1]'`), which the OpenAI SDK wraps as
  an opaque "Connection error." — and httpx's bundled certifi store
  lacks the proxy's CA, so TLS then fails with
  `CERTIFICATE_VERIFY_FAILED`. Fix (in `systemd/service-env-fix.env`,
  installed by the drop-in `systemd/90-audit-candidate.conf`):
  sanitize `NO_PROXY`/`no_proxy` to `localhost,127.0.0.1` and set
  `SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`. Note the
  systemd subtlety: `EnvironmentFile` values are read at spawn and
  override `Environment=` for the same variable, so the fix must
  arrive via a late env file, not a settings line.
- **Pinggy forwards the Telnyx signature headers** on media
  upgrades — proven live Oct 30 (signed upgrade accepted; the
  applied hangup webhook carries both headers). A tunnel that strips
  them fails the media gate closed: safe, silent, and ~half a cent.
- **Preflight receipt TTL is 1 hour.** A service (re)start re-runs
  prestart (real provider checks + phrase prewarm) and mints a
  fresh, config-fingerprinted receipt. Place calls inside the
  window or restart first; receipts are never forged.
- **Model order matters through this proxy:** the fast pool is
  `gemini-3.1-flash-lite` first (answers through the egress proxy),
  `gemini-3.5-flash-lite` second.

## Tests

```bash
VENV="$HOME/.venvs/ai-calling-candidate-b849e45"
"$VENV/bin/python" tools/offline_verify.py --output logs/offline-qa/app.json
"$VENV/bin/python" test-pack-v1/Muse_Telnyx_Integration_Test_Pack/run_tests.py \
  --output logs/offline-qa/reference.json
"$VENV/bin/python" test-pack-v1/Muse_Telnyx_Integration_Test_Pack/run_mutations.py \
  --output logs/offline-qa/reference-mutations.json
"$VENV/bin/python" tools/verify_reference_manifest.py \
  test-pack-v1/Muse_Telnyx_Integration_Test_Pack
```

Current: **219/219** application tests (including the synthetic
full media session), **57/57** reference tests, **3/3** reference
mutants caught, manifest verified — all offline, zero external
connections.

## Layout

- `server/` — adapter, live server, pipeline, provider config.
- `tools/` — `place_call.py` (the callable entry point), `call.sh`,
  `restore_stack.sh`, the dated qualification dialer (kept working),
  readiness report, operator-resolution CLI.
- `systemd/` — base units, the canonical activation drop-in
  (`90-audit-candidate.conf`), and the env-fix file.
- `tests/` — the application suite; `test-pack-v1/` — the frozen
  reference pack.
- `docs/` — the offline audit (31 findings), test handoff, backlog.

## Oct 30, 2026 changelog (abridged)

- Bench-diagnosed and fixed four stacked live-path defects
  (RC1–RC4) that had made every live call silent.
- Independent offline audit candidate (31 findings) verified in
  isolation, then activated with four operator corrections
  (shared-DB pin, model order, proxy workaround, full unit install).
- Cleared all database blockers: quota settlements at zero actuals
  and six operator-attested attempt resolutions (gate fully clear).
- Test call 1: opening heard live, replies blocked by the httpx
  proxy landmines above; probe-isolated both, fixed via env file.
- Budget: one-time manual reservation release, then the durable
  code fix (reservations convert to spend at settlement).
- **Retest call: first full two-way live conversation (30 turns).**
- Build-out: `place_call.py`, `restore_stack.sh`, outcome label
  split (hangup vs media_disconnected), STT error-chain logging,
  media signature-verification logging, this README.
