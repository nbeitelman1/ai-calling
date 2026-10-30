"""Voice pipeline skeleton for Nathan's self-hosted AI calling system.

WIRING-PREP ONLY (Oct 29, 2026). No live call is dialled by this file,
no tunnel is opened, no provider account is created, and no API key
appears here — every provider credential is a *reference* (an env-var
name / Secure Vault ref) resolved at deploy time, never a value.

Architecture (see docs/WIRING_PREP_2026-10-29.md):
  Telnyx Call Control (number +1 321 497 8649, voice-enabled)
      -> local webhook server (canonical adapter: verify-before-parse,
         durable acceptance, idempotent processing, quarantine)
      -> media transport (Telnyx media stream; Pipecat outline below)
      -> STT (streaming, OpenAI — Deepgram DROPPED Oct 29, 2026, unusable
         through the vault path; see live_pipeline.py)
      -> LIVE BRAIN (fast model) -- the default talker
      -> on stuck triggers: FRONTIER BRAIN (bounded try, see below)
      -> only if the frontier also fails: NATHAN LOOP-IN
      -> TTS (ElevenLabs female voice, current pick: Natasha)

Escalation ladder (Nathan, Oct 29, 2026 — supersedes "loop Nathan in
directly" from the first pack):
  1. Fast model handles the call.
  2. On a stuck trigger (repeated misunderstanding, decision outside
     the job, sensitive-data request, caller upset/escalating,
     unresolvable phone tree), the FRONTIER model takes over
     automatically — no Nathan involvement yet.
  3. Only if the frontier ALSO fails within its bound does the call
     loop Nathan in (or end gracefully if he cannot be reached).

Frontier bound (chosen for the $3.90 test balance and the $20/mo
ledger cap; see the wiring doc for the arithmetic):
  FRONTIER_MAX_TURNS = 3       # at most 3 frontier replies
  FRONTIER_MAX_SECONDS = 120   # at most 2 minutes on the frontier
  Whichever limit is hit first ends the frontier try.
  TEST_CALL_BUDGET_CENTS = 100 # $1.00 hard budget for the first test
  call; if projected spend would exceed it, the call ends gracefully
  (or loops Nathan in on a live business call) instead of spending on.

Hard rules carried over unchanged from call_job.py / server.py:
- Disclosure opening is forced; a job cannot drop "Nathan's assistant".
- If asked "are you AI?": "I'm Nathan's AI assistant, calling for
  him. I can keep going, or I can bring Nathan in if you'd prefer
  to talk to him directly."
- Forbidden on every call: SSN, card/bank numbers, one-time codes,
  passwords, background-check consent, legal-attestation signatures,
  payment authorisation without Nathan's explicit per-call approval.
- Voice: ElevenLabs female, stock synthetic, NEVER a clone of a real
  person; never impersonating Nathan or any real employee.
- One concurrent call at first. US/Canada (+1) only. No emergency
  services, no premium-rate numbers, no bulk/cold/marketing calls.
- Wallet: $20/month local cap, $5/call soft cap, alert at 80%.
  Prepaid / manual top-up only — NO bank auto-recharge.

What is NOT implemented here (outline only, needs the live gate):
- The Pipecat pipeline body (transport/STT/LLM/TTS wiring) — guarded
  behind PIPELINE_AVAILABLE exactly like server.py, so importing this
  file never dials or connects to any provider.
- The Telnyx media-stream transport (websocket attach by
  call_control_id / client_state hint through the canonical adapter;
  session id is NEVER used alone to attach a stream).
- Actual provider SDK calls (OpenAI STT / LLM / ElevenLabs) — the
  Brain classes below are interfaces; the deploy step binds them to
  provider clients built from credential *references*.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Escalation bounds (Nathan-approved ladder, Oct 29, 2026)
# ---------------------------------------------------------------------------
FRONTIER_MAX_TURNS = 3
FRONTIER_MAX_SECONDS = 120
TEST_CALL_BUDGET_CENTS = 100  # $1.00 for the first test call
MONTHLY_CAP_CENTS = 2000      # $20.00, first-pack ledger cap
PER_CALL_SOFT_CAP_CENTS = 500  # $5.00, first-pack soft cap
MONTHLY_ALERT_FRACTION = 0.80

DISCLOSURE_PREFIX = "Hi, this is Nathan's assistant calling"
AI_IDENTITY_ANSWER = (
    "I'm Nathan's AI assistant, calling for him. I can keep going, "
    "or I can bring Nathan in if you'd prefer to talk to him directly."
)

# Cheap pattern backstop ported from the archived adapter's job guard.
# This is a backstop, NOT a guarantee: the live brain must still refuse
# sensitive data in conversation, and the job approval (canonical
# adapter) is the binding gate.
FORBIDDEN_PATTERNS = (
    r"\b\d{3}-\d{2}-\d{4}\b",  # SSN-shaped
    r"\b\d{13,19}\b",          # card-number-shaped digit run
)

# Triggers that move the call UP the ladder (fast -> frontier).
# Mirrors call_job.LOOP_IN_TRIGGERS, minus the ones that are already
# terminal (voicemail / max duration end the call, they don't escalate).
FRONTIER_TRIGGERS = (
    "repeated misunderstanding",
    "decision outside job",
    "sensitive data requested",
    "caller upset or escalating",
    "unresolvable phone tree",
    "refused to talk to assistant",
)


def validate_job_script(script: str) -> None:
    """Guard ported from the archived adapter (telnyx-adapter-work).

    Raises ValueError if the job script lacks the disclosure opening
    or trips a forbidden-content pattern. The canonical adapter's
    per-job Approval remains the binding gate; this is the pipeline's
    own second check before any media flows.
    """
    if script != "Hi, this is Nathan's assistant calling on his behalf.":
        raise ValueError("opening must exactly match the approved disclosure")
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, script):
            raise ValueError("script trips a forbidden-content pattern")


# ---------------------------------------------------------------------------
# Credential references ONLY — never values. Resolved via Secure Vault /
# environment at deploy time by the parent, never in chat or files.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PipelineCredentialRefs:
    telnyx_api_key_ref: str = "TELNYX_API_KEY"
    telnyx_webhook_public_key_ref: str = "TELNYX_WEBHOOK_PUBLIC_KEY"
    telnyx_connection_id_ref: str = "TELNYX_CONNECTION_ID"  # from app creation
    openai_stt_api_key_ref: str = "OPENAI_API_KEY"        # STT (streaming, OpenAI)
    # Deepgram DROPPED Oct 29, 2026 ("screw deepgram"): fresh key + correct
    # Token header still 401s through the vault path, so Deepgram is out.
    # Do NOT re-add a Deepgram ref without a new provisioning mechanism.
    fast_llm_api_key_ref: str = "FAST_LLM_API_KEY"          # live brain
    frontier_llm_api_key_ref: str = "FRONTIER_LLM_API_KEY"  # escalation brain
    elevenlabs_api_key_ref: str = "ELEVENLABS_API_KEY"      # TTS voice
    # Voice identity is NOT a secret; it is the current user pick.
    elevenlabs_voice_id: str = "cgSgspJ2msm6clMCkdW9"  # Jessica, Nathan's pick Oct 29, 2026 (Natasha id not on this account)
    elevenlabs_voice_name: str = "Natasha"


# ---------------------------------------------------------------------------
# Escalation state machine (pure logic, no provider calls — unit-testable)
# ---------------------------------------------------------------------------
@dataclass
class EscalationState:
    stage: str = "fast"  # fast -> frontier -> nathan -> ended
    frontier_turns: int = 0
    frontier_started_at: float | None = None
    spent_cents: int = 0
    clock: object = field(default=time.time, repr=False)  # callable returning now

    def frontier_budget_remaining(self) -> dict:
        """Returns what the frontier try still has left. Empty means done."""
        if self.stage != "frontier":
            return {"turns": FRONTIER_MAX_TURNS, "seconds": FRONTIER_MAX_SECONDS}
        elapsed = (self.clock() - self.frontier_started_at) if self.frontier_started_at else 0
        return {
            "turns": max(0, FRONTIER_MAX_TURNS - self.frontier_turns),
            "seconds": max(0, FRONTIER_MAX_SECONDS - int(elapsed)),
        }

    def escalate_to_frontier(self, trigger: str) -> str:
        """Fast brain is stuck: hand the call to the frontier brain."""
        if trigger not in FRONTIER_TRIGGERS:
            raise ValueError(f"not a frontier trigger: {trigger}")
        if self.stage != "fast":
            raise ValueError(f"cannot escalate to frontier from stage {self.stage}")
        self.stage = "frontier"
        self.frontier_turns = 0
        self.frontier_started_at = self.clock()
        return "frontier"

    def frontier_reply(self) -> str:
        """Record one frontier reply. Returns the stage after the reply."""
        if self.stage != "frontier":
            raise ValueError(f"no frontier try in progress (stage {self.stage})")
        self.frontier_turns += 1
        remaining = self.frontier_budget_remaining()
        if remaining["turns"] <= 0 or remaining["seconds"] <= 0:
            self.stage = "nathan"
        return self.stage

    def frontier_exhausted(self) -> bool:
        if self.stage != "frontier":
            return self.stage == "nathan"
        remaining = self.frontier_budget_remaining()
        return remaining["turns"] <= 0 or remaining["seconds"] <= 0

    def loop_in_nathan(self) -> str:
        """Frontier also failed (or caller asked for Nathan): loop him in."""
        if self.stage not in ("frontier", "fast"):
            raise ValueError(f"cannot loop in Nathan from stage {self.stage}")
        self.stage = "nathan"
        return "nathan"

    def check_budget(self, projected_total_cents: int, test_call: bool = False) -> str:
        """Cost guard: 'ok', 'test_budget_exceeded', or 'soft_cap'.

        A test call stops at TEST_CALL_BUDGET_CENTS ($1.00); a live
        business call stops at the per-call soft cap ($5.00) unless
        Nathan explicitly approved more for that exact job. Neither
        threshold is a spend authorisation on its own — the canonical
        adapter's Approval + monthly ledger cap are the binding gates.
        """
        if test_call and projected_total_cents > TEST_CALL_BUDGET_CENTS:
            return "test_budget_exceeded"
        if projected_total_cents > PER_CALL_SOFT_CAP_CENTS:
            return "soft_cap"
        return "ok"


# ---------------------------------------------------------------------------
# Pipecat pipeline outline (NOT wired — mirrors server.py's guard)
# ---------------------------------------------------------------------------
try:
    import pipecat  # noqa: F401  (deploy machine only; not installed here)
    PIPELINE_AVAILABLE = True
except Exception:
    PIPELINE_AVAILABLE = False


def run_pipeline(call_job: dict, credential_refs: PipelineCredentialRefs) -> str:
    """Outline only. Returns a status string; never dials.

    Deploy steps before this can run (see docs/WIRING_PREP_2026-10-29.md):
    1. pip install pipecat-ai + provider extras on the deploy machine.
    2. Resolve every credential ref above via Secure Vault (values
       never in this file).
    3. Create the Telnyx Call Control app, assign +1 321 497 8649,
       set the webhook URL, and record the connection_id.
    4. Attach the media stream by call_control_id + client_state hint
       through the canonical adapter — never by session id alone.
    """
    validate_job_script(call_job.get("opening", ""))
    if not PIPELINE_AVAILABLE:
        return "stubbed: pipecat not installed on this machine"
    return "stubbed: provider clients not bound (deploy step)"
