"""Control plane — the System-One-style fast controller.

Keeps the big conversational model off the memory critical path
(docs/PERSONA_MEMORY_SPEC.md §4):

- build_brief() runs ONCE before the call: persona + profile slice
  + playbook + end conditions, frozen into a CallBrief the pipeline
  consumes. Fail-open: any failure returns a persona-only brief.
- TurnRouter routes between turns from the brief and the
  conversation tracker: pure rules, no I/O, no model calls, so its
  worst case is microseconds and it can never add dead air.
- extract_items() + writeback() run after the call: a deterministic
  typer classifies transcript items (fact / preference / commitment
  / counterparty note) and writes them to the profile store with
  episode provenance; the call record surfaces what landed.

The typing seat sits behind ControllerDecider so the hosted Jev
model (TypeSafe AI) can replace the rule-based typer later with
only an adapter — see the decision brief in the spec. The shipped
decider is RuleBasedDecider; HostedJevDecider is an inert stub that
raises with the provisioning steps and performs no network I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import persona
import playbooks as playbooks_mod
from conversation_state import ConversationTracker

EXTRACTION_PROVENANCE = "post-call extraction (rule-based typer)"


# ---------------------------------------------------------------- brief

@dataclass(frozen=True)
class CallBrief:
    persona_core: str
    hand_back_rules: str
    playbook: dict | None
    profile_text: str
    end_conditions: tuple[str, ...]
    counterparty_name: str = ""

    def context_sections(self) -> list[str]:
        """Playbook, profile background, and end conditions — the
        per-call sections (persona and hand-back rules lead)."""
        sections = []
        if self.playbook:
            sections.append(
                playbooks_mod.playbook_prompt_section(self.playbook))
        if self.profile_text:
            sections.append(self.profile_text)
        if self.end_conditions:
            sections.append(
                "End conditions for this call: "
                + "; ".join(self.end_conditions)
                + ". When one is met, thank the person, say goodbye "
                  "plainly, and stop.")
        return sections

    def prompt_sections(self) -> list[str]:
        return [self.persona_core, self.hand_back_rules] \
            + self.context_sections()


def build_brief(job, store=None, playbook=None) -> CallBrief:
    """Assemble the frozen pre-call brief. Never raises."""
    try:
        chosen = playbook
        if chosen is None:
            chosen = playbooks_mod.select_playbook(
                getattr(job, "playbook", "") or "",
                getattr(job, "objective", "") or "")
        profile_text = ""
        if store is not None:
            counterparty = getattr(job, "counterparty", "") \
                or getattr(job, "business", "") or ""
            profile_text = store.get_profile_slice(
                counterparty or None,
                getattr(job, "objective", "") or "")
        end_conditions: list[str] = []
        if chosen:
            end_conditions.extend(chosen.get("end_conditions", []))
        return CallBrief(
            persona_core=persona.PERSONA_CORE,
            hand_back_rules=persona.HAND_BACK_RULES,
            playbook=chosen,
            profile_text=profile_text,
            end_conditions=tuple(end_conditions),
            counterparty_name=getattr(job, "counterparty", "")
            or getattr(job, "business", "") or "")
    except Exception:
        return CallBrief(persona_core=persona.PERSONA_CORE,
                         hand_back_rules=persona.HAND_BACK_RULES,
                         playbook=None, profile_text="",
                         end_conditions=())


# ---------------------------------------------------------------- router

@dataclass(frozen=True)
class RouteDecision:
    action: str  # "continue" | "wrap_up"
    reason: str = ""


class TurnRouter:
    """Between-turn routing from the frozen brief + tracker.

    Pure and synchronous by contract — the pipeline calls it inline
    and trusts it to be effectively free.
    """

    def __init__(self, brief: CallBrief, tracker: ConversationTracker):
        self.brief = brief
        self.tracker = tracker

    def after_user_turn(self, user_text: str) -> RouteDecision:
        self.tracker.observe_user(user_text)
        playbook = self.brief.playbook
        if playbook:
            slots = {item["slot"] for item in playbook["collect"]}
            if slots and slots <= self.tracker.slots_filled:
                return RouteDecision(
                    "wrap_up", "all playbook items collected")
        return RouteDecision("continue")


# ---------------------------------------------------------------- decider

class ControllerDecider:
    """The typing seat. Implementations classify transcript text
    into typed memory items; they never generate call prose."""

    def type_items(self, transcript, brief: CallBrief) -> list[dict]:
        raise NotImplementedError


class RuleBasedDecider(ControllerDecider):
    """The shipped decider: deterministic patterns over the final
    transcript. Conservative by design — it files only what the
    patterns pin down, because a wrong memory outlives the call."""

    def type_items(self, transcript, brief: CallBrief) -> list[dict]:
        return extract_items(transcript,
                             counterparty=brief.counterparty_name)


class HostedJevDecider(ControllerDecider):
    """Drop-in point for TypeSafe AI's hosted Jev model.

    NOT provisioned. Enabling it requires, from Nathan personally:
    (1) he creates the TypeSafe/Jev account himself;
    (2) he creates an API key and stores it through the Secure
        Vault / credential-file flow — never in chat or in code;
    (3) he explicitly approves sending profile slices and call
        transcripts to the hosted API, knowing profile data then
        leaves this machine.
    Until all three exist, construction refuses loudly rather than
    silently falling back — a privacy boundary should fail closed
    at configuration time (call-time use still fails open to the
    rule-based decider in writeback()).
    """

    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "HostedJevDecider is not provisioned. Nathan must (1) "
            "create a TypeSafe/Jev account, (2) store an API key via "
            "the Secure Vault flow, and (3) explicitly approve "
            "profile/transcript egress to the hosted API. See "
            "docs/PERSONA_MEMORY_SPEC.md §8.")


# ---------------------------------------------------------------- typer

_MONTHS = (r"january|february|march|april|may|june|july|august|"
           r"september|october|november|december")
_DUE_RE = re.compile(
    r"\bby\s+((?:mon|tues|wednes|thurs|fri|satur|sun)day|tomorrow|"
    r"next\s+\w+|end\s+of\s+(?:the\s+)?\w+|" + _MONTHS +
    r"\s+\d{1,2}(?:st|nd|rd|th)?|\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b",
    re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

_COMMITMENT_PATTERNS = (
    # (owed_by, pattern) — group 1 is the promise text.
    ("counterparty", re.compile(
        r"\b(?:we(?:'ll| will)|i(?:'ll| will)|our office will|"
        r"i promise to|we promise to)\s+(.+)", re.IGNORECASE)),
    ("assistant", re.compile(
        r"\b(?:i(?:'ll| will) (?:have nathan|let nathan|make sure "
        r"nathan|pass that along|send|email|note)|nathan will)\s+(.+)",
        re.IGNORECASE)),
)

_PREFERENCE_PATTERNS = (
    re.compile(r"\b(?:i prefer|we prefer|please (?:call|email) (?:me|us)|"
               r"best (?:way|time) to reach (?:me|us) is|"
               r"call me at|reach me at)\b.*", re.IGNORECASE),
)

_FACT_PATTERNS = (
    # (predicate, pattern with group 1 = value)
    ("office_hours", re.compile(
        r"\bour (?:office )?hours are\s+(.+)", re.IGNORECASE)),
    ("location", re.compile(
        r"\bwe(?:'re| are) located (?:at|in)\s+(.+)", re.IGNORECASE)),
    ("contact_name", re.compile(
        r"\bmy name is\s+([A-Z][a-z]+(?: [A-Z][a-z]+)?)", re.IGNORECASE)),
    ("phone", re.compile(
        r"\bour (?:number|phone) is\s+([\d\s().-]+)", re.IGNORECASE)),
)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split((text or "").strip())
            if s.strip()]


def _due_of(sentence: str) -> str:
    match = _DUE_RE.search(sentence)
    return match.group(0) if match else ""


def extract_items(transcript, counterparty: str = "") -> list[dict]:
    """Type a final transcript into memory items.

    Counterparty sentences can yield commitments, preferences, and
    facts; assistant sentences yield only assistant commitments (the
    things Nathan is now on the hook for). Assistant statements of
    fact are never stored as facts — the assistant is a fallible
    narrator of its own calls.
    """
    items: list[dict] = []
    seen: set[tuple] = set()

    def add(key, item):
        if key not in seen:
            seen.add(key)
            items.append(item)

    for turn in transcript or []:
        role = turn.get("role")
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        for sentence in _sentences(text):
            for owed_by, pattern in _COMMITMENT_PATTERNS:
                if owed_by == "counterparty" and role != "user":
                    continue
                if owed_by == "assistant" and role != "assistant":
                    continue
                match = pattern.search(sentence)
                if match:
                    promise = match.group(1).strip().rstrip(".")
                    add(("commitment", owed_by, promise.lower()),
                        {"type": "commitment", "owed_by": owed_by,
                         "text": promise,
                         "due_text": _due_of(sentence)})
            if role != "user":
                continue
            for pattern in _PREFERENCE_PATTERNS:
                if pattern.search(sentence):
                    add(("preference", sentence.lower()),
                        {"type": "preference", "value": sentence})
                    break
            for predicate, pattern in _FACT_PATTERNS:
                match = pattern.search(sentence)
                if match:
                    add(("fact", predicate, match.group(1).lower()),
                        {"type": "fact", "entity": counterparty,
                         "predicate": predicate,
                         "value": match.group(1).strip().rstrip(".")})
    return items


# ---------------------------------------------------------------- writeback

def summarize_episode(job, record: dict) -> str:
    outcome = record.get("outcome", "unknown")
    turns = len(record.get("transcript") or [])
    playbook = getattr(job, "playbook", "") or ""
    objective = (getattr(job, "objective", "") or "")[:160]
    parts = [f"Outcome: {outcome}.", f"Turns: {turns}."]
    if playbook:
        parts.append(f"Playbook: {playbook}.")
    if objective:
        parts.append(f"Objective: {objective}")
    return " ".join(parts)


def writeback(job, record: dict, store, decider: ControllerDecider | None = None,
              episode_ref: str = "") -> dict:
    """Post-call extraction + storage. Fail-open: returns a summary
    for the call record either way, and never raises."""
    try:
        decider = decider or RuleBasedDecider()
        brief_stub = CallBrief(
            persona_core="", hand_back_rules="", playbook=None,
            profile_text="", end_conditions=(),
            counterparty_name=getattr(job, "counterparty", "")
            or getattr(job, "business", "") or "")
        counterparty = brief_stub.counterparty_name
        if counterparty:
            # First contact creates the entity; without this, facts
            # about a new counterparty have nowhere to land.
            store.get_or_create_entity("business", counterparty)
        episode_id = store.record_episode(
            ref=episode_ref or f"call:{record.get('attempt_id', 'unknown')}",
            counterparty=counterparty or None,
            summary=summarize_episode(job, record),
            transcript_ref=record.get("transcript_ref", ""),
            provenance=EXTRACTION_PROVENANCE)
        items = decider.type_items(record.get("transcript") or [], brief_stub)
        stored = store.extract_and_store(
            items, episode_id=episode_id, counterparty=counterparty or None,
            provenance=EXTRACTION_PROVENANCE)
        return {"episode_id": episode_id, "items_typed": len(items),
                "stored": stored, "decider": type(decider).__name__}
    except Exception as exc:  # a memory failure must not fail the call
        return {"error": f"{type(exc).__name__}: {exc}"}
