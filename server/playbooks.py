"""Playbooks: per-objective conversation shapes, as data.

A playbook is a JSON file in the repo's playbooks/ directory (see
docs/PERSONA_MEMORY_SPEC.md §7): goal, the facts to collect in
order, success/end conditions, allowed decisions, and hand-back
triggers specific to that kind of call. The control plane selects
one per call — explicitly (--playbook) or by objective keywords —
and the frozen brief carries its flow into the system prompt and
the repetition guard's progression choices.

Loading is fail-open by design: an unknown or malformed playbook
resolves to None, and the call proceeds on objective + persona
alone, exactly as it did before playbooks existed.
"""
from __future__ import annotations

import json
from pathlib import Path

PLAYBOOK_DIR = Path(__file__).resolve().parent.parent / "playbooks"

_REQUIRED = ("name", "title", "goal", "collect", "success_conditions",
             "end_conditions", "loop_in_triggers", "allowed_decisions")

# Keyword inference from the objective text, checked in order.
# Deliberately narrow: an ambiguous objective gets no playbook
# rather than the wrong one.
_INFERENCE = (
    ("follow_up_status", ("follow up", "follow-up", "followup",
                          "check on the status", "status of",
                          "checking in on")),
    ("appointment_scheduling", ("appointment", "schedule", "book a",
                                "booking", "reschedule", "evaluation")),
    ("information_gathering", ("find out", "ask about", "ask whether",
                               "confirm whether", "gather", "what is the",
                               "how much is", "get the")),
)


def load_playbook(name: str, directory: Path | None = None) -> dict | None:
    """Load and minimally validate one playbook by name; None if the
    name is empty, unknown, unreadable, or malformed."""
    if not name or not isinstance(name, str):
        return None
    safe = Path(name).name  # never let a name escape the directory
    path = (directory or PLAYBOOK_DIR) / f"{safe}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("name") != safe:
        return None
    if any(key not in data for key in _REQUIRED):
        return None
    collect = data.get("collect")
    if not isinstance(collect, list) or not collect:
        return None
    for item in collect:
        if not isinstance(item, dict) \
                or not all(k in item for k in ("slot", "question", "keywords")):
            return None
    return data


def available_playbooks(directory: Path | None = None) -> list[str]:
    base = directory or PLAYBOOK_DIR
    try:
        return sorted(p.stem for p in base.glob("*.json"))
    except Exception:
        return []


def select_playbook(explicit: str = "", objective: str = "",
                    directory: Path | None = None) -> dict | None:
    """Explicit selection wins; otherwise infer from the objective;
    otherwise None (the call runs on objective + persona alone)."""
    if explicit:
        return load_playbook(explicit, directory)
    lowered = (objective or "").lower()
    for name, keywords in _INFERENCE:
        if any(word in lowered for word in keywords):
            return load_playbook(name, directory)
    return None


def playbook_prompt_section(playbook: dict) -> str:
    """The playbook's contribution to the frozen brief."""
    lines = [f"Playbook: {playbook['title']}",
             f"Goal of this call: {playbook['goal']}",
             "Work through these items in order, one at a time:"]
    for i, item in enumerate(playbook["collect"], 1):
        lines.append(f"{i}. ({item['slot']}) {item['question']}")
    lines.append("This call is a success when: "
                 + "; ".join(playbook["success_conditions"]))
    lines.append("End the call when: "
                 + "; ".join(playbook["end_conditions"]))
    if playbook["loop_in_triggers"]:
        lines.append("Hand back to Nathan immediately if: "
                     + "; ".join(playbook["loop_in_triggers"]))
    return "\n".join(lines)
