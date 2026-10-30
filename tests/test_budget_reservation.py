"""Budget reservation lifecycle: a job holds its estimate against the
monthly cap only while its attempt is in flight.

Before this rule, reserved_cents was never released: 20 finished
October 2026 jobs x 100 cents of estimates exhausted the $20 cap and
refused a 21st call whose real spend was ~0 (a one-time operator
release converted those reservations by hand on Oct 30, 2026). These
tests pin the durable invariant in the adapter itself: every terminal
transition (hangup event, operator resolution, stop before submit)
converts reserved_cents to spent_cents in the same transaction, and
reconcile_bill records the actual bill into both fields.

All keys, numbers, and ids are synthetic test fixtures. No network.
"""
import base64
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from telnyx_adapter import AdapterConfig, Approval, TelnyxAdapter

NOW = 1793275200
CONNECTION = "987654321098765432"
FROM = "+12025550190"
TO = "+12025550191"
CONTROL = "v3:own-control-id"
LEG = "22222222-3333-4444-8555-666666666666"
SESSION = "33333333-4444-4555-8666-777777777777"

_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes([42]) * 32)
PUBLIC = base64.b64encode(_PRIVATE.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def _state(attempt):
    return base64.b64encode(json.dumps({"attempt_id": attempt}).encode()).decode()


def event(kind, event_id, attempt, **payload_extra):
    payload = {"call_control_id": CONTROL, "call_leg_id": LEG,
               "call_session_id": SESSION, "connection_id": CONNECTION,
               "from": FROM, "to": TO, "client_state": _state(attempt)}
    payload.update(payload_extra)
    return {"data": {"record_type": "event", "id": event_id, "event_type": kind,
                     "occurred_at": datetime.fromtimestamp(
                         NOW, timezone.utc).isoformat(),
                     "payload": payload},
            "meta": {"attempt": 1}}


def signed(body):
    sig = _PRIVATE.sign(str(NOW).encode() + b"|" + body)
    return {"telnyx-timestamp": str(NOW),
            "telnyx-signature-ed25519": base64.b64encode(sig).decode()}


class Base(unittest.TestCase):
    CAP = 300

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "budget.sqlite"
        self.adapter = TelnyxAdapter(
            self.path, PUBLIC,
            config=AdapterConfig(authorized_destinations=(TO,),
                                 monthly_cap_cents=self.CAP),
            clock=lambda: NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self, job, attempt, estimated=200):
        return self.adapter.prepare(
            job_id=job, connection_id=CONNECTION, from_number=FROM,
            to_number=TO, attempt_id=attempt,
            approval=Approval(job_id=job, destination=TO, max_minutes=10,
                              estimated_cost_cents=estimated))

    def deliver(self, ev):
        body = json.dumps(ev, separators=(",", ":")).encode()
        self.adapter.ingest_webhook(body, signed(body))
        return self.adapter.process_event(ev["data"]["id"])

    def drive_to_hangup(self, job, attempt, prefix):
        self.adapter.claim_submission(attempt)
        self.adapter.record_response(attempt, {"data": {
            "call_control_id": CONTROL, "call_leg_id": LEG,
            "call_session_id": SESSION}})
        self.deliver(event("call.initiated", prefix + "-i", attempt))
        self.deliver(event("call.answered", prefix + "-a", attempt))
        self.deliver(event("call.hangup", prefix + "-h", attempt))


class TestReservationLifecycle(Base):
    def test_inflight_reservation_counts_then_hangup_frees_cap(self):
        job_a, att_a = "job-a", "11111111-2222-4333-8444-555555555555"
        job_b, att_b = "job-b", "22222222-3333-4444-8555-666666666666"
        job_c, att_c = "job-c", "33333333-4444-4555-8666-777777777777"
        self.prepare(job_a, att_a)
        self.assertEqual(self.adapter.get_job(job_a)["reserved_cents"], 200)
        # In flight (prepared, then claimed, then live): still held.
        self.adapter.claim_submission(att_a)
        self.assertEqual(self.adapter.get_job(job_a)["reserved_cents"], 200)
        with self.assertRaises(ValueError):
            self.prepare(job_b, att_b)  # 200 + 200 > 300 cap
        self.adapter.record_response(att_a, {"data": {
            "call_control_id": CONTROL, "call_leg_id": LEG,
            "call_session_id": SESSION}})
        self.deliver(event("call.initiated", "a-i", att_a))
        self.deliver(event("call.answered", "a-a", att_a))
        self.assertEqual(self.adapter.get_job(job_a)["reserved_cents"], 200)
        # Terminal: reservation converts to recorded spend (zero here).
        self.deliver(event("call.hangup", "a-h", att_a))
        job = self.adapter.get_job(job_a)
        self.assertEqual(job["reserved_cents"], 0)
        self.assertEqual(job["reserved_cents"], job["spent_cents"])
        # The freed space is real: job B now fits, and while B is in
        # flight the cap still binds job C.
        self.prepare(job_b, att_b)
        with self.assertRaises(ValueError):
            self.prepare(job_c, att_c)

    def test_reconcile_bill_records_actual_into_reserved_and_spent(self):
        job, att = "job-bill", "11111111-2222-4333-8444-555555555555"
        self.prepare(job, att)
        self.drive_to_hangup(job, att, "bill")
        result = self.adapter.reconcile_bill(att, 137, "bill-1")
        self.assertEqual(result["actual_cents"], 137)
        row = self.adapter.get_job(job)
        self.assertEqual(row["spent_cents"], 137)
        self.assertEqual(row["reserved_cents"], 137)
        self.assertEqual(row["billing_reconciled"], 1)
        # Recorded spend counts truthfully: 137 + 200 > 300.
        with self.assertRaises(ValueError):
            self.prepare("job-next", "22222222-3333-4444-8555-666666666666")

    def test_stop_before_submit_releases_reservation(self):
        job, att = "job-stop", "11111111-2222-4333-8444-555555555555"
        self.prepare(job, att)
        self.adapter.request_stop(attempt_id=att, reason="operator stop")
        self.assertEqual(self.adapter.get_attempt(att)["state"],
                         "cancelled_before_submit")
        self.assertEqual(self.adapter.get_job(job)["reserved_cents"], 0)

    def test_pending_stop_job_is_born_released(self):
        job, att = "job-later", "11111111-2222-4333-8444-555555555555"
        self.adapter.request_stop(job_id=job, reason="operator stop")
        self.prepare(job, att)
        self.assertEqual(self.adapter.get_attempt(att)["state"],
                         "cancelled_before_submit")
        self.assertEqual(self.adapter.get_job(job)["reserved_cents"], 0)

    def test_stop_on_live_attempt_releases_nothing(self):
        job, att = "job-live", "11111111-2222-4333-8444-555555555555"
        self.prepare(job, att)
        self.adapter.claim_submission(att)
        self.adapter.record_response(att, {"data": {
            "call_control_id": CONTROL, "call_leg_id": LEG,
            "call_session_id": SESSION}})
        self.deliver(event("call.answered", "live-a", att))
        self.adapter.request_stop(attempt_id=att, reason="wrap up")
        self.assertEqual(self.adapter.get_attempt(att)["state"], "answered")
        self.assertEqual(self.adapter.get_job(job)["reserved_cents"], 200)


class TestResolutionReleasesReservation(Base):
    def resolve(self, attempt, basis, **kwargs):
        return self.adapter.resolve_attempt_unsubmitted(
            attempt, basis=basis, operator="nathan (via Muse, test)",
            evidence_ref="budget-reservation-test",
            attestation="Synthetic test attestation.", **kwargs)

    def test_carrier_refused_resolution_releases(self):
        job, att = "job-refused", "11111111-2222-4333-8444-555555555555"
        self.prepare(job, att)
        self.adapter.claim_submission(att)
        self.assertEqual(self.adapter.get_job(job)["reserved_cents"], 200)
        receipt = self.resolve(att, "carrier_refused")
        self.assertEqual(receipt["state"], "closed_unsubmitted")
        self.assertEqual(self.adapter.get_job(job)["reserved_cents"], 0)

    def test_carrier_record_verified_shape_a_releases(self):
        job, att = "job-shape-a", "11111111-2222-4333-8444-555555555555"
        self.prepare(job, att)
        self.adapter.claim_submission(att)
        self.adapter.record_response(att, {"data": {
            "call_control_id": CONTROL, "call_leg_id": LEG,
            "call_session_id": SESSION}})
        self.deliver(event("call.initiated", "sa-i", att))
        self.deliver(event("call.answered", "sa-a", att))
        receipt = self.resolve(att, "carrier_record_verified",
                               carrier_hangup_at=NOW + 60)
        self.assertEqual(receipt["state"], "hangup")
        row = self.adapter.get_job(job)
        self.assertEqual(row["reserved_cents"], row["spent_cents"])
        self.assertEqual(row["reserved_cents"], 0)
        # Billing can still be reconciled afterwards and lands in both.
        self.adapter.reconcile_bill(att, 42, "bill-shape-a")
        row = self.adapter.get_job(job)
        self.assertEqual((row["reserved_cents"], row["spent_cents"]), (42, 42))


class TestManualReleaseStateConsistency(Base):
    """The Oct 30, 2026 one-time manual release left jobs in exactly
    this state: terminal attempt, reserved == spent (zero recorded),
    billing_reconciled = 0. The code paths must treat that state as
    already consistent -- no double-count, no stuck reservation, and
    reconcile_bill still lands normally."""

    def test_manual_release_state(self):
        job, att = "job-manual", "11111111-2222-4333-8444-555555555555"
        self.prepare(job, att)
        self.drive_to_hangup(job, att, "man")
        # Recreate the manual-release end state by hand (the code path
        # above now produces it too, which is the point of the fix):
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE jobs SET reserved_cents=spent_cents, "
                     "billing_reconciled=0 WHERE job_id=?", (job,))
        conn.commit()
        conn.close()
        row = self.adapter.get_job(job)
        self.assertEqual(row["reserved_cents"], 0)
        self.assertEqual(row["billing_reconciled"], 0)
        # Freed space is usable at the full cap...
        self.prepare("job-after", "22222222-3333-4444-8555-666666666666",
                     estimated=200)
        # ...and a late bill for the manually released job still
        # reconciles into both fields.
        self.adapter.reconcile_bill(att, 88, "bill-manual")
        row = self.adapter.get_job(job)
        self.assertEqual((row["reserved_cents"], row["spent_cents"]), (88, 88))


class TestHangupSource(Base):
    def test_reconcile_reports_callee_hangup_source(self):
        job, att = "job-src", "11111111-2222-4333-8444-555555555555"
        self.prepare(job, att)
        self.adapter.claim_submission(att)
        self.adapter.record_response(att, {"data": {
            "call_control_id": CONTROL, "call_leg_id": LEG,
            "call_session_id": SESSION}})
        self.deliver(event("call.answered", "src-a", att))
        self.deliver(event("call.hangup", "src-h", att,
                           hangup_source="callee"))
        receipt = self.adapter.reconcile_call_record(att)
        self.assertTrue(receipt["hangup_event"])
        self.assertEqual(receipt["hangup_source"], "callee")

    def test_reconcile_hangup_source_absent_is_none(self):
        job, att = "job-nosrc", "11111111-2222-4333-8444-555555555555"
        self.prepare(job, att)
        self.adapter.claim_submission(att)
        self.adapter.record_response(att, {"data": {
            "call_control_id": CONTROL, "call_leg_id": LEG,
            "call_session_id": SESSION}})
        self.deliver(event("call.hangup", "nosrc-h", att))
        receipt = self.adapter.reconcile_call_record(att)
        self.assertTrue(receipt["hangup_event"])
        self.assertIsNone(receipt["hangup_source"])


if __name__ == "__main__":
    unittest.main()
