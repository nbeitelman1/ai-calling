import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from store import Store
from fixtures_support import PUBLIC, NOW, CONNECTION, FROM, TO, ATTEMPT, CONTROL, LEG, SESSION, event, raw, signed

class Recovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.sqlite'
        self.now = [NOW]
        self.s = self.reopen()
        self.s.prepare(CONNECTION, FROM, TO, ATTEMPT)

    def reopen(self):
        return Store(self.path, PUBLIC, clock=lambda: self.now[0])

    def tearDown(self):
        self.tmp.cleanup()

    def deliver(self, e):
        body = raw(e)
        self.s.ingest(body, signed(body, self.now[0]))
        return self.s.process(e['data']['id'])

    def test_claim_is_single_use(self):
        self.s.claim_submission(ATTEMPT)
        with self.assertRaises(ValueError):
            self.s.claim_submission(ATTEMPT)

    def test_lost_response_recovered_by_signed_event(self):
        self.s.claim_submission(ATTEMPT)
        self.s = self.reopen()
        self.assertEqual(self.s.get(ATTEMPT)['state'], 'submit_unknown')
        self.assertEqual(self.deliver(event()), 'applied')
        self.assertEqual(self.s.get(ATTEMPT)['control'], CONTROL)

    def test_unclaimed_event_quarantined(self):
        self.assertEqual(self.deliver(event()), 'quarantined_unknown_or_unclaimed')
        self.assertIsNone(self.s.get(ATTEMPT)['control'])

    def test_response_identity(self):
        self.s.claim_submission(ATTEMPT)
        self.s.response(ATTEMPT, {'data': {'call_control_id':CONTROL,'call_leg_id':LEG,'call_session_id':SESSION}})
        self.assertEqual(self.s.get(ATTEMPT)['state'], 'initiated')

    def test_ack_then_restart_before_processing(self):
        self.s.claim_submission(ATTEMPT)
        e = event(); b = raw(e)
        self.s.ingest(b, signed(b))
        self.s = self.reopen()
        self.assertEqual(self.s.event_status('ev-1')['processed'], 0)
        self.assertEqual(self.s.process('ev-1'), 'applied')

    def test_duplicate_delivery_metadata_does_not_change_event(self):
        self.s.claim_submission(ATTEMPT)
        self.deliver(event())
        b = raw(event(delivery=2))
        self.assertTrue(self.s.ingest(b, signed(b))['duplicate'])
        self.assertEqual(self.s.process('ev-1'), 'applied')

    def test_id_collision_rejected(self):
        self.s.claim_submission(ATTEMPT)
        self.deliver(event())
        b = raw(event('call.hangup'))
        with self.assertRaises(ValueError):
            self.s.ingest(b, signed(b))

    def test_out_of_order_terminal_does_not_reopen(self):
        self.s.claim_submission(ATTEMPT)
        self.deliver(event('call.hangup', 'end', NOW+2))
        self.deliver(event('call.answered', 'answer', NOW+1))
        self.deliver(event())
        self.assertEqual(self.s.get(ATTEMPT)['state'], 'hangup')

    def test_wrong_route_quarantined(self):
        self.s.claim_submission(ATTEMPT)
        e = event(); e['data']['payload']['connection_id'] = 'different'
        self.assertEqual(self.deliver(e), 'quarantined_route_mismatch')
        self.assertIsNone(self.s.get(ATTEMPT)['control'])

    def test_conflicting_leg_does_not_overwrite(self):
        self.s.claim_submission(ATTEMPT); self.deliver(event())
        e = event('call.answered', 'e2'); e['data']['payload']['call_leg_id'] = 'another-leg'
        self.assertEqual(self.deliver(e), 'quarantined_leg_mismatch')
        self.assertEqual(self.s.get(ATTEMPT)['leg'], LEG)

    def test_unknown_event_stored_not_executed(self):
        self.assertEqual(self.deliver(event('call.custom.future')), 'unsupported_event_stored')

    def test_invalid_signature_never_queued(self):
        b = raw(event())
        with self.assertRaises(ValueError):
            self.s.ingest(b+b' ', signed(b))
        self.assertIsNone(self.s.event_status('ev-1'))

    def test_concurrent_claims_only_one(self):
        def claim(_):
            try:
                self.reopen().claim_submission(ATTEMPT)
                return 1
            except ValueError:
                return 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(claim, range(8))), 1)

    def test_concurrent_duplicate_events(self):
        b = raw(event())
        def queue(_):
            return self.reopen().ingest(b, signed(b))['duplicate']
        with ThreadPoolExecutor(max_workers=8) as pool:
            duplicates = list(pool.map(queue, range(8)))
        self.assertEqual(duplicates.count(False), 1)

    def test_stop_before_claim_cancels_without_send(self):
        self.s.request_stop(ATTEMPT)
        self.assertEqual(self.s.get(ATTEMPT)['state'], 'cancelled_before_submit')
        with self.assertRaises(ValueError):
            self.s.claim_submission(ATTEMPT)

    def test_stop_while_unknown_waits_for_identity(self):
        self.s.claim_submission(ATTEMPT); self.s.request_stop(ATTEMPT)
        self.assertIsNone(self.s.hangup_command(ATTEMPT))
        self.deliver(event())
        self.assertIsNotNone(self.s.hangup_command(ATTEMPT))

    def test_hangup_ack_is_not_terminal(self):
        self.s.claim_submission(ATTEMPT); self.deliver(event()); self.s.request_stop(ATTEMPT)
        command = self.s.hangup_command(ATTEMPT)
        self.s.acknowledge_hangup(ATTEMPT, {'data': {'result': 'ok'}})
        self.assertEqual(self.s.get(ATTEMPT)['state'], 'initiated')
        self.assertEqual(self.reopen().hangup_command(ATTEMPT), command)
        self.deliver(event('call.hangup', 'end'))
        self.assertIsNone(self.s.hangup_command(ATTEMPT))

    def test_no_new_hangup_command_after_window(self):
        self.s.claim_submission(ATTEMPT); self.deliver(event()); self.s.request_stop(ATTEMPT)
        self.s.hangup_command(ATTEMPT)
        self.now[0] += 61
        with self.assertRaises(ValueError):
            self.s.hangup_command(ATTEMPT)

    def test_conflicting_prepared_request(self):
        with self.assertRaises(ValueError):
            self.s.prepare(CONNECTION, FROM, '+12025550147', ATTEMPT)

    def test_late_response_preserves_terminal_state(self):
        self.s.claim_submission(ATTEMPT)
        self.deliver(event('call.hangup'))
        self.s.response(ATTEMPT, {'data': {'call_control_id':CONTROL,
            'call_leg_id':LEG, 'call_session_id':SESSION}})
        self.assertEqual(self.s.get(ATTEMPT)['state'], 'hangup')

    def test_known_control_can_correlate_without_client_state(self):
        self.s.claim_submission(ATTEMPT); self.deliver(event())
        e = event('call.answered', 'e2'); del e['data']['payload']['client_state']
        self.assertEqual(self.deliver(e), 'applied')

    def test_unknown_control_without_hint_is_quarantined(self):
        self.s.claim_submission(ATTEMPT)
        e = event(); del e['data']['payload']['client_state']
        self.assertEqual(self.deliver(e), 'quarantined_unknown_or_unclaimed')

    def test_signed_malformed_json_not_queued(self):
        b = b'not-json'
        with self.assertRaises(ValueError):
            self.s.ingest(b, signed(b))
        self.assertIsNone(self.s.event_status('ev-1'))
