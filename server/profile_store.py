"""Temporal profile store — the calling assistant's memory plane.

Implements the design in docs/PERSONA_MEMORY_SPEC.md §3: Graphiti's
temporal-knowledge-graph model reduced to what a single-assistant,
single-user system needs, on SQLite.

- entities: people, businesses, places, topics, and Nathan himself,
  resolvable by canonical name or alias (phone numbers are aliases).
- facts: (entity, predicate, value) with valid_from / valid_to and a
  superseded_by link. Storing a new value for an existing
  (entity, predicate) closes the old row's validity window instead
  of deleting it — the store knows what is true now AND what was
  true then. Every fact carries provenance and an episode link.
- episodes: one row per meaningful event (a call, a seeding batch),
  the audit trail behind facts and commitments.
- commitments: promises with an owner, the counterparty they bind,
  the promise text, the due string as spoken, and a lifecycle state
  (open -> done / dropped / superseded).

The store is deliberately boring: synchronous SQLite, no network, no
model calls. All judgment about WHAT to store lives in the control
plane (call_controller.py); this module only keeps it faithfully.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS entity_aliases (
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    alias TEXT NOT NULL,
    PRIMARY KEY (entity_id, alias)
);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY,
    ref TEXT NOT NULL UNIQUE,
    counterparty_id INTEGER REFERENCES entities(id),
    summary TEXT NOT NULL DEFAULT '',
    transcript_ref TEXT NOT NULL DEFAULT '',
    occurred_at REAL NOT NULL,
    provenance TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    predicate TEXT NOT NULL,
    value TEXT NOT NULL,
    valid_from REAL NOT NULL,
    valid_to REAL,
    superseded_by INTEGER REFERENCES facts(id),
    provenance TEXT NOT NULL DEFAULT '',
    episode_id INTEGER REFERENCES episodes(id),
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS facts_current
    ON facts(entity_id, predicate, valid_to);
CREATE TABLE IF NOT EXISTS commitments (
    id INTEGER PRIMARY KEY,
    owed_by TEXT NOT NULL,
    counterparty_id INTEGER REFERENCES entities(id),
    text TEXT NOT NULL,
    due_text TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'open',
    episode_id INTEGER REFERENCES episodes(id),
    provenance TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    closed_at REAL
);
"""

COMMITMENT_STATES = ("open", "done", "dropped", "superseded")


def default_profile_db_path() -> Path:
    env = os.environ.get("AI_CALLING_PROFILE_DB", "").strip()
    if env:
        return Path(env)
    log_dir = os.environ.get("AI_CALLING_LOG_DIR", "").strip()
    if log_dir:
        return Path(log_dir) / "profile.db"
    return Path(__file__).resolve().parent.parent / "logs" / "profile.db"


class ProfileStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_profile_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self):
        self._conn.close()

    def _tx(self):
        return self._conn

    # -- entities -----------------------------------------------------

    def get_or_create_entity(self, kind: str, name: str,
                             aliases=(), now: float | None = None) -> int:
        now = time.time() if now is None else now
        found = self.resolve_entity(name)
        if found is None:
            for alias in aliases:
                found = self.resolve_entity(alias)
                if found is not None:
                    break
        if found is None:
            cur = self._tx().execute(
                "INSERT INTO entities(kind, name, created_at) VALUES(?,?,?)",
                (kind, name, now))
            found = cur.lastrowid
        for alias in aliases:
            self._tx().execute(
                "INSERT OR IGNORE INTO entity_aliases(entity_id, alias) "
                "VALUES(?,?)", (found, alias))
        # The canonical name is always an alias of itself.
        self._tx().execute(
            "INSERT OR IGNORE INTO entity_aliases(entity_id, alias) "
            "VALUES(?,?)", (found, name))
        self._tx().commit()
        return found

    def resolve_entity(self, key: str) -> int | None:
        """Resolve by canonical name or any alias (case-insensitive)."""
        if not key:
            return None
        row = self._tx().execute(
            "SELECT entity_id FROM entity_aliases WHERE lower(alias)=lower(?)",
            (key,)).fetchone()
        return row["entity_id"] if row else None

    def entity_name(self, entity_id: int) -> str:
        row = self._tx().execute(
            "SELECT name FROM entities WHERE id=?", (entity_id,)).fetchone()
        return row["name"] if row else f"entity:{entity_id}"

    # -- episodes -----------------------------------------------------

    def record_episode(self, ref: str, counterparty=None, summary: str = "",
                       transcript_ref: str = "", provenance: str = "",
                       occurred_at: float | None = None) -> int:
        """Idempotent on ref: re-recording returns the existing id."""
        occurred_at = time.time() if occurred_at is None else occurred_at
        counterparty_id = None
        if counterparty:
            counterparty_id = counterparty if isinstance(counterparty, int) \
                else self.resolve_entity(counterparty)
        row = self._tx().execute(
            "SELECT id FROM episodes WHERE ref=?", (ref,)).fetchone()
        if row:
            return row["id"]
        cur = self._tx().execute(
            "INSERT INTO episodes(ref, counterparty_id, summary, "
            "transcript_ref, occurred_at, provenance) VALUES(?,?,?,?,?,?)",
            (ref, counterparty_id, summary, transcript_ref, occurred_at,
             provenance))
        self._tx().commit()
        return cur.lastrowid

    # -- facts ----------------------------------------------------------

    def add_fact(self, entity, predicate: str, value: str,
                 provenance: str = "", episode_id: int | None = None,
                 now: float | None = None) -> int:
        """Store a fact, superseding the current value for the same
        (entity, predicate). The superseded row keeps its history:
        valid_to is closed and superseded_by points at the new row."""
        now = time.time() if now is None else now
        entity_id = entity if isinstance(entity, int) \
            else self.resolve_entity(entity)
        if entity_id is None:
            raise ValueError(f"unknown entity: {entity!r}")
        value = str(value).strip()
        if not value:
            raise ValueError("fact value must be nonempty")
        current = self._tx().execute(
            "SELECT id, value FROM facts WHERE entity_id=? AND predicate=? "
            "AND valid_to IS NULL", (entity_id, predicate)).fetchone()
        if current and current["value"] == value:
            return current["id"]
        cur = self._tx().execute(
            "INSERT INTO facts(entity_id, predicate, value, valid_from, "
            "valid_to, superseded_by, provenance, episode_id, created_at) "
            "VALUES(?,?,?,?,NULL,NULL,?,?,?)",
            (entity_id, predicate, value, now, provenance, episode_id, now))
        new_id = cur.lastrowid
        if current:
            self._tx().execute(
                "UPDATE facts SET valid_to=?, superseded_by=? WHERE id=?",
                (now, new_id, current["id"]))
        self._tx().commit()
        return new_id

    def current_facts(self, entity) -> list[dict]:
        entity_id = entity if isinstance(entity, int) \
            else self.resolve_entity(entity)
        if entity_id is None:
            return []
        rows = self._tx().execute(
            "SELECT predicate, value, valid_from, provenance FROM facts "
            "WHERE entity_id=? AND valid_to IS NULL ORDER BY predicate",
            (entity_id,)).fetchall()
        return [dict(r) for r in rows]

    def fact_history(self, entity, predicate: str) -> list[dict]:
        """All values a (entity, predicate) has ever held, oldest first."""
        entity_id = entity if isinstance(entity, int) \
            else self.resolve_entity(entity)
        if entity_id is None:
            return []
        rows = self._tx().execute(
            "SELECT value, valid_from, valid_to, superseded_by FROM facts "
            "WHERE entity_id=? AND predicate=? ORDER BY valid_from",
            (entity_id, predicate)).fetchall()
        return [dict(r) for r in rows]

    # -- commitments ----------------------------------------------------

    def add_commitment(self, owed_by: str, text: str, counterparty=None,
                       due_text: str = "", provenance: str = "",
                       episode_id: int | None = None,
                       now: float | None = None) -> int:
        now = time.time() if now is None else now
        counterparty_id = None
        if counterparty:
            counterparty_id = counterparty if isinstance(counterparty, int) \
                else self.resolve_entity(counterparty)
        cur = self._tx().execute(
            "INSERT INTO commitments(owed_by, counterparty_id, text, "
            "due_text, state, episode_id, provenance, created_at) "
            "VALUES(?,?,?,?,'open',?,?,?)",
            (owed_by, counterparty_id, text.strip(), due_text, episode_id,
             provenance, now))
        self._tx().commit()
        return cur.lastrowid

    def set_commitment_state(self, commitment_id: int, state: str,
                             now: float | None = None) -> None:
        if state not in COMMITMENT_STATES:
            raise ValueError(f"unknown commitment state: {state!r}")
        now = time.time() if now is None else now
        closed_at = None if state == "open" else now
        self._tx().execute(
            "UPDATE commitments SET state=?, closed_at=? WHERE id=?",
            (state, closed_at, commitment_id))
        self._tx().commit()

    def open_commitments(self, counterparty=None) -> list[dict]:
        sql = ("SELECT c.id, c.owed_by, c.text, c.due_text, c.created_at, "
               "e.name AS counterparty FROM commitments c "
               "LEFT JOIN entities e ON e.id = c.counterparty_id "
               "WHERE c.state='open'")
        params: list = []
        if counterparty:
            cid = counterparty if isinstance(counterparty, int) \
                else self.resolve_entity(counterparty)
            sql += " AND c.counterparty_id=?"
            params.append(cid)
        sql += " ORDER BY c.created_at"
        return [dict(r) for r in self._tx().execute(sql, params).fetchall()]

    # -- extraction writeback --------------------------------------------

    def extract_and_store(self, items, episode_id: int | None = None,
                          counterparty=None, provenance: str = "",
                          now: float | None = None) -> dict:
        """Store typed items produced by the control plane's typer.

        Item shapes:
          {"type": "fact", "entity": name|id, "predicate": str,
           "value": str}
          {"type": "preference", "entity": ..., "value": str}
             -> fact under predicate "preference"
          {"type": "commitment", "owed_by": str, "text": str,
           "due_text": str (optional)}
          {"type": "counterparty_note", "value": str}
             -> fact on the counterparty entity, predicate "note"
                (notes accumulate: each note supersedes nothing, so
                they are stored under a unique predicate suffix)

        Returns a summary of what landed, for the call record.
        """
        now = time.time() if now is None else now
        stored = {"facts": 0, "preferences": 0, "commitments": 0, "notes": 0}
        counterparty_id = None
        if counterparty:
            counterparty_id = counterparty if isinstance(counterparty, int) \
                else self.resolve_entity(counterparty)
        for item in items or []:
            kind = item.get("type")
            if kind == "fact":
                self.add_fact(item["entity"], item["predicate"],
                              item["value"], provenance=provenance,
                              episode_id=episode_id, now=now)
                stored["facts"] += 1
            elif kind == "preference":
                self.add_fact(item.get("entity") or counterparty_id,
                              "preference", item["value"],
                              provenance=provenance, episode_id=episode_id,
                              now=now)
                stored["preferences"] += 1
            elif kind == "commitment":
                self.add_commitment(
                    item.get("owed_by", "counterparty"), item["text"],
                    counterparty=counterparty_id,
                    due_text=item.get("due_text", ""),
                    provenance=provenance, episode_id=episode_id, now=now)
                stored["commitments"] += 1
            elif kind == "counterparty_note":
                if counterparty_id is None:
                    continue
                note_predicate = f"note:{int(now * 1000)}:{stored['notes']}"
                self.add_fact(counterparty_id, note_predicate, item["value"],
                              provenance=provenance, episode_id=episode_id,
                              now=now)
                stored["notes"] += 1
        return stored

    # -- profile slice ----------------------------------------------------

    def get_profile_slice(self, counterparty=None, objective: str = "",
                          budget_chars: int = 1800) -> str:
        """Prompt-ready background block, priority-ordered and capped.

        Order: open commitments (never truncated away silently —
        they are the reason follow-ups exist), counterparty's
        current facts, recent episode summaries (newest first,
        truncated first when the budget bites). The block is framed
        as historical background that may be stale.
        """
        counterparty_id = None
        if counterparty:
            counterparty_id = counterparty if isinstance(counterparty, int) \
                else self.resolve_entity(counterparty)
        sections: list[str] = []

        commitments = self.open_commitments(counterparty_id) \
            if counterparty_id else self.open_commitments()
        if commitments:
            lines = ["Open commitments on record:"]
            for c in commitments:
                who = c.get("counterparty") or "general"
                due = f" (due: {c['due_text']})" if c.get("due_text") else ""
                lines.append(
                    f"- [{c['owed_by']} -> {who}] {c['text']}{due}")
            sections.append("\n".join(lines))

        if counterparty_id:
            facts = [f for f in self.current_facts(counterparty_id)
                     if not f["predicate"].startswith("note:")]
            notes = [f for f in self.current_facts(counterparty_id)
                     if f["predicate"].startswith("note:")]
            name = self.entity_name(counterparty_id)
            if facts:
                lines = [f"Known facts about {name}:"]
                lines += [f"- {f['predicate']}: {f['value']}" for f in facts]
                sections.append("\n".join(lines))
            if notes:
                lines = [f"Notes from earlier contact with {name}:"]
                lines += [f"- {n['value']}" for n in notes[-4:]]
                sections.append("\n".join(lines))

        nathan_id = self.resolve_entity("Nathan Beitelman") \
            or self.resolve_entity("Nathan E. Beitelman")
        if nathan_id and nathan_id != counterparty_id:
            core_predicates = ("full_name", "fiancee", "dog",
                               "house_purchase", "home_city")
            core = [f for f in self.current_facts(nathan_id)
                    if f["predicate"] in core_predicates]
            if core:
                lines = ["About Nathan (who you represent):"]
                lines += [f"- {f['predicate']}: {f['value']}" for f in core]
                sections.append("\n".join(lines))

        if counterparty_id:
            rows = self._tx().execute(
                "SELECT summary, occurred_at FROM episodes "
                "WHERE counterparty_id=? AND summary != '' "
                "ORDER BY occurred_at DESC LIMIT 6",
                (counterparty_id,)).fetchall()
            if rows:
                lines = ["Recent history with them (newest first):"]
                lines += [f"- {r['summary']}" for r in rows]
                sections.append("\n".join(lines))

        if not sections:
            return ""
        header = ("Background from Nathan's records (historical; may be "
                  "stale — rely on what the person says now over this):")
        block = header + "\n" + "\n\n".join(sections)
        if len(block) > budget_chars:
            marker = "\n[background trimmed]"
            block = block[:budget_chars - len(marker)].rstrip() + marker
        return block
