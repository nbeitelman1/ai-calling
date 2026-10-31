"""WP-2 pure controller contracts; these are NOT media-pipeline acceptance."""
import ast
from pathlib import Path
import unittest

import call_controller as cc
from conversation_state import ConversationTracker


class Job:
    business = 'Offline closing fixture'
    objective = 'Say hello'
    playbook = ''
    counterparty = ''


def router(book=None):
    brief = cc.build_brief(Job(), playbook=book)
    return cc.TurnRouter(brief, ConversationTracker(book))


class TestClosingRouter(unittest.TestCase):
    def test_caller_goodbye_starts_closing_without_model(self):
        r = router()
        decision = r.after_user_turn('Thank you, goodbye.')
        self.assertEqual(decision.action, 'wrap_up')
        self.assertEqual(decision.reason, 'caller_goodbye')
        self.assertEqual(r.closing.phase.value, 'CLOSING')

    def test_objective_complete_is_an_explicit_closing_transition(self):
        book = {'name': 'synthetic', 'collect': [
            {'slot': 'status', 'keywords': ['complete'], 'question': 'Status?'}]}
        r = router(book)
        self.assertEqual(r.after_user_turn('The item is complete.').action, 'wrap_up')
        self.assertEqual(r.closing.reason, 'objective_complete')
        self.assertEqual(r.after_user_turn('Thanks, bye.').action, 'suppress')

    def test_question_reopens_once_and_does_not_immediately_reclose_filled_slots(self):
        book = {'name': 'synthetic', 'collect': [
            {'slot': 'status', 'keywords': ['complete'], 'question': 'Status?'}]}
        r = router(book)
        r.after_user_turn('It is complete.')
        decision = r.after_user_turn('Wait, what are your hours?')
        self.assertEqual(decision.action, 'reopen')
        self.assertEqual(r.closing.phase.value, 'OPEN')
        r.after_user_turn('Goodbye.')
        self.assertEqual(r.after_user_turn('Wait, one more question.').action, 'suppress')

    def test_thanks_alone_does_not_end_an_open_conversation(self):
        self.assertEqual(router().after_user_turn('Thanks.').action, 'continue')

    def test_negated_or_quoted_goodbye_does_not_end_a_call(self):
        for text in ('Do not say goodbye yet.', 'Goodbye Plumbing is our company.',
                     'Goodbye, but what time do you close?', 'He said goodbye yesterday.'):
            with self.subTest(text=text):
                self.assertEqual(router().after_user_turn(text).action, 'continue')

    def test_empty_input_does_not_mark_questions_answered(self):
        r = router()
        r.tracker.observe_assistant('What time?')
        self.assertEqual(r.after_user_turn('   ').action, 'continue')
        self.assertFalse(r.tracker.asked[0].answered)

    def test_closing_acknowledgements_do_not_change_slot_or_question_state(self):
        r = router()
        r.tracker.observe_assistant('What time?')
        r.closing.request_close('caller_goodbye')
        before = r.tracker.summary()
        self.assertEqual(r.after_user_turn('Thanks. Bye bye.').action, 'suppress')
        self.assertEqual(r.tracker.summary(), before)


class TestClosingState(unittest.TestCase):
    def state(self):
        from closing_state import ClosingState
        return ClosingState()

    def test_initial_state_has_no_line_or_hangup(self):
        s = self.state()
        self.assertEqual(s.phase.value, 'OPEN')
        self.assertIsNone(s.take_line())
        self.assertFalse(s.claim_hangup(0))

    def test_duplicate_triggers_claim_exactly_one_line(self):
        s = self.state()
        self.assertTrue(s.request_close('caller_goodbye'))
        for _ in range(5):
            self.assertFalse(s.request_close('objective_complete'))
        action = s.take_line()
        self.assertIsNotNone(action)
        self.assertEqual(action.text, 'Thank you for your time. Goodbye.')
        self.assertIsNone(s.take_line())
        self.assertEqual(s.summary()['lines_claimed'], 1)

    def test_generation_alone_cannot_arm_hangup(self):
        s = self.state()
        s.request_close('caller_goodbye')
        line = s.take_line()
        self.assertFalse(s.claim_hangup(line.token))
        self.assertFalse(s.playback_stopped(line.token))
        self.assertFalse(s.claim_hangup(line.token))

    def test_playback_must_be_claimed_before_start(self):
        s = self.state()
        s.request_close('caller_goodbye')
        self.assertFalse(s.playback_started(s.generation))

    def test_stop_after_matching_start_allows_exactly_one_hangup(self):
        s = self.state()
        s.request_close('caller_goodbye')
        line = s.take_line()
        self.assertTrue(s.playback_started(line.token))
        self.assertTrue(s.playback_stopped(line.token))
        self.assertTrue(s.claim_hangup(line.token))
        self.assertFalse(s.claim_hangup(line.token))
        self.assertEqual(s.phase.value, 'ENDED')
        self.assertEqual(s.summary()['hangup_claims'], 1)

    def test_one_real_interjection_invalidates_pending_hangup(self):
        s = self.state()
        s.request_close('caller_goodbye')
        line = s.take_line()
        s.playback_started(line.token)
        self.assertEqual(s.on_user_text('Wait, what time are you open?'), 'reopen')
        self.assertFalse(s.playback_stopped(line.token))
        self.assertFalse(s.claim_hangup(line.token))
        self.assertEqual(s.summary()['reopens'], 1)

    def test_second_closing_cannot_reopen(self):
        s = self.state()
        s.request_close('caller_goodbye')
        first = s.take_line()
        s.on_user_text('Wait a moment.')
        s.request_close('caller_goodbye')
        second = s.take_line()
        self.assertNotEqual(first.token, second.token)
        self.assertEqual(s.on_user_text('Wait, another question.'), 'suppress')
        self.assertEqual(s.phase.value, 'CLOSING')

    def test_stale_stop_cannot_complete_new_closing(self):
        s = self.state()
        s.request_close('caller_goodbye')
        old = s.take_line()
        s.playback_started(old.token)
        s.on_user_text('Wait a moment.')
        s.request_close('caller_goodbye')
        current = s.take_line()
        s.playback_started(current.token)
        self.assertFalse(s.playback_stopped(old.token))
        self.assertFalse(s.claim_hangup(current.token))
        s.playback_stopped(current.token)
        self.assertTrue(s.claim_hangup(current.token))

    def test_acknowledgements_never_cancel_closing(self):
        for text in ('Bye.', 'Thanks.', 'Thank you, goodbye.', 'Okay, bye bye.',
                     'Have a good day.', 'Thanks, you too.', ''):
            with self.subTest(text=text):
                s = self.state()
                s.request_close('caller_goodbye')
                self.assertEqual(s.on_user_text(text), 'suppress')
                self.assertEqual(s.phase.value, 'CLOSING')

    def test_substantive_correction_is_not_a_mere_acknowledgement(self):
        s = self.state()
        s.request_close('caller_goodbye')
        self.assertEqual(s.on_user_text('No.'), 'reopen')

    def test_handback_is_verbatim_and_not_reopened(self):
        s = self.state()
        s.request_close('loop_in_requested')
        self.assertEqual(s.take_line().text,
                         "Nathan will need to take this over. I'll end the call now.")
        self.assertEqual(s.on_user_text('Wait, another question.'), 'suppress')
        self.assertEqual(s.reason, 'loop_in_requested')

    def test_hard_caps_are_not_extended_by_interjections(self):
        for reason in ('max_minutes', 'test_budget_exceeded', 'soft_cap',
                       'monthly_cap', 'callee_opt_out', 'pipeline_error'):
            with self.subTest(reason=reason):
                s = self.state()
                s.request_close(reason)
                self.assertEqual(s.on_user_text('Wait, another question.'), 'suppress')

    def test_mandatory_trigger_supersedes_pending_reversible_close_without_second_line(self):
        s = self.state()
        s.request_close('caller_goodbye')
        line = s.take_line()
        self.assertFalse(s.request_close('max_minutes'))
        self.assertEqual(s.reason, 'max_minutes')
        self.assertEqual(s.on_user_text('Wait please.'), 'suppress')
        self.assertIsNone(s.take_line())
        s.playback_started(line.token)
        s.playback_stopped(line.token)
        self.assertTrue(s.claim_hangup(line.token))

    def test_mandatory_reason_upgrades_a_second_final_closing(self):
        s = self.state()
        s.request_close('caller_goodbye')
        s.on_user_text('Wait please.')
        s.request_close('caller_goodbye')
        s.request_close('max_minutes')
        self.assertEqual(s.reason, 'max_minutes')

    def test_bounded_event_interleavings_preserve_effect_limits(self):
        from itertools import product
        events = ('line', 'start', 'stop', 'ack', 'question', 'close', 'hangup', 'clear')
        for trace in product(events, repeat=4):
            s = self.state()
            s.request_close('caller_goodbye')
            for event in trace:
                before = s.summary()
                if event == 'line':
                    s.take_line()
                elif event == 'start':
                    s.playback_started(s.generation)
                elif event == 'stop':
                    s.playback_stopped(s.generation)
                elif event == 'ack':
                    s.on_user_text('Thanks, bye.')
                elif event == 'question':
                    s.on_user_text('Wait, what time?')
                elif event == 'close':
                    s.request_close('caller_goodbye')
                elif event == 'clear':
                    s.playback_interrupted(s.generation)
                elif s.claim_hangup(s.generation):
                    self.assertTrue(before['playback_complete'], trace)
                    self.assertEqual(before['phase'], 'CLOSING', trace)
                after = s.summary()
                self.assertLessEqual(after['reopens'], 1, trace)
                self.assertLessEqual(after['lines_claimed'], 2, trace)
                self.assertLessEqual(after['hangup_claims'], 1, trace)
                if before['phase'] == 'ENDED':
                    self.assertEqual(after, before, trace)

    def test_duplicate_callbacks_do_not_rearm(self):
        s = self.state()
        s.request_close('caller_goodbye')
        line = s.take_line()
        self.assertTrue(s.playback_started(line.token))
        self.assertFalse(s.playback_started(line.token))
        self.assertTrue(s.playback_stopped(line.token))
        self.assertFalse(s.playback_stopped(line.token))
        self.assertFalse(s.playback_started(line.token))
        self.assertTrue(s.claim_hangup(line.token))

    def test_clear_or_interruption_is_not_playback_completion(self):
        s = self.state()
        s.request_close('caller_goodbye')
        line = s.take_line()
        s.playback_started(line.token)
        self.assertTrue(s.playback_interrupted(line.token))
        self.assertFalse(s.playback_stopped(line.token))
        self.assertFalse(s.claim_hangup(line.token))

    def test_unknown_reason_fails_loudly_in_configuration(self):
        with self.assertRaises(ValueError):
            self.state().request_close('invented_close_reason')

    def test_forced_teardown_records_failure_not_completed_audio(self):
        s = self.state()
        s.request_close('max_minutes')
        line = s.take_line()
        self.assertTrue(s.fail_closed(line.token, 'cached_audio_unavailable'))
        summary = s.summary()
        self.assertEqual(summary['failure'], 'cached_audio_unavailable')
        self.assertFalse(summary['playback_complete'])
        self.assertEqual(summary['hangup_claims'], 1)
        self.assertFalse(s.fail_closed(line.token, 'retry'))

    def test_reason_preserved_after_hangup_and_no_new_closing(self):
        s = self.state()
        s.request_close('objective_complete')
        line = s.take_line()
        s.playback_started(line.token)
        s.playback_stopped(line.token)
        s.claim_hangup(line.token)
        self.assertFalse(s.request_close('caller_goodbye'))
        self.assertEqual(s.reason, 'objective_complete')
        self.assertEqual(s.on_user_text('Wait please.'), 'suppress')

    def test_no_network_or_model_imports_in_state_machine(self):
        import closing_state
        tree = ast.parse(Path(closing_state.__file__).read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module)
        self.assertLessEqual(set(imports), {'__future__', 'dataclasses', 'enum', 're'})
        self.assertFalse(any(isinstance(node, ast.AsyncFunctionDef) for node in ast.walk(tree)))


if __name__ == '__main__':
    unittest.main()
