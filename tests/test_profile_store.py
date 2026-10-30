"""Temporal profile store: supersession, slicing, commitments."""
import tempfile
import unittest
from pathlib import Path

import profile_store as ps
import profile_seed


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ps.ProfileStore(Path(self.tmp.name) / "profile.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()


class TestSupersession(StoreCase):
    def test_new_value_supersedes_and_history_is_kept(self):
        e = self.store.get_or_create_entity("business", "Acme Dental")
        self.store.add_fact(e, "phone", "(321) 555-0100",
                            provenance="test", now=1000.0)
        self.store.add_fact(e, "phone", "(321) 555-0199",
                            provenance="test", now=2000.0)
        current = self.store.current_facts(e)
        self.assertEqual([f["value"] for f in current], ["(321) 555-0199"])
        history = self.store.fact_history(e, "phone")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["value"], "(321) 555-0100")
        self.assertEqual(history[0]["valid_to"], 2000.0)
        self.assertIsNotNone(history[0]["superseded_by"])
        self.assertIsNone(history[1]["valid_to"])

    def test_same_value_is_idempotent(self):
        e = self.store.get_or_create_entity("business", "Acme Dental")
        first = self.store.add_fact(e, "phone", "x", now=1000.0)
        again = self.store.add_fact(e, "phone", "x", now=2000.0)
        self.assertEqual(first, again)
        self.assertEqual(len(self.store.fact_history(e, "phone")), 1)

    def test_slice_shows_only_current_value(self):
        e = self.store.get_or_create_entity("business", "Acme Dental")
        self.store.add_fact(e, "address", "1 Old Way", now=1000.0)
        self.store.add_fact(e, "address", "9 New Road", now=2000.0)
        block = self.store.get_profile_slice("Acme Dental", budget_chars=4000)
        self.assertIn("9 New Road", block)
        self.assertNotIn("1 Old Way", block)


class TestSliceBudget(StoreCase):
    def test_budget_truncates_and_commitments_come_first(self):
        e = self.store.get_or_create_entity("business", "Acme Dental")
        self.store.add_commitment(
            "counterparty", "Send the intake form", counterparty=e,
            due_text="by Friday", now=1000.0)
        for i in range(12):
            self.store.record_episode(
                ref=f"ep-{i}", counterparty=e,
                summary="x" * 200, occurred_at=1000.0 + i)
        block = self.store.get_profile_slice("Acme Dental", budget_chars=600)
        self.assertLessEqual(len(block), 620)
        self.assertIn("Send the intake form", block)
        self.assertTrue(block.startswith("Background from Nathan's records"))

    def test_unknown_counterparty_slice_is_empty_without_nathan(self):
        self.assertEqual(self.store.get_profile_slice("Nobody"), "")


class TestCommitments(StoreCase):
    def test_lifecycle_open_done_dropped(self):
        e = self.store.get_or_create_entity("business", "Acme Dental")
        a = self.store.add_commitment("assistant", "Email the form",
                                      counterparty=e, now=1000.0)
        b = self.store.add_commitment("counterparty", "Call us back",
                                      counterparty=e, now=1001.0)
        self.assertEqual(len(self.store.open_commitments("Acme Dental")), 2)
        self.store.set_commitment_state(a, "done")
        remaining = self.store.open_commitments("Acme Dental")
        self.assertEqual([c["id"] for c in remaining], [b])
        self.store.set_commitment_state(b, "dropped")
        self.assertEqual(self.store.open_commitments(), [])
        with self.assertRaises(ValueError):
            self.store.set_commitment_state(a, "bogus")

    def test_extract_and_store_types_items(self):
        e = self.store.get_or_create_entity("business", "Acme Dental")
        ep = self.store.record_episode(ref="call-1", counterparty=e,
                                       summary="s")
        stored = self.store.extract_and_store([
            {"type": "fact", "entity": "Acme Dental",
             "predicate": "hours", "value": "Mon-Fri 9-5"},
            {"type": "preference", "entity": "Acme Dental",
             "value": "Prefers morning calls"},
            {"type": "commitment", "owed_by": "counterparty",
             "text": "Fax the referral", "due_text": "by Monday"},
            {"type": "counterparty_note", "value": "Front desk is new"},
        ], episode_id=ep, counterparty="Acme Dental", provenance="eval")
        self.assertEqual(stored, {"facts": 1, "preferences": 1,
                                  "commitments": 1, "notes": 1})
        facts = {f["predicate"]: f["value"]
                 for f in self.store.current_facts(e)}
        self.assertEqual(facts["hours"], "Mon-Fri 9-5")
        self.assertEqual(facts["preference"], "Prefers morning calls")
        self.assertTrue(any(v == "Front desk is new"
                            for v in facts.values()))
        opens = self.store.open_commitments("Acme Dental")
        self.assertEqual(opens[0]["due_text"], "by Monday")


class TestSeed(StoreCase):
    def test_seed_is_idempotent_and_complete(self):
        profile_seed.seed_starter_profile(self.store)
        again = profile_seed.seed_starter_profile(self.store)
        nathan = self.store.resolve_entity("+13218315278")
        self.assertIsNotNone(nathan)
        facts = {f["predicate"]: f["value"]
                 for f in self.store.current_facts(nathan)}
        self.assertEqual(facts["fiancee"], "Lauren Whittaker")
        self.assertEqual(facts["dog"], "Roscoe")
        self.assertIn("2825 Madras Drive NW", facts["house_purchase"])
        self.assertIn("Hi, this is Nathan's assistant",
                      facts["preference:disclosure_line"])
        self.assertEqual(again["nathan_facts"], len(facts))
        block = self.store.get_profile_slice(
            "Nathan E. Beitelman", budget_chars=4000)
        self.assertIn("Test call #2", block)
        self.assertEqual(
            self.store.resolve_entity("+13217241614"),
            self.store.resolve_entity("Atlantic Psychiatric"))
        self.assertEqual(
            self.store.resolve_entity("+13212416800"),
            self.store.resolve_entity("Brevard Health Alliance"))


if __name__ == "__main__":
    unittest.main()
