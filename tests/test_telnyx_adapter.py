"""Tests for MUSE'S OWN Telnyx adapter, against Muse's own fake carrier.

These assertions are ported from the contract exercised by the
independent reference pack, but every test here runs against
telnyx_adapter.TelnyxAdapter and telnyx_adapter.transport.FakeCarrier --
never against the reference Store. A pass here means Muse's service
passed, not that the reference oracle passed.

All keys, numbers, and ids are synthetic test fixtures. No Telnyx
account, real credential, or live call is involved. Network use is
loopback (127.0.0.1) only, in the HTTP tests.
"""
import base64
import http.client
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from telnyx_adapter import AdapterConfig, Approval, TelnyxAdapter
from telnyx_adapter.adapter import RetryWindowElapsed, SubmissionUnknown
from telnyx_adapter.transport import FakeCarrier, LoopbackFakeCarrier
from telnyx_adapter.verify import WebhookRejected, verify_webhook
from telnyx_adapter.wire import (
    decode_client_state,
    dial_request,
    hangup_request,
    parse_event,
)

NOW = 1793275200
CONNECTION = "987654321098765432"
FROM = "+12025550190"
TO = "+12025550191"
JOB = "job-own-adapter-001"
ATTEMPT = "11111111-2222-4333-8444-555555555555"
CONTROL = "v3:own-control-id"
LEG = "22222222-3333-4444-8555-666666666666"
SESSION = "33333333-4444-4555-8666-777777777777"


def approval(estimated=200, destination=TO, job=JOB, max_minutes=10):
    return Approval(job_id=job, destination=destination, max_minutes=max_minutes,
                    estimated_cost_cents=estimated)


class Fixtures:
    """Per-test-class deterministic signing key (test-only, not a Telnyx key)."""

    def __init__(self):
        self.private = Ed25519PrivateKey.from_private_bytes(bytes([42]) * 32)
        self.public = base64.b64encode(self.private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()

    def state(self, attempt=ATTEMPT):
        return base64.b64encode(json.dumps({"attempt_id": attempt}).encode()).decode()

    def event(self, kind="call.initiated", event_id="ev-1", at=NOW, delivery=1,
              control=CONTROL, leg=LEG, session=SESSION, connection=CONNECTION,
              from_=FROM, to=TO, with_state=True):
        payload = {"call_control_id": control, "call_leg_id": leg,
                   "call_session_id": session, "connection_id": connection,
                   "from": from_, "to": to}
        if with_state:
            payload["client_state"] = self.state()
        return {"data": {"record_type": "event", "id": event_id, "event_type": kind,
                         "occurred_at": datetime.fromtimestamp(at, timezone.utc).isoformat(),
                         "payload": payload},
                "meta": {"attempt": delivery}}

    def raw(self, value):
        return json.dumps(value, separators=(",", ":")).encode()

    def signed(self, body, stamp=NOW):
        sig = self.private.sign(str(stamp).encode() + b"|" + body)
        return {"telnyx-timestamp": str(stamp),
                "telnyx-signature-ed25519": base64.b64encode(sig).decode()}


FIX = Fixtures()
RAW_EVENT = FIX.raw(FIX.event())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "own.sqlite"
        self.now = [NOW]
        self.adapter = self.reopen()
        self.adapter.prepare(job_id=JOB, connection_id=CONNECTION, from_number=FROM,
                             to_number=TO, attempt_id=ATTEMPT, approval=approval())

    def reopen(self):
        return TelnyxAdapter(self.path, FIX.public,
                             config=AdapterConfig(authorized_destinations=(TO,)),
                             clock=lambda: self.now[0])

    def tearDown(self):
        self.tmp.cleanup()

    def deliver(self, event):
        body = FIX.raw(event)
        self.adapter.ingest_webhook(body, FIX.signed(body, self.now[0]))
        return self.adapter.process_event(event["data"]["id"])

    def fake(self):
        return FakeCarrier(expected_connection=CONNECTION, expected_from=FROM,
                           expected_to=TO, expected_attempt=ATTEMPT,
                           control_id=CONTROL, leg_id=LEG, session_id=SESSION)


class TestWire(unittest.TestCase):
    def test_dial_shape(self):
        r = dial_request(CONNECTION, FROM, TO, ATTEMPT, max_minutes=10)
        self.assertEqual((r["method"], r["path"]), ("POST", "/v2/calls"))
        self.assertEqual(set(r["json"]), {"connection_id", "from", "to", "client_state",
                                          "command_id", "time_limit_secs"})
        self.assertEqual(r["json"]["time_limit_secs"], 600)
        self.assertEqual(decode_client_state(r["json"]["client_state"]), ATTEMPT)
        self.assertNotIn("Authorization", r)

    def test_live_transport_resolves_credential_only_when_enabled(self):
        from telnyx_adapter.transport import LiveTransport, LiveTransportNotEnabled

        with self.assertRaises(LiveTransportNotEnabled):
            LiveTransport(AdapterConfig(), credential_resolver=lambda _ref: "value")
        transport = LiveTransport(
            AdapterConfig(live_enabled=True, api_credential_ref="TELNYX_API_KEY"),
            credential_resolver=lambda ref: "resolved" if ref == "TELNYX_API_KEY" else "")
        self.assertEqual(transport.api_key, "resolved")

    def test_array_and_sip_destinations_refused(self):
        with self.assertRaises(ValueError):
            dial_request(CONNECTION, FROM, [TO], ATTEMPT)
        with self.assertRaises(ValueError):
            dial_request(CONNECTION, FROM, "sip:test@example.invalid", ATTEMPT)

    def test_hangup_path_escaping(self):
        r = hangup_request("v3:opaque/id?x", ATTEMPT)
        self.assertEqual(r["path"], "/v2/calls/v3%3Aopaque%2Fid%3Fx/actions/hangup")
        self.assertEqual(r["json"], {"command_id": ATTEMPT})

    def test_client_state_rejects_extra_fields(self):
        value = base64.b64encode(b'{"attempt_id":"x","secret":"bad"}').decode()
        with self.assertRaises(ValueError):
            decode_client_state(value)

    def test_duplicate_json_and_naive_time_refused(self):
        with self.assertRaises(ValueError):
            parse_event(b'{"data":{},"data":{}}')
        e = FIX.event()
        e["data"]["occurred_at"] = "2026-10-29T12:00:00"
        with self.assertRaises(ValueError):
            parse_event(FIX.raw(e))


class TestSignatures(unittest.TestCase):
    def test_valid_and_mixed_case(self):
        verify_webhook(RAW_EVENT, FIX.signed(RAW_EVENT), FIX.public, now=NOW)
        headers = [(k.upper(), v) for k, v in FIX.signed(RAW_EVENT).items()]
        verify_webhook(RAW_EVENT, headers, FIX.public, now=NOW)

    def test_tamper_old_future_boundary_and_duplicates(self):
        with self.assertRaises(WebhookRejected):
            verify_webhook(RAW_EVENT + b" ", FIX.signed(RAW_EVENT), FIX.public, now=NOW)
        with self.assertRaises(WebhookRejected):
            verify_webhook(RAW_EVENT, FIX.signed(RAW_EVENT, NOW - 301), FIX.public, now=NOW)
        with self.assertRaises(WebhookRejected):
            verify_webhook(RAW_EVENT, FIX.signed(RAW_EVENT, NOW + 301), FIX.public, now=NOW)
        verify_webhook(RAW_EVENT, FIX.signed(RAW_EVENT, NOW - 300), FIX.public, now=NOW)
        dup = list(FIX.signed(RAW_EVENT).items()) + [("TELNYX-TIMESTAMP", str(NOW))]
        with self.assertRaises(WebhookRejected):
            verify_webhook(RAW_EVENT, dup, FIX.public, now=NOW)

    def test_bad_key_signature_and_body(self):
        with self.assertRaises(WebhookRejected):
            verify_webhook(RAW_EVENT, FIX.signed(RAW_EVENT),
                           base64.b64encode(b"x" * 32).decode(), now=NOW)
        bad = FIX.signed(RAW_EVENT)
        bad["telnyx-signature-ed25519"] = "%%%%"
        with self.assertRaises(WebhookRejected):
            verify_webhook(RAW_EVENT, bad, FIX.public, now=NOW)
        short = FIX.signed(RAW_EVENT)
        short["telnyx-signature-ed25519"] = base64.b64encode(b"x" * 63).decode()
        with self.assertRaises(WebhookRejected):
            verify_webhook(RAW_EVENT, short, FIX.public, now=NOW)
        with self.assertRaises(WebhookRejected):
            verify_webhook(b"x" * 65537, FIX.signed(b"x" * 65537), FIX.public, now=NOW)
        with self.assertRaises(WebhookRejected):
            verify_webhook(RAW_EVENT.decode(), FIX.signed(RAW_EVENT), FIX.public, now=NOW)
        changed = FIX.signed(RAW_EVENT)
        changed["telnyx-timestamp"] = str(NOW + 1)
        with self.assertRaises(WebhookRejected):
            verify_webhook(RAW_EVENT, changed, FIX.public, now=NOW)


class TestAdapterRecovery(Base):
    def test_claim_is_single_use(self):
        self.adapter.claim_submission(ATTEMPT)
        with self.assertRaises(ValueError):
            self.adapter.claim_submission(ATTEMPT)

    def test_lost_response_recovered_by_signed_event(self):
        self.adapter.claim_submission(ATTEMPT)
        self.adapter = self.reopen()
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "submit_unknown")
        self.assertEqual(self.deliver(FIX.event()), "applied")
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["control_id"], CONTROL)

    def test_unclaimed_event_quarantined(self):
        self.assertEqual(self.deliver(FIX.event()), "quarantined_unknown_or_unclaimed")
        self.assertIsNone(self.adapter.get_attempt(ATTEMPT)["control_id"])

    def test_response_identity_and_submit_via_fake(self):
        self.adapter.submit_via(self.fake(), ATTEMPT)
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "initiated")

    def test_live_transport_is_blocked_when_disabled(self):
        class UnmarkedTransport:
            def dial(self, path, body):
                raise AssertionError("must not send")

        with self.assertRaises(ValueError):
            self.adapter.submit_via(UnmarkedTransport(), ATTEMPT)
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "prepared")

    def test_dropped_fake_response_leaves_unknown_no_redial(self):
        fake = self.fake()
        fake.drop_response = True
        with self.assertRaises(SubmissionUnknown):
            self.adapter.submit_via(fake, ATTEMPT)
        self.assertEqual(fake.dial_count, 1)
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "submit_unknown")
        with self.assertRaises(ValueError):
            self.adapter.claim_submission(ATTEMPT)
        # Recovery is by signed event, not by a second dial.
        self.assertEqual(self.deliver(FIX.event()), "applied")
        self.assertEqual(fake.dial_count, 1)

    def test_ack_then_restart_before_processing(self):
        self.adapter.claim_submission(ATTEMPT)
        body = FIX.raw(FIX.event())
        self.adapter.ingest_webhook(body, FIX.signed(body))
        self.adapter = self.reopen()
        self.assertEqual(self.adapter.event_status("ev-1")["processed"], 0)
        self.assertEqual(self.adapter.process_event("ev-1"), "applied")

    def test_duplicate_delivery_metadata_is_harmless(self):
        self.adapter.claim_submission(ATTEMPT)
        self.deliver(FIX.event())
        body = FIX.raw(FIX.event(delivery=2))
        self.assertTrue(self.adapter.ingest_webhook(body, FIX.signed(body))["duplicate"])
        self.assertEqual(self.adapter.process_event("ev-1"), "applied")

    def test_id_collision_rejected(self):
        self.adapter.claim_submission(ATTEMPT)
        self.deliver(FIX.event())
        with self.assertRaises(ValueError):
            self.adapter.ingest_webhook(
                FIX.raw(FIX.event("call.hangup")),
                FIX.signed(FIX.raw(FIX.event("call.hangup"))))

    def test_out_of_order_terminal_does_not_reopen(self):
        self.adapter.claim_submission(ATTEMPT)
        self.deliver(FIX.event("call.hangup", "end", NOW + 2))
        self.deliver(FIX.event("call.answered", "answer", NOW + 1))
        self.deliver(FIX.event())
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "hangup")

    def test_wrong_route_and_conflicting_leg_quarantined(self):
        self.adapter.claim_submission(ATTEMPT)
        e = FIX.event(event_id="ev-route")
        e["data"]["payload"]["connection_id"] = "different"
        self.assertEqual(self.deliver(e), "quarantined_route_mismatch")
        self.assertIsNone(self.adapter.get_attempt(ATTEMPT)["control_id"])
        self.assertEqual(self.deliver(FIX.event()), "applied")
        e2 = FIX.event("call.answered", "e2")
        e2["data"]["payload"]["call_leg_id"] = "another-leg"
        self.assertEqual(self.deliver(e2), "quarantined_leg_mismatch")
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["leg_id"], LEG)

    def test_session_alone_never_correlates(self):
        # Bound leg first, then an event with an unknown control id, no
        # client_state hint, but the SAME session id. Session alone must
        # not join it to the bound attempt.
        self.adapter.claim_submission(ATTEMPT)
        self.deliver(FIX.event())
        e = FIX.event("call.answered", "e-session-only", control="v3:unrelated-control",
                      with_state=False)
        # session intentionally matches the bound leg; control does not.
        self.assertEqual(self.deliver(e), "quarantined_unknown_or_unclaimed")
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "initiated")

    def test_unknown_event_stored_not_executed(self):
        self.assertEqual(self.deliver(FIX.event("call.custom.future")), "unsupported_event_stored")

    def test_invalid_signature_and_malformed_json_never_queued(self):
        body = FIX.raw(FIX.event())
        with self.assertRaises(WebhookRejected):
            self.adapter.ingest_webhook(body + b" ", FIX.signed(body))
        self.assertIsNone(self.adapter.event_status("ev-1"))
        bad = b"not-json"
        with self.assertRaises(ValueError):
            self.adapter.ingest_webhook(bad, FIX.signed(bad))

    def test_concurrent_claims_and_duplicate_events(self):
        def claim(_):
            try:
                self.reopen().claim_submission(ATTEMPT)
                return 1
            except ValueError:
                return 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(claim, range(8))), 1)
        body = FIX.raw(FIX.event())

        def queue(_):
            return self.reopen().ingest_webhook(body, FIX.signed(body))["duplicate"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            dups = list(pool.map(queue, range(8)))
        self.assertEqual(dups.count(False), 1)

    def test_stop_before_claim_cancels_without_send(self):
        self.adapter.request_stop(attempt_id=ATTEMPT)
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "cancelled_before_submit")
        with self.assertRaises(ValueError):
            self.adapter.claim_submission(ATTEMPT)

    def test_stop_without_call_id_waits_then_hangup(self):
        self.adapter.claim_submission(ATTEMPT)
        self.adapter.request_stop(attempt_id=ATTEMPT)
        self.assertIsNone(self.adapter.hangup_command(ATTEMPT))
        self.deliver(FIX.event())
        self.assertIsNotNone(self.adapter.hangup_command(ATTEMPT))

    def test_hangup_ack_is_not_terminal_and_window(self):
        self.adapter.claim_submission(ATTEMPT)
        self.deliver(FIX.event())
        self.adapter.request_stop(attempt_id=ATTEMPT)
        command = self.adapter.hangup_command(ATTEMPT)
        self.adapter.acknowledge_hangup(ATTEMPT, {"data": {"result": "ok"}})
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "initiated")
        self.assertEqual(self.reopen().hangup_command(ATTEMPT), command)
        self.deliver(FIX.event("call.hangup", "end"))
        self.assertIsNone(self.adapter.hangup_command(ATTEMPT))

    def test_no_new_hangup_command_after_window(self):
        self.adapter.claim_submission(ATTEMPT)
        self.deliver(FIX.event())
        self.adapter.request_stop(attempt_id=ATTEMPT)
        self.adapter.hangup_command(ATTEMPT)
        self.now[0] += 61
        with self.assertRaises(RetryWindowElapsed):
            self.adapter.hangup_command(ATTEMPT)

    def test_conflicting_prepared_request_and_late_response(self):
        with self.assertRaises(ValueError):
            self.adapter.prepare(job_id=JOB, connection_id=CONNECTION, from_number=FROM,
                                 to_number="+12025550147", attempt_id=ATTEMPT,
                                 approval=approval(destination="+12025550147"))
        self.adapter.claim_submission(ATTEMPT)
        self.deliver(FIX.event("call.hangup"))
        self.adapter.record_response(ATTEMPT, {"data": {"call_control_id": CONTROL,
                                                       "call_leg_id": LEG,
                                                       "call_session_id": SESSION}})
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "hangup")

    def test_known_control_correlates_without_client_state(self):
        self.adapter.claim_submission(ATTEMPT)
        self.deliver(FIX.event())
        e = FIX.event("call.answered", "e2", with_state=False)
        self.assertEqual(self.deliver(e), "applied")

    def test_quarantine_is_listed_for_operator(self):
        self.assertEqual(self.deliver(FIX.event()), "quarantined_unknown_or_unclaimed")
        self.assertEqual(len(self.adapter.list_quarantined()), 1)

    def test_media_job_claim_is_durable_single_use_and_expires(self):
        payload = {"job": {"to": TO}, "refs": {}, "route": [CONNECTION, FROM, TO],
                   "log_name": "call.jsonl"}
        self.adapter.register_media_job(ATTEMPT, payload, expires_at=NOW + 60)
        self.adapter = self.reopen()
        self.assertEqual(self.adapter.claim_media_job(ATTEMPT, now=NOW), payload)
        self.assertIsNone(self.adapter.claim_media_job(ATTEMPT, now=NOW))
        other = "99999999-8888-4777-8666-555555555555"
        self.adapter.register_media_job(other, payload, expires_at=NOW - 1)
        self.assertIsNone(self.adapter.claim_media_job(other, now=NOW))

    def test_carrier_reconciliation_reports_answer_and_hangup(self):
        self.adapter.claim_submission(ATTEMPT)
        self.deliver(FIX.event("call.answered", "answered"))
        self.deliver(FIX.event("call.hangup", "hangup"))
        receipt = self.adapter.reconcile_call_record(ATTEMPT)
        self.assertTrue(receipt["answered_event"])
        self.assertTrue(receipt["hangup_event"])

    def test_quota_reservation_is_atomic_and_bounded(self):
        self.assertEqual(self.adapter.reserve_quota(
            "groq_audio_seconds", "2026-10-29", 30, 60), 30)
        self.assertEqual(self.adapter.reserve_quota(
            "groq_audio_seconds", "2026-10-29", 30, 60), 60)
        with self.assertRaises(ValueError):
            self.adapter.reserve_quota(
                "groq_audio_seconds", "2026-10-29", 1, 60)

    def test_multi_quota_reservation_rolls_back_as_one_unit(self):
        with self.assertRaises(ValueError):
            self.adapter.reserve_quotas([
                ("groq_audio_seconds", "2026-10-30", 30, 60),
                ("gemini_requests", "2026-10-30", 11, 10),
            ])
        self.assertEqual(self.adapter.quota_usage(
            "groq_audio_seconds", "2026-10-30"), 0)

    def test_media_job_quota_settlement_releases_unused_reservation(self):
        attempt = "12345678-8888-4777-8666-555555555555"
        reservation = [("groq_audio_seconds", "2026-10-31", 60, 100)]
        self.adapter.register_media_job(
            attempt, {"job": {}}, expires_at=NOW + 60,
            quota_reservations=reservation)
        self.adapter.settle_media_job_quotas(
            attempt, {"groq_audio_seconds": 10})
        self.assertEqual(self.adapter.quota_usage(
            "groq_audio_seconds", "2026-10-31"), 10)


class TestApprovalBudget(Base):
    def test_empty_allowlist_fails_closed(self):
        adapter = TelnyxAdapter(
            Path(self.tmp.name) / "closed.sqlite", FIX.public,
            config=AdapterConfig(), clock=lambda: self.now[0])
        with self.assertRaises(ValueError):
            adapter.prepare(job_id="job-closed", connection_id=CONNECTION,
                            from_number=FROM, to_number=TO,
                            attempt_id="66666666-7777-4888-8999-000000000000",
                            approval=approval(job="job-closed"))

    def test_negative_costs_are_rejected(self):
        with self.assertRaises(ValueError):
            self.adapter.prepare(
                job_id="job-negative", connection_id=CONNECTION,
                from_number=FROM, to_number=TO,
                attempt_id="77777777-8888-4999-8000-111111111111",
                approval=approval(job="job-negative", estimated=-1))
        self.adapter.claim_submission(ATTEMPT)
        self.deliver(FIX.event("call.hangup", "negative-end"))
        with self.assertRaises(ValueError):
            self.adapter.reconcile_bill(ATTEMPT, -1, "bill-negative")

    def test_one_approval_cannot_create_second_attempt(self):
        with self.assertRaises(ValueError):
            self.adapter.prepare(
                job_id=JOB, connection_id=CONNECTION, from_number=FROM,
                to_number=TO,
                attempt_id="88888888-9999-4000-8111-222222222222",
                approval=approval())

    def test_job_stop_cancels_existing_prepared_attempt(self):
        self.adapter.request_stop(job_id=JOB, reason="operator stop")
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"],
                         "cancelled_before_submit")
        with self.assertRaises(ValueError):
            self.adapter.claim_submission(ATTEMPT)

    def test_stop_rejects_mismatched_job_and_attempt(self):
        with self.assertRaises(ValueError):
            self.adapter.request_stop(attempt_id=ATTEMPT, job_id="other-job")

    def test_approval_and_allowlist_gates(self):
        with self.assertRaises(ValueError):
            self.adapter.prepare(job_id="job-2", connection_id=CONNECTION, from_number=FROM,
                                 to_number=TO, attempt_id="22222222-3333-4444-8555-666666666666",
                                 approval=None)
        with self.assertRaises(ValueError):
            self.adapter.prepare(job_id="job-2", connection_id=CONNECTION, from_number=FROM,
                                 to_number=TO, attempt_id="22222222-3333-4444-8555-666666666666",
                                 approval=approval(job="job-other"))
        with self.assertRaises(ValueError):
            self.adapter.prepare(job_id="job-2", connection_id=CONNECTION, from_number=FROM,
                                 to_number="+12025550199", attempt_id="22222222-3333-4444-8555-666666666666",
                                 approval=approval(job="job-2", destination="+12025550199"))

    def test_per_call_and_monthly_caps(self):
        with self.assertRaises(ValueError):
            self.adapter.prepare(job_id="job-big", connection_id=CONNECTION, from_number=FROM,
                                 to_number=TO, attempt_id="33333333-4444-4555-8666-777777777777",
                                 approval=approval(job="job-big", estimated=501))
        # Monthly cap: existing $2.00 reservation + $19.00 would exceed $20.
        with self.assertRaises(ValueError):
            self.adapter.prepare(job_id="job-big2", connection_id=CONNECTION, from_number=FROM,
                                 to_number=TO, attempt_id="44444444-5555-4666-8777-888888888888",
                                 approval=approval(job="job-big2", estimated=1900))

    def test_stop_before_job_exists_is_remembered(self):
        self.adapter.request_stop(job_id="job-later", reason="operator stop")
        self.adapter.prepare(job_id="job-later", connection_id=CONNECTION, from_number=FROM,
                             to_number=TO, attempt_id="55555555-6666-4777-8888-999999999999",
                             approval=approval(job="job-later"))
        attempt = self.adapter.get_attempt("55555555-6666-4777-8888-999999999999")
        self.assertEqual(attempt["state"], "cancelled_before_submit")
        with self.assertRaises(ValueError):
            self.adapter.claim_submission("55555555-6666-4777-8888-999999999999")

    def test_hangup_is_not_a_final_bill(self):
        self.adapter.claim_submission(ATTEMPT)
        self.deliver(FIX.event())
        with self.assertRaises(ValueError):
            self.adapter.reconcile_bill(ATTEMPT, 150, "bill-test-1")
        self.deliver(FIX.event("call.hangup", "end"))
        job_before = self.adapter.get_job(JOB)
        self.assertEqual(job_before["billing_reconciled"], 0)
        result = self.adapter.reconcile_bill(ATTEMPT, 250, "bill-test-1")
        self.assertEqual(result["overage_cents"], 50)
        self.assertEqual(self.adapter.get_job(JOB)["billing_reconciled"], 1)


class TestLoopbackHttp(Base):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "http.sqlite"
        self.adapter = TelnyxAdapter(self.path, FIX.public,
                                     config=AdapterConfig(authorized_destinations=(TO,)),
                                     clock=lambda: NOW)
        self.adapter.prepare(job_id=JOB, connection_id=CONNECTION, from_number=FROM,
                             to_number=TO, attempt_id=ATTEMPT, approval=approval())
        self.fake = FakeCarrier(expected_connection=CONNECTION, expected_from=FROM,
                                expected_to=TO, expected_attempt=ATTEMPT,
                                control_id=CONTROL, leg_id=LEG, session_id=SESSION)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_shape_over_http(self):
        request = self.adapter.claim_submission(ATTEMPT)
        with LoopbackFakeCarrier(self.adapter, self.fake) as lab:
            status, response = lab.request(request["path"], FIX.raw(request["json"]))
            self.assertEqual(status, 200)
            self.adapter.record_response(ATTEMPT, response)
            self.assertEqual(self.fake.dial_count, 1)
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "initiated")

    def test_loss_after_acceptance_over_http(self):
        self.fake.drop_response = True
        request = self.adapter.claim_submission(ATTEMPT)
        with LoopbackFakeCarrier(self.adapter, self.fake) as lab:
            with self.assertRaises(http.client.RemoteDisconnected):
                lab.request(request["path"], FIX.raw(request["json"]))
            self.assertEqual(self.fake.dial_count, 1)
            with self.assertRaises(ValueError):
                self.adapter.claim_submission(ATTEMPT)
            body = FIX.raw(FIX.event())
            self.assertEqual(lab.request("/webhooks/telnyx", body, FIX.signed(body))[0], 200)
            self.assertEqual(self.adapter.process_event("ev-1"), "applied")
            self.assertEqual(self.fake.dial_count, 1)

    def test_tampered_and_duplicate_webhooks_over_http(self):
        self.adapter.claim_submission(ATTEMPT)
        with LoopbackFakeCarrier(self.adapter, self.fake) as lab:
            body = FIX.raw(FIX.event())
            self.assertEqual(lab.request("/webhooks/telnyx", body + b" ", FIX.signed(body))[0], 400)
            self.assertIsNone(self.adapter.event_status("ev-1"))
            for n in (1, 2):
                body = FIX.raw(FIX.event(delivery=n))
                status, receipt = lab.request("/webhooks/telnyx", body, FIX.signed(body))
                self.assertEqual(status, 200)
                self.assertEqual(receipt["duplicate"], n == 2)

    def test_hangup_ack_not_terminal_over_http(self):
        self.adapter.claim_submission(ATTEMPT)
        body = FIX.raw(FIX.event())
        self.adapter.ingest_webhook(body, FIX.signed(body))
        self.adapter.process_event("ev-1")
        self.adapter.request_stop(attempt_id=ATTEMPT)
        command = self.adapter.hangup_command(ATTEMPT)
        with LoopbackFakeCarrier(self.adapter, self.fake) as lab:
            status, response = lab.request(command["path"], FIX.raw(command["json"]))
            self.assertEqual(status, 200)
            self.adapter.acknowledge_hangup(ATTEMPT, response)
            self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "initiated")


if __name__ == "__main__":
    unittest.main()
