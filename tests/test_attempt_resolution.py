"""Tests for the operator-attested attempt resolution.

resolve_attempt_unsubmitted is the adapter's only non-evidence
transition, so these tests pin its guards: it closes refused or
never-submitted attempts only when no stored event correlates, no
carrier-verified create response is on record, and an operator
attestation with an evidence reference is supplied and recorded.

All keys, numbers, and ids are synthetic test fixtures. No network.
"""
import base64
import json
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
JOB = "job-resolution-001"
JOB2 = "job-resolution-002"
ATTEMPT = "11111111-2222-4333-8444-555555555555"
ATTEMPT2 = "22222222-3333-4444-8555-666666666666"
CONTROL = "v3:own-control-id"
LEG = "22222222-3333-4444-8555-666666666666"
SESSION = "33333333-4444-4555-8666-777777777777"

OPERATOR = "nathan (via Muse, test)"
EVIDENCE = "blocker-resolution-plan section 2"
ATTESTATION = "Carrier refused this submission before acceptance; no call was created."

_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes([42]) * 32)
PUBLIC = base64.b64encode(_PRIVATE.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def approval(job=JOB, attempt_irrelevant=None):
    return Approval(job_id=job, destination=TO, max_minutes=10,
                    estimated_cost_cents=200)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "resolution.sqlite"
        self.now = [NOW]
        self.adapter = TelnyxAdapter(
            self.path, PUBLIC,
            config=AdapterConfig(authorized_destinations=(TO,)),
            clock=lambda: self.now[0])
        self.adapter.prepare(job_id=JOB, connection_id=CONNECTION,
                             from_number=FROM, to_number=TO,
                             attempt_id=ATTEMPT, approval=approval())

    def tearDown(self):
        self.tmp.cleanup()

    def resolve(self, attempt=ATTEMPT, basis="carrier_refused",
                operator=OPERATOR, evidence_ref=EVIDENCE, attestation=ATTESTATION):
        return self.adapter.resolve_attempt_unsubmitted(
            attempt, basis=basis, operator=operator,
            evidence_ref=evidence_ref, attestation=attestation)

    def signed_event_body(self, event_id="ev-resolution-1"):
        payload = {"call_control_id": CONTROL, "call_leg_id": LEG,
                   "call_session_id": SESSION, "connection_id": CONNECTION,
                   "from": FROM, "to": TO,
                   "client_state": base64.b64encode(
                       json.dumps({"attempt_id": ATTEMPT}).encode()).decode()}
        event = {"data": {"record_type": "event", "id": event_id,
                          "event_type": "call.initiated",
                          "occurred_at": datetime.fromtimestamp(
                              NOW, timezone.utc).isoformat(),
                          "payload": payload},
                 "meta": {"attempt": 1}}
        return json.dumps(event, separators=(",", ":")).encode()


class TestResolution(Base):
    def test_carrier_refused_happy_path_records_attestation_and_frees_gate(self):
        self.adapter.claim_submission(ATTEMPT)
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "submit_unknown")
        # The gate is shut while the refused attempt sits in submit_unknown.
        self.adapter.prepare(job_id=JOB2, connection_id=CONNECTION,
                             from_number=FROM, to_number=TO,
                             attempt_id=ATTEMPT2, approval=approval(job=JOB2))
        with self.assertRaises(ValueError):
            self.adapter.claim_submission(ATTEMPT2)
        receipt = self.resolve()
        self.assertEqual(receipt["state"], "closed_unsubmitted")
        self.assertEqual(receipt["prior_state"], "submit_unknown")
        attempt = self.adapter.get_attempt(ATTEMPT)
        self.assertEqual(attempt["state"], "closed_unsubmitted")
        recorded = self.adapter.get_attempt_resolution(ATTEMPT)
        self.assertIsNotNone(recorded)
        self.assertEqual(recorded["operator"], OPERATOR)
        self.assertEqual(recorded["evidence_ref"], EVIDENCE)
        self.assertEqual(recorded["attestation"], ATTESTATION)
        self.assertEqual(recorded["basis"], "carrier_refused")
        self.assertEqual(recorded["prior_state"], "submit_unknown")
        # Gate freed: the second attempt may now claim.
        self.adapter.claim_submission(ATTEMPT2)
        self.assertEqual(self.adapter.get_attempt(ATTEMPT2)["state"], "submit_unknown")

    def test_bench_artifact_happy_path_keeps_fabricated_ids_for_audit(self):
        self.adapter.claim_submission(ATTEMPT)
        self.adapter.record_response(ATTEMPT, {"data": {
            "call_control_id": "bench-cc-0123456789abcdef",
            "call_leg_id": "bench-leg-0123456789abcdef",
            "call_session_id": "bench-sess-0123456789abcdef"}})
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "initiated")
        receipt = self.resolve(basis="not_a_carrier_attempt")
        self.assertEqual(receipt["prior_state"], "initiated")
        attempt = self.adapter.get_attempt(ATTEMPT)
        self.assertEqual(attempt["state"], "closed_unsubmitted")
        self.assertEqual(attempt["control_id"], "bench-cc-0123456789abcdef")

    def test_refused_when_a_stored_event_correlates(self):
        self.adapter.claim_submission(ATTEMPT)
        self.adapter.record_response(ATTEMPT, {"data": {
            "call_control_id": CONTROL, "call_leg_id": LEG,
            "call_session_id": SESSION}})
        body = self.signed_event_body()
        headers = {"telnyx-timestamp": str(NOW),
                   "telnyx-signature-ed25519": base64.b64encode(
                       _PRIVATE.sign(str(NOW).encode() + b"|" + body)).decode()}
        self.adapter.ingest_webhook(body, headers)
        with self.assertRaises(ValueError):
            self.resolve(basis="not_a_carrier_attempt")
        # Still refused after the event is processed (non-regressing here:
        # record_response already advanced the attempt to initiated).
        self.assertEqual(self.adapter.process_event("ev-resolution-1"),
                         "nonregressing_event_stored")
        with self.assertRaises(ValueError):
            self.resolve(basis="not_a_carrier_attempt")
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "initiated")
        self.assertIsNone(self.adapter.get_attempt_resolution(ATTEMPT))

    def test_refused_without_evidence_reference_or_attestation(self):
        self.adapter.claim_submission(ATTEMPT)
        with self.assertRaises(ValueError):
            self.resolve(evidence_ref="")
        with self.assertRaises(ValueError):
            self.resolve(attestation="")
        with self.assertRaises(ValueError):
            self.resolve(operator="")
        with self.assertRaises(ValueError):
            self.resolve(basis="guessed")
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "submit_unknown")
        self.assertIsNone(self.adapter.get_attempt_resolution(ATTEMPT))

    def test_refused_when_carrier_shaped_ids_are_bound(self):
        self.adapter.claim_submission(ATTEMPT)
        self.adapter.record_response(ATTEMPT, {"data": {
            "call_control_id": CONTROL, "call_leg_id": LEG,
            "call_session_id": SESSION}})
        # Zero events, but a carrier-shaped create response is on record:
        # neither basis may retire this attempt.
        with self.assertRaises(ValueError):
            self.resolve(basis="not_a_carrier_attempt")
        with self.assertRaises(ValueError):
            self.resolve(basis="carrier_refused")
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "initiated")

    def test_refused_from_unclaimed_and_terminal_states_and_twice(self):
        # prepared (never claimed): not resolvable.
        with self.assertRaises(ValueError):
            self.resolve()
        self.adapter.claim_submission(ATTEMPT)
        self.resolve()
        # Already resolved: a second attestation is refused.
        with self.assertRaises(ValueError):
            self.resolve()
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "closed_unsubmitted")


CRV_EVIDENCE = "telnyx detailed records report (synthetic fixture)"
CRV_ATTESTATION_A = (
    "The signed call.hangup event for this call was never stored. The "
    "carrier record shows the call ended at the recorded hangup time.")
CRV_ATTESTATION_B = (
    "A carrier records search of the dial window found exactly one "
    "call, under a different control id, and none for this attempt.")


class TestCarrierRecordVerified(Base):
    """Basis 'carrier_record_verified': the attestation cites a
    carrier call record. Shape A closes a live (answered) call whose
    terminal event was never stored; shape B closes an unbound
    submit_unknown attempt a records search proves never became a
    call. Every other shape must refuse."""

    def event_body(self, event_id, event_type, control=CONTROL):
        payload = {"call_control_id": control, "call_leg_id": LEG,
                   "call_session_id": SESSION, "connection_id": CONNECTION,
                   "from": FROM, "to": TO,
                   "client_state": base64.b64encode(
                       json.dumps({"attempt_id": ATTEMPT}).encode()).decode()}
        event = {"data": {"record_type": "event", "id": event_id,
                          "event_type": event_type,
                          "occurred_at": datetime.fromtimestamp(
                              NOW, timezone.utc).isoformat(),
                          "payload": payload},
                 "meta": {"attempt": 1}}
        return json.dumps(event, separators=(",", ":")).encode()

    def ingest(self, event_id, event_type, control=CONTROL):
        body = self.event_body(event_id, event_type, control=control)
        headers = {"telnyx-timestamp": str(NOW),
                   "telnyx-signature-ed25519": base64.b64encode(
                       _PRIVATE.sign(str(NOW).encode() + b"|" + body)).decode()}
        return self.adapter.ingest_webhook(body, headers)

    def resolve_crv(self, attempt=ATTEMPT, hangup_at=None,
                    attestation=CRV_ATTESTATION_A, **overrides):
        kwargs = dict(basis="carrier_record_verified", operator=OPERATOR,
                      evidence_ref=CRV_EVIDENCE, attestation=attestation,
                      carrier_hangup_at=hangup_at)
        kwargs.update(overrides)
        return self.adapter.resolve_attempt_unsubmitted(attempt, **kwargs)

    def force_state(self, state, control_id="keep"):
        import sqlite3
        conn = sqlite3.connect(self.path)
        if control_id == "keep":
            conn.execute("UPDATE attempts SET state=? WHERE attempt_id=?",
                         (state, ATTEMPT))
        else:
            conn.execute(
                "UPDATE attempts SET state=?,control_id=? WHERE attempt_id=?",
                (state, control_id, ATTEMPT))
        conn.commit()
        conn.close()

    def drive_to_answered(self):
        self.adapter.claim_submission(ATTEMPT)
        self.adapter.record_response(ATTEMPT, {"data": {
            "call_control_id": CONTROL, "call_leg_id": LEG,
            "call_session_id": SESSION}})
        self.ingest("ev-crv-init", "call.initiated")
        self.adapter.process_event("ev-crv-init")
        self.ingest("ev-crv-ans", "call.answered")
        self.assertEqual(self.adapter.process_event("ev-crv-ans"), "applied")
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "answered")

    def test_shape_a_happy_path_records_hangup_and_frees_gate(self):
        self.drive_to_answered()
        self.adapter.prepare(job_id=JOB2, connection_id=CONNECTION,
                              from_number=FROM, to_number=TO,
                              attempt_id=ATTEMPT2, approval=approval(job=JOB2))
        with self.assertRaises(ValueError):
            self.adapter.claim_submission(ATTEMPT2)
        receipt = self.resolve_crv(hangup_at=NOW + 59)
        self.assertEqual(receipt["state"], "hangup")
        self.assertEqual(receipt["prior_state"], "answered")
        self.assertEqual(receipt["hangup_at"], NOW + 59)
        attempt = self.adapter.get_attempt(ATTEMPT)
        self.assertEqual(attempt["state"], "hangup")
        self.assertEqual(attempt["hangup_at"], NOW + 59)
        self.assertEqual(attempt["control_id"], CONTROL)
        recorded = self.adapter.get_attempt_resolution(ATTEMPT)
        self.assertEqual(recorded["basis"], "carrier_record_verified")
        self.assertEqual(recorded["prior_state"], "answered")
        self.assertEqual(recorded["attestation"], CRV_ATTESTATION_A)
        self.adapter.claim_submission(ATTEMPT2)
        self.assertEqual(self.adapter.get_attempt(ATTEMPT2)["state"], "submit_unknown")

    def test_shape_a_refused_without_carrier_hangup_time(self):
        self.drive_to_answered()
        with self.assertRaises(ValueError):
            self.resolve_crv()
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "answered")
        self.assertIsNone(self.adapter.get_attempt_resolution(ATTEMPT))

    def test_shape_a_refused_without_control_id(self):
        self.adapter.claim_submission(ATTEMPT)
        self.force_state("answered", control_id=None)
        with self.assertRaises(ValueError):
            self.resolve_crv(hangup_at=NOW + 59)
        self.assertIsNone(self.adapter.get_attempt_resolution(ATTEMPT))

    def test_shape_a_refused_without_stored_events(self):
        self.adapter.claim_submission(ATTEMPT)
        self.adapter.record_response(ATTEMPT, {"data": {
            "call_control_id": CONTROL, "call_leg_id": LEG,
            "call_session_id": SESSION}})
        self.force_state("answered")
        with self.assertRaises(ValueError):
            self.resolve_crv(hangup_at=NOW + 59)
        self.assertIsNone(self.adapter.get_attempt_resolution(ATTEMPT))

    def test_shape_a_refused_when_hangup_event_is_stored(self):
        self.drive_to_answered()
        self.ingest("ev-crv-hangup", "call.hangup")
        with self.assertRaises(ValueError):
            self.resolve_crv(hangup_at=NOW + 59)
        # The honest path was there all along: processing applies it.
        self.assertEqual(self.adapter.process_event("ev-crv-hangup"), "applied")
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "hangup")
        self.assertIsNone(self.adapter.get_attempt_resolution(ATTEMPT))

    def test_refused_on_terminal_attempt(self):
        self.drive_to_answered()
        self.ingest("ev-crv-hangup2", "call.hangup")
        self.adapter.process_event("ev-crv-hangup2")
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "hangup")
        with self.assertRaises(ValueError):
            self.resolve_crv(hangup_at=NOW + 59)
        self.assertIsNone(self.adapter.get_attempt_resolution(ATTEMPT))

    def test_shape_b_happy_path(self):
        self.adapter.claim_submission(ATTEMPT)
        receipt = self.resolve_crv(attestation=CRV_ATTESTATION_B)
        self.assertEqual(receipt["state"], "closed_unsubmitted")
        self.assertEqual(receipt["prior_state"], "submit_unknown")
        self.assertNotIn("hangup_at", receipt)
        attempt = self.adapter.get_attempt(ATTEMPT)
        self.assertEqual(attempt["state"], "closed_unsubmitted")
        self.assertIsNone(attempt["hangup_at"])
        recorded = self.adapter.get_attempt_resolution(ATTEMPT)
        self.assertEqual(recorded["basis"], "carrier_record_verified")
        self.assertEqual(recorded["attestation"], CRV_ATTESTATION_B)

    def test_shape_b_refused_with_stored_event(self):
        self.adapter.claim_submission(ATTEMPT)
        # Stored (never processed) event correlated by client_state hint.
        self.ingest("ev-crv-hint", "call.initiated", control="v3:unknown-control")
        with self.assertRaises(ValueError):
            self.resolve_crv(attestation=CRV_ATTESTATION_B)
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "submit_unknown")
        self.assertIsNone(self.adapter.get_attempt_resolution(ATTEMPT))

    def test_shape_b_refused_with_hangup_time(self):
        self.adapter.claim_submission(ATTEMPT)
        with self.assertRaises(ValueError):
            self.resolve_crv(hangup_at=NOW + 59, attestation=CRV_ATTESTATION_B)
        self.assertIsNone(self.adapter.get_attempt_resolution(ATTEMPT))

    def test_carrier_record_refused_with_empty_fields(self):
        self.adapter.claim_submission(ATTEMPT)
        with self.assertRaises(ValueError):
            self.resolve_crv(evidence_ref="")
        with self.assertRaises(ValueError):
            self.resolve_crv(attestation="")
        with self.assertRaises(ValueError):
            self.resolve_crv(operator="")
        self.assertEqual(self.adapter.get_attempt(ATTEMPT)["state"], "submit_unknown")
        self.assertIsNone(self.adapter.get_attempt_resolution(ATTEMPT))


if __name__ == "__main__":
    unittest.main()
