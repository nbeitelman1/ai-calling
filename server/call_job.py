"""Call job schema for Nathan's self-hosted AI calling system.

Scaffold only — no live calls, no credentials in this file.
A job is written by Muse, approved by Nathan, then executed by the
Pipecat server. Anything outside the job = loop-in or end the call.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

FORBIDDEN_TOPICS = (
    "SSN",
    "card numbers",
    "bank account numbers",
    "one-time codes",
    "passwords",
    "background-check consent",
    "legal-attestation signatures",
    "payment authorisation without explicit per-call approval",
)

LOOP_IN_TRIGGERS = (
    "human requested",
    "asked if AI",
    "refused to talk to assistant",
    "decision outside job",
    "sensitive data requested",
    "repeated misunderstanding",
    "max duration reached",
    "caller upset or escalating",
    "voicemail or unresolvable phone tree",
)

OPENING_TEMPLATE = (
    "Hi, this is Nathan's assistant calling on his behalf about {objective}. "
    "Is now an okay time to talk through that?"
)

AI_IDENTITY_ANSWER = (
    "I'm Nathan's AI assistant, calling for him. I can keep going, "
    "or I can bring Nathan in if you'd prefer to talk to him directly."
)


@dataclass
class CallJob:
    to: str
    business: str
    objective: str
    allowed_decisions: list[str] = field(default_factory=list)
    max_minutes: int = 10
    opening: str = ""

    def __post_init__(self) -> None:
        if not self.opening:
            self.opening = OPENING_TEMPLATE.format(objective=self.objective)

    def to_json(self) -> str:
        return json.dumps(
            {
                "to": self.to,
                "business": self.business,
                "objective": self.objective,
                "allowed_decisions": self.allowed_decisions,
                "forbidden": list(FORBIDDEN_TOPICS),
                "loop_in_triggers": list(LOOP_IN_TRIGGERS),
                "max_minutes": self.max_minutes,
                "opening": self.opening,
            },
            indent=2,
        )


def init_db(db_path: Path) -> sqlite3.Connection:
    """Local transcript + wallet ledger. No secrets stored here."""
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS calls (
            id INTEGER PRIMARY KEY,
            to_number TEXT, business TEXT, objective TEXT,
            outcome TEXT, transcript_path TEXT,
            minutes REAL, estimated_cost_usd REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS wallet (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            monthly_cap_usd REAL DEFAULT 20.0,
            spent_this_month_usd REAL DEFAULT 0.0
        );
        INSERT OR IGNORE INTO wallet (id) VALUES (1);
        """
    )
    conn.commit()
    return conn
