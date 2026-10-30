"""Dynamic model selection and cost ledger — NO hard-pinned models.

"Dynamic always" (Nathan, Oct 29, 2026) means: no model name is baked
into pipeline code. Pools come from environment variables at call-build
time, and the resolver picks the first pool entry the provider actually
offers (when an available-model list is supplied) or the first pool
entry otherwise. An empty pool fails closed — the call does not build
— rather than silently falling back to an invented default.

Environment pools (comma-separated, set at deploy time by the parent):
  GEMINI_FAST_MODELS      fast brain pool (live talker)
  GEMINI_FRONTIER_MODELS  frontier brain pool (escalation)
  GROQ_STT_MODELS         request-based Whisper transcription pool
  ELEVENLABS_TTS_MODELS   TTS model pool (optional; empty = service default)

Cost rates below are ESTIMATES from the Oct 29 wiring doc (published-
rate arithmetic, not provider quotes). Measured provider metering at
call time governs; the ledger's job is to stop the call BEFORE the
caps, not to bill precisely. Caps (Nathan-approved): $1.00 hard budget
for the first test call, $5.00/call soft cap, $20.00/month ledger cap.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

TEST_CALL_BUDGET_CENTS = 100
PER_CALL_SOFT_CAP_CENTS = 500
MONTHLY_CAP_CENTS = 2000
MONTHLY_ALERT_FRACTION = 0.80

# Estimates only — see module docstring. Units: cents.
RATE_TELNYX_PER_MINUTE_CENTS = 0.7       # ~$0.007/min outbound voice
RATE_TELNYX_MEDIA_PER_MINUTE_CENTS = 0.35  # ~$0.0035/min media streaming
RATE_STT_PER_MINUTE_CENTS = 0.0          # Groq free-tier monetary estimate
RATE_TTS_PER_1000_CHARS_CENTS = 5.0      # ElevenLabs Flash API ~$0.05/1k
RATE_LLM_PER_1000_TOKENS_CENTS = 0.0     # Gemini free-tier monetary estimate
RATE_FRONTIER_PER_1000_TOKENS_CENTS = 0.0  # Gemini free-tier monetary estimate


def parse_pool(csv_value: str | None) -> list[str]:
    if not csv_value:
        return []
    seen: list[str] = []
    for item in csv_value.split(","):
        item = item.strip()
        if item and item not in seen:
            seen.append(item)
    return seen


def resolve_model(role: str, pool: list[str], available: list[str] | None = None) -> str:
    """Pick a model for `role` from `pool`. Fail closed on empty pool.

    When `available` (a provider model list, e.g. from a free
    GET /v1/models probe at deploy time) is given, the first pool entry
    present in it wins; if none is present, resolution fails rather
    than dialling with an unlisted model.
    """
    if not pool:
        raise ValueError(f"no {role} model pool configured; refusing to build call")
    if available is None:
        return pool[0]
    available_set = set(available)
    for candidate in pool:
        if candidate in available_set:
            return candidate
    raise ValueError(
        f"no {role} pool model is offered by the provider "
        f"(pool={pool}, offered={sorted(available_set)[:20]}...)")

def resolve_models_from_env(available: dict[str, list[str]] | None = None) -> dict[str, str]:
    """Resolve fast/frontier/STT models from environment pools."""
    return {
        "fast": resolve_model("fast", parse_pool(os.environ.get("GEMINI_FAST_MODELS")),
                              available.get("gemini") if available else None),
        "frontier": resolve_model("frontier", parse_pool(os.environ.get("GEMINI_FRONTIER_MODELS")),
                                  available.get("gemini") if available else None),
        "stt": resolve_model("stt", parse_pool(os.environ.get("GROQ_STT_MODELS")),
                             available.get("groq") if available else None),
    }


# ---------------------------------------------------------------------------
# Cost ledger + transcript log (JSONL, one record per call).
# ---------------------------------------------------------------------------

@dataclass
class CallCost:
    minutes: float = 0.0
    stt_minutes: float = 0.0
    tts_chars: int = 0
    llm_tokens: int = 0
    frontier_tokens: int = 0

    def total_cents(self) -> float:
        return sum(self.breakdown_cents().values())

    def breakdown_cents(self) -> dict[str, float]:
        return {
            "telnyx_voice_estimate": self.minutes * RATE_TELNYX_PER_MINUTE_CENTS,
            "telnyx_media_stream_estimate": (
                self.minutes * RATE_TELNYX_MEDIA_PER_MINUTE_CENTS),
            "groq_free_tier_estimate": self.stt_minutes * RATE_STT_PER_MINUTE_CENTS,
            "elevenlabs_estimate": (self.tts_chars / 1000.0)
            * RATE_TTS_PER_1000_CHARS_CENTS,
            "gemini_fast_free_tier_estimate": (self.llm_tokens / 1000.0)
            * RATE_LLM_PER_1000_TOKENS_CENTS,
            "gemini_frontier_free_tier_estimate": (self.frontier_tokens / 1000.0)
            * RATE_FRONTIER_PER_1000_TOKENS_CENTS,
        }


@dataclass
class CostLedger:
    """Enforces test budget / soft cap / monthly cap before spending."""
    spent_this_month_cents: float = 0.0
    test_call: bool = False

    def project(self, cost: CallCost) -> str:
        total = cost.total_cents()
        if self.test_call and total > TEST_CALL_BUDGET_CENTS:
            return "test_budget_exceeded"
        if total > PER_CALL_SOFT_CAP_CENTS:
            return "soft_cap"
        if self.spent_this_month_cents + total > MONTHLY_CAP_CENTS:
            return "monthly_cap"
        if self.spent_this_month_cents + total >= MONTHLY_CAP_CENTS * MONTHLY_ALERT_FRACTION:
            return "ok_alert_80_percent"
        return "ok"

    def record(self, path: str, record: dict) -> dict:
        """Append a transcript+cost record (JSONL). Returns the record."""
        entry = redact_record(record)
        entry["recorded_at"] = time.time()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        return entry


# ---------------------------------------------------------------------------
# Outgoing-text guard: the assistant must never speak a forbidden shape.
# Same patterns as voice_pipeline.FORBIDDEN_PATTERNS; applied to every
# assistant reply before it reaches TTS. A trip ends the reply (the
# caller hears a refusal, not the pattern).
# ---------------------------------------------------------------------------
import re as _re

_FORBIDDEN_OUTGOING = (
    _re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    _re.compile(r"\b\d{13,19}\b"),
    _re.compile(r"\b(?:otp|one[- ]time|verification|security)\s*(?:code)?\s*[:#-]?\s*\d{4,8}\b", _re.I),
    _re.compile(r"\b(?:routing|account)\s*(?:number|no\.?|#)?\s*[:#-]?\s*\d{6,17}\b", _re.I),
    _re.compile(r"\bpassword\s*[:=-]\s*\S+", _re.I),
    _re.compile(r"\b(?:authorization|x-goog-api-key|xi-api-key)\s*[:=]\s*(?:bearer\s+)?\S+", _re.I),
    _re.compile(r"\b(?:sk|gsk|AIza)[-_A-Za-z0-9]{16,}\b"),
    _re.compile(r"\bhsurr:[A-Za-z0-9_./+=-]{8,}\b"),
)

REFUSAL_LINE = "I can't share or repeat that kind of information on a call."


def sanitize_outgoing(text: str) -> str:
    """Return text, or the refusal line if it trips a forbidden pattern."""
    for pattern in _FORBIDDEN_OUTGOING:
        if pattern.search(text or ""):
            return REFUSAL_LINE
    return text


def redact_persisted_text(text: str) -> str:
    value = text or ""
    for pattern in _FORBIDDEN_OUTGOING:
        value = pattern.sub("[REDACTED]", value)
    return value


def redact_record(record: dict) -> dict:
    """Redact nested diagnostics too; provider exceptions can contain secrets.

    This is a pattern backstop, not a complete PII classifier. Production logs
    still require access control, retention and approved collection practices.
    """
    def clean(value):
        if isinstance(value, str):
            return redact_persisted_text(value)
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        return value
    entry = clean(record)
    entry.setdefault('transcript', [])
    return entry
