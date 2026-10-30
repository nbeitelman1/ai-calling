"""Starter profile seed — durable facts from Nathan's records.

Idempotent: safe to re-run (facts dedupe on identical current
values, episodes dedupe on ref, entities resolve by name/alias).
Every seeded row carries the provenance string below so the record
never confuses seed data with something learned on a call.
"""
from __future__ import annotations

from profile_store import ProfileStore

SEED_PROVENANCE = "seeded from Nathan's records Oct 30 2026"

# Verbatim standing lines (Nathan-approved Oct 27-30, 2026). These
# are Nathan's own approved wording for his assistant, stored as
# profile facts so any surface can quote them exactly.
DISCLOSURE_LINE = "Hi, this is Nathan's assistant calling on his behalf."
AI_IDENTITY_ANSWER = (
    "I'm Nathan's AI assistant, calling for him. I can keep going, "
    "or I can bring Nathan in if you'd prefer to talk to him directly."
)
HAND_BACK_LINE = "Nathan will need to take this over. I'll end the call now."


def seed_starter_profile(store: ProfileStore) -> dict:
    """Seed entities, Nathan's durable facts, and the two Oct 30
    test-call episodes. Returns counts of what exists afterwards."""
    p = SEED_PROVENANCE

    nathan = store.get_or_create_entity(
        "person", "Nathan E. Beitelman", aliases=("+13218315278",))
    store.add_fact(nathan, "full_name", "Nathan E. Beitelman", provenance=p)
    store.add_fact(nathan, "home_city", "Palm Bay, FL", provenance=p)
    store.add_fact(nathan, "fiancee", "Lauren Whittaker", provenance=p)
    store.add_fact(nathan, "dog", "Roscoe", provenance=p)
    store.add_fact(
        nathan, "house_purchase",
        "2825 Madras Drive NW, Palm Bay, FL — new build; "
        "NVR Mortgage, loan officer Terry Cullen", provenance=p)
    store.add_fact(nathan, "preference:disclosure_line",
                   DISCLOSURE_LINE, provenance=p)
    store.add_fact(nathan, "preference:ai_identity_answer",
                   AI_IDENTITY_ANSWER, provenance=p)
    store.add_fact(nathan, "preference:hand_back_line",
                   HAND_BACK_LINE, provenance=p)
    store.add_fact(nathan, "preference:call_caps",
                   "$1.00 per call / $20.00 per month unless Nathan "
                   "approves more for a specific call", provenance=p)

    lauren = store.get_or_create_entity("person", "Lauren Whittaker")
    store.add_fact(lauren, "relationship",
                   "fiancée of Nathan E. Beitelman", provenance=p)

    store.get_or_create_entity("pet", "Roscoe")
    terry = store.get_or_create_entity("person", "Terry Cullen")
    store.add_fact(terry, "role",
                   "loan officer at NVR Mortgage for Nathan's "
                   "house purchase", provenance=p)
    store.get_or_create_entity(
        "place", "2825 Madras Drive NW",
        aliases=("2825 Madras Drive NW, Palm Bay, FL",))

    moak = store.get_or_create_entity("person", "Dr. Moak")
    store.add_fact(moak, "role",
                   "primary care at Brevard Health Alliance",
                   provenance=p)
    bha = store.get_or_create_entity(
        "business", "Brevard Health Alliance",
        aliases=("BHA", "+13212416800"))
    store.add_fact(bha, "therapy_line",
                   "BHA therapy evaluations: (321) 241-6800",
                   provenance=p)
    atlantic = store.get_or_create_entity(
        "business", "Atlantic Psychiatric", aliases=("+13217241614",))
    store.add_fact(atlantic, "phone", "(321) 724-1614", provenance=p)
    jordan = store.get_or_create_entity("person", "Jordan")
    store.add_fact(jordan, "role",
                   "Nathan's doctor at Atlantic Psychiatric",
                   provenance=p)

    # The two Oct 30, 2026 test calls to Nathan's own number. The
    # counterparty of record is Nathan himself (test calls).
    store.record_episode(
        ref="fea4ddae-64ed-41b3-ab82-bd0d64c16034",
        counterparty=nathan,
        summary=("Test call #1 (Oct 30, 2026): opening played, but "
                 "speech-to-text failed on every segment (provider "
                 "connection errors), so the assistant never replied; "
                 "Nathan hung up. Diagnosed and fixed the same day "
                 "(egress-proxy environment)."),
        transcript_ref=("qualification-call-2026-10-29.jsonl#"
                        "v3:B3sX_8JhzI1ug10dPH8LkT_rIYaCx7mi2H7JoH4nvrkFIob7l6m72Q"),
        provenance=p, occurred_at=1793388491.0)
    store.record_episode(
        ref="v3:mTsV3Fl79u4Z0UwJeiMB84JmL8TGpp7aNqXaQcOkOHsNXAvOEoVetg",
        counterparty=nathan,
        summary=("Test call #2 (Oct 30, 2026): first full live "
                 "two-way conversation, 30 turns. The assistant "
                 "answered the identity question with the approved "
                 "honest-AI line and used the hand-back line under "
                 "pressure, but repeated one qualifying question "
                 "several times and spoke the hand-back line more "
                 "than once before the call ended — the repetition "
                 "and terminal-discipline defects the intelligence "
                 "pass addresses."),
        transcript_ref=("qualification-call-2026-10-29.jsonl#"
                        "v3:mTsV3Fl79u4Z0UwJeiMB84JmL8TGpp7aNqXaQcOkOHsNXAvOEoVetg"),
        provenance=p, occurred_at=1793389620.0)

    return {
        "nathan_facts": len(store.current_facts(nathan)),
        "open_commitments": len(store.open_commitments()),
    }
