"""Control plane: playbooks, tracker/guard, typer, brief assembly."""
import unittest

import call_controller as cc
import conversation_state as cs
import playbooks as pb


class FakeJob:
    def __init__(self, **kw):
        self.to = "+15551234567"
        self.business = "Acme Dental"
        self.objective = "controlled live qualification"
        self.allowed_decisions = ["ending the call"]
        self.playbook = ""
        self.counterparty = ""
        for k, v in kw.items():
            setattr(self, k, v)


class TestPlaybooks(unittest.TestCase):
    def test_all_three_ship_and_validate(self):
        names = pb.available_playbooks()
        for expected in ("appointment_scheduling", "information_gathering",
                         "follow_up_status"):
            self.assertIn(expected, names)
            loaded = pb.load_playbook(expected)
            self.assertIsNotNone(loaded, expected)
            self.assertTrue(loaded["collect"])

    def test_selection_explicit_then_inferred(self):
        self.assertEqual(
            pb.select_playbook("", "Please book an appointment")["name"],
            "appointment_scheduling")
        self.assertEqual(
            pb.select_playbook("", "Follow up on the status of my claim")
            ["name"], "follow_up_status")
        self.assertEqual(
            pb.select_playbook("", "Find out what the fee is")["name"],
            "information_gathering")
        self.assertIsNone(pb.select_playbook("", "Say hello"))
        self.assertEqual(
            pb.select_playbook("follow_up_status",
                               "book an appointment")["name"],
            "follow_up_status")
        self.assertIsNone(pb.select_playbook("does_not_exist", "x"))

    def test_appointment_playbook_blocks_dob(self):
        book = pb.load_playbook("appointment_scheduling")
        joined = " ".join(book["loop_in_triggers"]).lower()
        self.assertIn("date of birth", joined)


class TestTracker(unittest.TestCase):
    def test_similarity_catches_rephrasings(self):
        a = "Could you tell me about the kind of work you do?"
        b = "Can you tell me what kind of work you do?"
        self.assertGreaterEqual(cs.similarity(a, b), cs.REPEAT_THRESHOLD)
        self.assertLess(cs.similarity(a, "What are your office hours?"),
                        cs.REPEAT_THRESHOLD)

    def test_guard_escalates_substitute_then_wrap_up(self):
        tracker = cs.ConversationTracker()
        tracker.observe_assistant("What kind of work do you do?")
        first = tracker.check("Could you tell me what kind of work you do?")
        self.assertEqual(first["action"], "substitute")
        tracker.note_repeat_attempts(
            "Could you tell me what kind of work you do?")
        second = tracker.check("Tell me about the kind of work you do?")
        self.assertEqual(second["action"], "wrap_up")

    def test_distinct_question_passes(self):
        tracker = cs.ConversationTracker()
        tracker.observe_assistant("What kind of work do you do?")
        self.assertEqual(
            tracker.check("And what is your budget range?")["action"],
            "pass")

    def test_user_turn_marks_answered_and_fills_slots(self):
        book = pb.load_playbook("appointment_scheduling")
        tracker = cs.ConversationTracker(book)
        tracker.observe_assistant("What is the earliest available?")
        tracker.observe_user("Our scheduling office has an opening "
                             "Tuesday at the earliest.")
        self.assertTrue(tracker.asked[0].answered)
        self.assertIn("office_confirmed", tracker.slots_filled)
        self.assertIn("earliest_options", tracker.slots_filled)

    def test_next_unasked_uses_playbook_order(self):
        book = pb.load_playbook("information_gathering")
        tracker = cs.ConversationTracker(book)
        nxt = tracker.next_unasked_question()
        self.assertEqual(nxt, book["collect"][0]["question"])


class TestTyper(unittest.TestCase):
    TRANSCRIPT = [
        {"role": "assistant", "text": "Hi, this is Nathan's assistant "
         "calling on his behalf. I'm following up on the referral."},
        {"role": "user", "text": "Our office hours are Monday to Friday, "
         "nine to five. My name is Dana Lee."},
        {"role": "user", "text": "We'll fax the referral by Friday. "
         "Please call me at this number, mornings are best."},
        {"role": "assistant", "text": "Thank you. I'll have Nathan review "
         "it by Monday."},
    ]

    def test_types_facts_preferences_commitments(self):
        items = cc.extract_items(self.TRANSCRIPT, counterparty="Acme Dental")
        kinds = [i["type"] for i in items]
        self.assertIn("fact", kinds)
        self.assertIn("preference", kinds)
        commitments = [i for i in items if i["type"] == "commitment"]
        owed = {c["owed_by"] for c in commitments}
        self.assertEqual(owed, {"counterparty", "assistant"})
        cp = [c for c in commitments if c["owed_by"] == "counterparty"][0]
        self.assertEqual(cp["due_text"], "by Friday")
        facts = {i["predicate"]: i["value"] for i in items
                 if i["type"] == "fact"}
        self.assertEqual(facts["contact_name"], "Dana Lee")
        self.assertIn("Monday to Friday", facts["office_hours"])

    def test_assistant_facts_are_never_stored(self):
        items = cc.extract_items(
            [{"role": "assistant",
              "text": "Our office hours are nine to five."}],
            counterparty="Acme Dental")
        self.assertEqual([i for i in items if i["type"] == "fact"], [])


class TestBrief(unittest.TestCase):
    def test_brief_fail_open_without_store(self):
        brief = cc.build_brief(FakeJob(objective="book an appointment"))
        self.assertIsNotNone(brief.playbook)
        self.assertEqual(brief.playbook["name"], "appointment_scheduling")
        self.assertIn("Pip", brief.persona_core)
        sections = brief.prompt_sections()
        self.assertTrue(any("Playbook:" in s for s in sections))

    def test_jev_stub_refuses_with_instructions(self):
        with self.assertRaises(RuntimeError) as ctx:
            cc.HostedJevDecider()
        self.assertIn("Secure Vault", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
