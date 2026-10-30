"""Muse's Telnyx adapter: durable job/attempt state, gated dialling,
idempotent webhook acceptance, and bounded hangup.

State machine (attempts):
    prepared -> submit_unknown -> initiated -> answered -> hangup
    prepared -> cancelled_before_submit   (stop before claim only)
    submit_unknown|initiated -> closed_unsubmitted
        (operator-attested resolution only -- never an evidence path.
        resolve_attempt_unsubmitted() requires zero stored events for
        the attempt, no carrier-verified create response on record,
        and a recorded operator attestation with an evidence reference
        in attempt_resolutions. It exists for submissions the carrier
        refused before acceptance and for attempts that were never
        carrier submissions at all: cases no signed event or verified
        response can ever arrive for. It must never close an attempt
        that may have become a real call.)
    answered -> hangup; submit_unknown -> closed_unsubmitted
        (basis 'carrier_record_verified' of the same method: the
        operator attests to a carrier call record instead of to the
        absence of evidence. Shape A closes a live call whose
        terminal signed event was never stored, taking hangup_at
        from the carrier record; shape B closes an unbound
        submit_unknown attempt that a carrier records search proves
        never became a call. See the method docstring.)

Rules that must not be weakened:
- prepare() persists the job, the attempt, the exact dial payload, the
  approval, and the budget reservation BEFORE claim() is ever called.
  claim() is a single-use, atomic transition to submit_unknown.
  A timeout, a dropped response, or a process crash after claim()
  leaves submit_unknown -- UNKNOWN, never an automatic redial, and
  never a second claim. Recovery is only through a verified create
  response or a verified signed event, never by guessing.
- Webhook acceptance is durable and separate from processing:
  verify signature over raw bytes -> parse strictly -> store raw
  bytes, headers, and canonical data in one transaction -> return
  acknowledgement (stored, not processed). process_event() then
  applies the stored event in the same transaction that marks it
  processed, so a crash between ack and processing cannot lose or
  double-apply it.
- Correlation binds an event to the exact pending attempt AND route.
  A control-id match and a client_state hint, when both are present,
  must agree. A session id is never queried on its own: an event
  whose control id is unknown and which carries no hint is
  quarantined even if its session id happens to match another leg.
  A quarantined event is stored, reported, and never executed; it
  needs explicit operator review, not an automatic retry.
- Stop is a persisted intent, not a call id. request_stop() works
  for a job with no attempt yet (a pending-stop record), for an
  attempt with no call id yet (hangup waits for identity), and for
  a live leg (one persisted hangup command). A hangup command ACK
  records acknowledgement only; it is not a hangup event, and a
  hangup event is not a final bill -- billing reconciliation is a
  separate, explicit step after hangup.
- Hangup is monotonic and absorbing: once hangup, no later event or
  late create response reopens the attempt.
- Budget reservations follow the attempt lifecycle. prepare() holds
  the job's full estimate in reserved_cents while its attempt is in
  flight, and every transition of the attempt into a terminal state
  (hangup, closed_unsubmitted, cancelled_before_submit) converts the
  reservation to the job's recorded spend in the same transaction
  (reserved_cents := spent_cents). reconcile_bill() then records the
  actual bill into both fields. The monthly cap therefore sums
  in-flight estimates plus finished jobs' recorded spend -- never a
  finished job's stale estimate. (Before this rule, reservations were
  never released: 20 finished October jobs x 100 cents of estimates
  exhausted the $20 cap and refused a 21st call whose real monthly
  spend was ~0. A one-time operator release applied the same
  conversion by hand on Oct 30, 2026.)
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from contextlib import contextmanager
from uuid import NAMESPACE_URL, UUID, uuid5

from .config import AdapterConfig, Approval
from .verify import WebhookRejected, verify_webhook
from .wire import (
    canonical_json,
    check_identifier,
    decode_client_state,
    dial_request,
    hangup_request,
    parse_event,
    response_ids,
)

_LIFECYCLE_RANK = {"call.initiated": 1, "call.answered": 2, "call.hangup": 3}
_STATE_RANK = {"submit_unknown": 0, "initiated": 1, "answered": 2, "hangup": 3,
               "closed_unsubmitted": 3}
_STATE_FROM_EVENT = {"call.initiated": "initiated", "call.answered": "answered", "call.hangup": "hangup"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  job_id TEXT PRIMARY KEY, connection_id TEXT NOT NULL,
  from_number TEXT NOT NULL, to_number TEXT NOT NULL,
  max_minutes INTEGER NOT NULL, estimated_cents INTEGER NOT NULL,
  reserved_cents INTEGER NOT NULL, spent_cents INTEGER NOT NULL DEFAULT 0,
  stop INTEGER NOT NULL DEFAULT 0, approval_ref TEXT, bill_ref TEXT,
  billing_reconciled INTEGER NOT NULL DEFAULT 0,
  billing_month TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pending_stops(
  job_id TEXT PRIMARY KEY, reason TEXT);
CREATE TABLE IF NOT EXISTS attempts(
  attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, payload_json TEXT NOT NULL,
  state TEXT NOT NULL, control_id TEXT UNIQUE, leg_id TEXT, session_id TEXT,
  stop INTEGER NOT NULL DEFAULT 0, hangup_command TEXT,
  hangup_at REAL, hangup_ack INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(
  event_id TEXT PRIMARY KEY, digest TEXT NOT NULL, raw_body BLOB NOT NULL,
  headers_json TEXT NOT NULL, data_json TEXT NOT NULL, occurred REAL NOT NULL,
  processed INTEGER NOT NULL DEFAULT 0, outcome TEXT);
CREATE TABLE IF NOT EXISTS media_jobs(
  attempt_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
  expires_at REAL NOT NULL, claimed_at REAL, quota_json TEXT NOT NULL DEFAULT '[]',
  quota_settled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS quota_ledger(
  name TEXT NOT NULL, period TEXT NOT NULL, used REAL NOT NULL,
  PRIMARY KEY(name,period));
CREATE TABLE IF NOT EXISTS attempt_resolutions(
  attempt_id TEXT PRIMARY KEY, prior_state TEXT NOT NULL,
  basis TEXT NOT NULL, operator TEXT NOT NULL,
  evidence_ref TEXT NOT NULL, attestation TEXT NOT NULL,
  created_at REAL NOT NULL);
"""


def _ids_are_fabricated(bound) -> bool:
    """True only when every bound id fails the carrier's issued shapes.

    Carrier control ids carry the 'v3:' version prefix and carrier leg
    and session ids are UUIDs. A binding that matches neither shape on
    any of the three ids (e.g. a test harness's fabricated 'bench-...'
    ids) demonstrably did not come from a carrier create response.
    Any doubt returns False: partially bound or carrier-shaped ids are
    treated as a real create response on record.
    """
    control_id, leg_id, session_id = bound
    if not control_id or control_id.startswith("v3:"):
        return False
    for value in (leg_id, session_id):
        if not value:
            return False
        try:
            UUID(value)
        except (ValueError, AttributeError, TypeError):
            continue
        return False
    return True


class SubmissionUnknown(RuntimeError):
    """Carrier request outcome is unknown; do not redial, reconcile."""


class RetryWindowElapsed(ValueError):
    pass


class TelnyxAdapter:
    def __init__(self, db_path, public_key_b64: str, config: AdapterConfig | None = None,
                 clock=time.time):
        self.db_path = str(db_path)
        self.public_key_b64 = public_key_b64
        self.config = config or AdapterConfig()
        self.clock = clock
        with self._tx() as conn:
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
            if "billing_month" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN billing_month TEXT NOT NULL DEFAULT ''")
            conn.execute(
                "UPDATE jobs SET billing_month=? WHERE billing_month=''",
                (time.strftime("%Y-%m", time.gmtime(self.clock())),))
            media_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(media_jobs)")}
            if "quota_json" not in media_columns:
                conn.execute(
                    "ALTER TABLE media_jobs ADD COLUMN quota_json TEXT NOT NULL DEFAULT '[]'")
            if "quota_settled" not in media_columns:
                conn.execute(
                    "ALTER TABLE media_jobs ADD COLUMN quota_settled INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def _tx(self):
        conn = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- preparation, approval, budget -----------------------------------

    def prepare(self, *, job_id: str, connection_id: str, from_number: str,
                to_number: str, attempt_id: str, approval: Approval,
                max_minutes: int = 10) -> dict:
        check_identifier(job_id)
        check_identifier(attempt_id)
        if approval is None or approval.job_id != job_id:
            raise ValueError("approval must name this exact job")
        if approval.destination != to_number:
            raise ValueError("approval destination does not match dial destination")
        if approval.max_minutes != max_minutes:
            raise ValueError("approval max_minutes does not match job max_minutes")
        if not 1 <= max_minutes <= 60:
            raise ValueError("max_minutes must be 1..60")
        if not self.config.authorized_destinations:
            raise ValueError("authorized destination allowlist is empty")
        if to_number not in self.config.authorized_destinations:
            raise ValueError("destination is not on the authorized allowlist")
        if not isinstance(approval.estimated_cost_cents, int) or approval.estimated_cost_cents < 0:
            raise ValueError("estimated cost must be a nonnegative integer")
        if approval.estimated_cost_cents > self.config.per_call_cap_cents:
            raise ValueError("estimated cost exceeds per-call cap")
        request = dial_request(connection_id, from_number, to_number, attempt_id,
                               max_minutes=max_minutes)
        payload = canonical_json(request["json"])
        with self._tx() as conn:
            existing_job = conn.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if existing_job:
                if (existing_job["to_number"] != to_number
                        or existing_job["connection_id"] != connection_id
                        or existing_job["from_number"] != from_number
                        or existing_job["max_minutes"] != max_minutes
                        or existing_job["estimated_cents"] != approval.estimated_cost_cents
                        or existing_job["approval_ref"] != approval.approver_ref):
                    raise ValueError("conflicting job identity")
                existing_attempt = conn.execute(
                    "SELECT attempt_id FROM attempts WHERE job_id=?", (job_id,)).fetchone()
                if existing_attempt and existing_attempt["attempt_id"] != attempt_id:
                    raise ValueError("approval already belongs to another attempt")
            billing_month = time.strftime("%Y-%m", time.gmtime(self.clock()))
            reserved_total = conn.execute(
                "SELECT COALESCE(SUM(reserved_cents),0) FROM jobs WHERE billing_month=?",
                (billing_month,)).fetchone()[0]
            if not existing_job and reserved_total + approval.estimated_cost_cents > self.config.monthly_cap_cents:
                raise ValueError("monthly budget cap would be exceeded")
            pending_stop = conn.execute(
                "SELECT 1 FROM pending_stops WHERE job_id=?", (job_id,)).fetchone()
            if not existing_job:
                conn.execute(
                    "INSERT INTO jobs(job_id,connection_id,from_number,to_number,"
                    "max_minutes,estimated_cents,reserved_cents,stop,approval_ref,billing_month)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (job_id, connection_id, from_number, to_number, max_minutes,
                     approval.estimated_cost_cents, approval.estimated_cost_cents,
                     1 if pending_stop else 0, approval.approver_ref, billing_month))
            old = conn.execute(
                "SELECT payload_json FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if old and old["payload_json"] != payload:
                raise ValueError("conflicting attempt identity")
            state = "cancelled_before_submit" if pending_stop else "prepared"
            stop = 1 if pending_stop else 0
            conn.execute(
                "INSERT OR IGNORE INTO attempts(attempt_id,job_id,payload_json,state,stop)"
                " VALUES(?,?,?,?,?)", (attempt_id, job_id, payload, state, stop))
            if state == "cancelled_before_submit":
                # Born terminal (a stop predates the job): the attempt
                # can never be claimed, so its reservation converts to
                # recorded spend (zero) immediately.
                self._release_job_reservation(conn, job_id)
        return request

    @staticmethod
    def _release_job_reservation(conn, job_id: str) -> None:
        """Convert a finished job's budget reservation to recorded spend.

        Called in the same transaction as the attempt's transition
        into a terminal state, and from reconcile_bill() once the
        actual bill is known. Idempotent: a job already at
        reserved == spent is untouched in effect.
        """
        conn.execute(
            "UPDATE jobs SET reserved_cents=spent_cents WHERE job_id=?",
            (job_id,))

    def get_attempt(self, attempt_id: str) -> dict:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if not row:
                raise ValueError("unknown attempt")
            return dict(row)

    def get_job(self, job_id: str) -> dict:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise ValueError("unknown job")
            return dict(row)

    # -- submission -------------------------------------------------------

    def claim_submission(self, attempt_id: str) -> dict:
        """Single-use claim. Must be called before any network request."""
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if not row or row["state"] != "prepared" or row["stop"]:
                raise ValueError("no redial: reconcile the existing attempt")
            active = conn.execute(
                "SELECT COUNT(*) FROM attempts WHERE state IN "
                "('submit_unknown','initiated','answered')").fetchone()[0]
            limit = self.config.max_concurrent_calls
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise ValueError('invalid concurrent-call limit')
            if active >= limit:
                raise ValueError('concurrent-call limit: reconcile active or unknown calls first')
            conn.execute(
                "UPDATE attempts SET state=? WHERE attempt_id=?", ("submit_unknown", attempt_id))
            return {"method": "POST", "path": "/v2/calls",
                    "json": json.loads(row["payload_json"])}

    def submit_via(self, transport, attempt_id: str):
        """Claim, then send once via the given transport.

        Any transport exception, or any unparseable response, leaves
        submit_unknown and raises SubmissionUnknown. The caller must
        NOT retry with a new attempt or a new claim.
        """
        if not getattr(transport, "is_test_transport", False) and not self.config.live_enabled:
            raise ValueError("live transport is disabled")
        request = self.claim_submission(attempt_id)
        try:
            status, response = transport.dial(request["path"], request["json"])
        except Exception as exc:
            raise SubmissionUnknown("dial outcome unknown after claim") from exc
        if status != 200:
            # Carrier refused before acceptance: this is still unknown
            # to the adapter's recovery model unless a signed event or
            # a verified response later proves otherwise. Do not guess.
            raise SubmissionUnknown(f"carrier returned {status} after claim")
        try:
            self.record_response(attempt_id, response)
        except Exception as exc:
            raise SubmissionUnknown("create response could not be bound") from exc
        return response

    def record_response(self, attempt_id: str, response: dict) -> tuple[str, str, str]:
        ids = response_ids(response)
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if not row or row["state"] in ("prepared", "cancelled_before_submit"):
                raise ValueError("no submission claimed for this attempt")
            self._bind(conn, row, ids)
            if row["state"] == "submit_unknown":
                conn.execute(
                    "UPDATE attempts SET state=? WHERE attempt_id=?", ("initiated", attempt_id))
        return ids

    @staticmethod
    def _bind(conn, row, ids):
        if row["control_id"] and (row["control_id"], row["leg_id"], row["session_id"]) != ids:
            raise ValueError("conflicting provider identities")
        conn.execute(
            "UPDATE attempts SET control_id=?,leg_id=?,session_id=? WHERE attempt_id=?",
            (*ids, row["attempt_id"]))

    # -- operator-attested resolution ------------------------------------

    @staticmethod
    def _correlated_events(conn, row) -> list:
        """Parsed stored events correlated to this attempt, by the
        same rule _apply uses (see _correlated_event_count)."""
        events = []
        for event in conn.execute("SELECT data_json FROM events").fetchall():
            data = json.loads(event["data_json"])
            payload = data.get("payload", {})
            if row["control_id"] and payload.get("call_control_id") == row["control_id"]:
                events.append(data)
                continue
            hint = None
            if payload.get("client_state"):
                try:
                    hint = decode_client_state(payload["client_state"])
                except Exception:
                    hint = None
            if hint is not None and hint == row["attempt_id"]:
                events.append(data)
        return events

    @staticmethod
    def _correlated_event_count(conn, row) -> int:
        """Stored events correlated to this attempt, by the same rule
        _apply uses: the bound control id, or a client_state hint
        naming this attempt. An undecodable hint names nothing."""
        count = 0
        for event in conn.execute("SELECT data_json FROM events").fetchall():
            payload = json.loads(event["data_json"]).get("payload", {})
            if row["control_id"] and payload.get("call_control_id") == row["control_id"]:
                count += 1
                continue
            hint = None
            if payload.get("client_state"):
                try:
                    hint = decode_client_state(payload["client_state"])
                except Exception:
                    hint = None
            if hint is not None and hint == row["attempt_id"]:
                count += 1
        return count

    def resolve_attempt_unsubmitted(self, attempt_id: str, *, basis: str,
                                    operator: str, evidence_ref: str,
                                    attestation: str,
                                    carrier_hangup_at: float | None = None) -> dict:
        """Terminally close an attempt on an operator attestation.

        This is an operator attestation, not evidence processing, and
        every guard must hold in one transaction. operator,
        evidence_ref and attestation are always required; the
        attestation is stored verbatim in attempt_resolutions, and an
        attempt with a recorded resolution cannot be resolved again.

        Bases 'carrier_refused' and 'not_a_carrier_attempt' close an
        attempt that never became a carrier call:

        - basis is 'carrier_refused' (the carrier refused the
          submission before acceptance) or 'not_a_carrier_attempt'
          (the attempt was never submitted to the carrier at all);
        - the attempt is in submit_unknown or initiated;
        - zero stored webhook events correlate to the attempt;
        - no carrier-verified create response is on record: for
          'carrier_refused' the attempt must still be submit_unknown
          with nothing bound (this adapter binds ids only from a
          create response or an event, and there are no events); for
          'not_a_carrier_attempt', any bound ids must be demonstrably
          fabricated (see _ids_are_fabricated).

        Basis 'carrier_record_verified' instead rests on a carrier
        call record the operator cites in evidence_ref/attestation,
        and matches exactly one of two shapes:

        - Shape A (lost terminal event): the attempt is answered,
          carries a bound carrier control id, and has stored signed
          events proving the call was live -- but no stored
          call.hangup event, so the signed-evidence path can never
          complete it (e.g. the event's delivery was lost). The
          operator must supply carrier_hangup_at, the end time from
          the carrier record; the attempt moves to hangup with that
          hangup_at. If a call.hangup event IS stored, this basis
          refuses: process the event instead.
        - Shape B (no carrier call created): the attempt is
          submit_unknown, unbound, and has zero stored events, and
          the attestation cites a carrier records search (report and
          window) proving no call was created for the submission.
          carrier_hangup_at must not be supplied. The attempt moves
          to closed_unsubmitted.

        On success under any basis the attempt leaves the states the
        submission gate counts, and media attach refuses the terminal
        states. Any bound ids stay on the attempt row, untouched, for
        audit.
        """
        check_identifier(operator)
        check_identifier(evidence_ref)
        if basis not in ("carrier_refused", "not_a_carrier_attempt",
                         "carrier_record_verified"):
            raise ValueError("unknown resolution basis")
        if not isinstance(attestation, str) or not attestation.strip():
            raise ValueError("an attestation is required")
        if len(attestation) > 4096:
            raise ValueError("attestation is oversized")
        if basis != "carrier_record_verified" and carrier_hangup_at is not None:
            raise ValueError(
                "carrier_hangup_at is only valid with basis 'carrier_record_verified'")
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if not row:
                raise ValueError("unknown attempt")
            existing = conn.execute(
                "SELECT 1 FROM attempt_resolutions WHERE attempt_id=?",
                (attempt_id,)).fetchone()
            if existing:
                raise ValueError("attempt already has a recorded resolution")
            bound = (row["control_id"], row["leg_id"], row["session_id"])
            new_hangup_at = None
            if basis == "carrier_record_verified":
                new_state, new_hangup_at = self._carrier_record_shape(
                    conn, row, bound, carrier_hangup_at)
            else:
                if row["state"] not in ("submit_unknown", "initiated"):
                    raise ValueError("attempt is not in a resolvable state")
                if self._correlated_event_count(conn, row):
                    raise ValueError("stored webhook events exist for this attempt")
                if basis == "carrier_refused":
                    if row["state"] != "submit_unknown" or any(bound):
                        raise ValueError(
                            "carrier_refused requires an unbound submit_unknown attempt")
                elif any(bound) and not _ids_are_fabricated(bound):
                    raise ValueError(
                        "bound ids are carrier-shaped: a create response is on record")
                new_state = "closed_unsubmitted"
            created = self.clock()
            conn.execute(
                "INSERT INTO attempt_resolutions(attempt_id,prior_state,basis,"
                "operator,evidence_ref,attestation,created_at) VALUES(?,?,?,?,?,?,?)",
                (attempt_id, row["state"], basis, operator, evidence_ref,
                 attestation, created))
            if new_hangup_at is not None:
                conn.execute(
                    "UPDATE attempts SET state=?,hangup_at=? WHERE attempt_id=?",
                    (new_state, new_hangup_at, attempt_id))
            else:
                conn.execute(
                    "UPDATE attempts SET state=? WHERE attempt_id=?",
                    (new_state, attempt_id))
            # Both resolution outcomes are terminal (hangup or
            # closed_unsubmitted): release the job's reservation in
            # the same transaction.
            self._release_job_reservation(conn, row["job_id"])
            receipt = {"attempt_id": attempt_id, "prior_state": row["state"],
                       "state": new_state, "basis": basis,
                       "operator": operator, "evidence_ref": evidence_ref,
                       "created_at": created}
            if new_hangup_at is not None:
                receipt["hangup_at"] = new_hangup_at
            return receipt

    def _carrier_record_shape(self, conn, row, bound,
                              carrier_hangup_at) -> tuple[str, float | None]:
        """Fail-closed shape match for basis 'carrier_record_verified'.

        Returns (new_state, hangup_at): ('hangup', carrier_hangup_at)
        for shape A, ('closed_unsubmitted', None) for shape B. Raises
        ValueError on any mismatch -- a state or binding that fits
        neither shape is not resolvable on a carrier record.
        """
        if row["state"] == "answered":
            # Shape A: a live call whose terminal event was never stored.
            if not row["control_id"] or not row["control_id"].startswith("v3:"):
                raise ValueError(
                    "carrier_record_verified shape A requires a bound carrier control id")
            events = self._correlated_events(conn, row)
            if not events:
                raise ValueError(
                    "no stored signed events prove this call was live")
            if any(event.get("event_type") == "call.hangup" for event in events):
                raise ValueError(
                    "a call.hangup event is stored for this attempt: process it instead")
            if (not isinstance(carrier_hangup_at, (int, float))
                    or isinstance(carrier_hangup_at, bool)
                    or not math.isfinite(carrier_hangup_at)
                    or carrier_hangup_at <= 0):
                raise ValueError(
                    "carrier_record_verified shape A requires the carrier-recorded"
                    " hangup time")
            return "hangup", float(carrier_hangup_at)
        if row["state"] == "submit_unknown" and not any(bound):
            # Shape B: a submission a records search proves never
            # became a carrier call.
            if carrier_hangup_at is not None:
                raise ValueError(
                    "carrier_hangup_at applies only to a live call closed on a"
                    " carrier record")
            if self._correlated_event_count(conn, row):
                raise ValueError("stored webhook events exist for this attempt")
            return "closed_unsubmitted", None
        raise ValueError(
            "attempt state does not match a carrier-record resolution shape")

    def get_attempt_resolution(self, attempt_id: str) -> dict | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM attempt_resolutions WHERE attempt_id=?",
                (attempt_id,)).fetchone()
            return dict(row) if row else None

    # -- webhooks: durable acceptance, then idempotent processing -------

    def ingest_webhook(self, raw_body: bytes, headers) -> dict:
        # Verify over raw bytes BEFORE parsing anything.
        verify_webhook(raw_body, headers, self.public_key_b64, now=self.clock(),
                       body_cap=self.config.webhook_body_cap_bytes,
                       freshness_seconds=self.config.webhook_freshness_seconds)
        data, occurred = parse_event(raw_body)
        encoded = canonical_json(data)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        header_list = list(headers.items()) if hasattr(headers, "items") else list(headers)
        with self._tx() as conn:
            old = conn.execute(
                "SELECT digest FROM events WHERE event_id=?", (data["id"],)).fetchone()
            if old:
                if old["digest"] != digest:
                    raise ValueError("event id collision")
                return {"accepted": True, "duplicate": True}
            conn.execute(
                "INSERT INTO events(event_id,digest,raw_body,headers_json,data_json,occurred)"
                " VALUES(?,?,?,?,?,?)",
                (data["id"], digest, raw_body, json.dumps(header_list), encoded, occurred))
        return {"accepted": True, "duplicate": False}

    def event_status(self, event_id: str):
        with self._tx() as conn:
            row = conn.execute(
                "SELECT processed,outcome FROM events WHERE event_id=?", (event_id,)).fetchone()
            return dict(row) if row else None

    def register_media_job(self, attempt_id: str, payload: dict,
                           expires_at: float,
                           quota_reservations: list[tuple[str, str, float, float]] | None = None) -> None:
        check_identifier(attempt_id)
        encoded = canonical_json(payload)
        with self._tx() as conn:
            old = conn.execute(
                "SELECT 1 FROM media_jobs WHERE attempt_id=?", (attempt_id,)).fetchone()
            if old:
                raise ValueError("attempt is already registered or consumed")
            reservations = quota_reservations or []
            quota_totals = []
            seen = set()
            for name, period, amount, limit in reservations:
                check_identifier(name)
                check_identifier(period)
                if (name, period) in seen:
                    raise ValueError('duplicate quota reservation key')
                seen.add((name, period))
                if not all(math.isfinite(float(v)) and float(v) >= 0 for v in (amount, limit)):
                    raise ValueError("quota values must be finite and nonnegative")
                row = conn.execute(
                    "SELECT used FROM quota_ledger WHERE name=? AND period=?",
                    (name, period)).fetchone()
                total = (float(row["used"]) if row else 0.0) + float(amount)
                if total > float(limit):
                    raise ValueError("quota limit would be exceeded")
                quota_totals.append(total)
            for (name, period, _amount, _limit), total in zip(
                    reservations, quota_totals):
                conn.execute(
                    "INSERT INTO quota_ledger(name,period,used) VALUES(?,?,?) "
                    "ON CONFLICT(name,period) DO UPDATE SET used=excluded.used",
                    (name, period, total))
            conn.execute(
                "INSERT INTO media_jobs(attempt_id,payload_json,expires_at,quota_json) "
                "VALUES(?,?,?,?)",
                (attempt_id, encoded, float(expires_at),
                 canonical_json(reservations)))

    def claim_media_job(self, attempt_id: str, now: float) -> dict | None:
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE media_jobs SET claimed_at=? WHERE attempt_id=? "
                "AND claimed_at IS NULL AND expires_at>=?",
                (float(now), attempt_id, float(now)))
            if cursor.rowcount != 1:
                return None
            row = conn.execute(
                "SELECT payload_json FROM media_jobs WHERE attempt_id=?",
                (attempt_id,)).fetchone()
            return json.loads(row["payload_json"])

    def peek_media_job(self, attempt_id: str, now: float) -> dict | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT payload_json FROM media_jobs WHERE attempt_id=? "
                "AND claimed_at IS NULL AND expires_at>=?",
                (attempt_id, float(now))).fetchone()
            return json.loads(row["payload_json"]) if row else None

    def settle_media_job_quotas(self, attempt_id: str,
                                actual: dict[str, float] | None = None) -> None:
        actual = actual or {}
        with self._tx() as conn:
            job = conn.execute(
                "SELECT quota_json,quota_settled FROM media_jobs WHERE attempt_id=?",
                (attempt_id,)).fetchone()
            if not job or job["quota_settled"]:
                return
            reservations = json.loads(job["quota_json"])
            for name, period, reserved, limit in reservations:
                row = conn.execute(
                    "SELECT used FROM quota_ledger WHERE name=? AND period=?",
                    (name, period)).fetchone()
                used = float(row["used"]) if row else 0.0
                observed = float(actual.get(name, 0.0))
                if not math.isfinite(observed) or observed < 0:
                    raise ValueError('actual quota usage must be finite and nonnegative')
                settled = max(0.0, used - float(reserved) + observed)
                # Settlement records reality even above the limit. Rejecting
                # an overage here hides spend; subsequent reservations fail.
                conn.execute(
                    "UPDATE quota_ledger SET used=? WHERE name=? AND period=?",
                    (settled, name, period))
            conn.execute(
                "UPDATE media_jobs SET quota_settled=1 WHERE attempt_id=?",
                (attempt_id,))

    def release_expired_media_job_quotas(self, now: float) -> int:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT attempt_id,quota_json FROM media_jobs WHERE expires_at<? "
                "AND claimed_at IS NULL AND quota_settled=0", (float(now),)).fetchall()
            for row in rows:
                for name, period, reserved, _limit in json.loads(row["quota_json"]):
                    quota = conn.execute(
                        "SELECT used FROM quota_ledger WHERE name=? AND period=?",
                        (name, period)).fetchone()
                    used = float(quota["used"]) if quota else 0.0
                    conn.execute(
                        "UPDATE quota_ledger SET used=? WHERE name=? AND period=?",
                        (max(0.0, used - float(reserved)), name, period))
                conn.execute(
                    "UPDATE media_jobs SET quota_settled=1 WHERE attempt_id=?",
                    (row["attempt_id"],))
            return len(rows)

    def reconcile_call_record(self, attempt_id: str) -> dict:
        attempt = self.get_attempt(attempt_id)
        control_id = attempt.get("control_id")
        answered = False
        hangup = False
        hangup_source = None
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT data_json FROM events WHERE processed=1 "
                "AND outcome IN ('applied','nonregressing_event_stored')").fetchall()
            for row in rows:
                data = json.loads(row["data_json"])
                payload = data.get("payload", {})
                if control_id and payload.get("call_control_id") == control_id:
                    answered = answered or data.get("event_type") == "call.answered"
                    if data.get("event_type") == "call.hangup":
                        hangup = True
                        # Telnyx reports who ended the call on the
                        # signed hangup event (callee/caller/unknown).
                        source = payload.get("hangup_source")
                        if isinstance(source, str) and source:
                            hangup_source = source
        return {"attempt_id": attempt_id, "answered_event": answered,
                "hangup_event": hangup, "hangup_source": hangup_source,
                "state": attempt["state"]}

    def reserve_quota(self, name: str, period: str, amount: float,
                      limit: float) -> float:
        return self.reserve_quotas([(name, period, amount, limit)])[0]

    def reserve_quotas(self, reservations: list[tuple[str, str, float, float]]) -> list[float]:
        seen = set()
        for name, period, amount, limit in reservations:
            check_identifier(name)
            check_identifier(period)
            if (name, period) in seen:
                raise ValueError('duplicate quota reservation key')
            seen.add((name, period))
            if not all(math.isfinite(float(v)) and float(v) >= 0 for v in (amount, limit)):
                raise ValueError("quota values must be finite and nonnegative")
        totals = []
        with self._tx() as conn:
            for name, period, amount, limit in reservations:
                row = conn.execute(
                    "SELECT used FROM quota_ledger WHERE name=? AND period=?",
                    (name, period)).fetchone()
                used = float(row["used"]) if row else 0.0
                new_used = used + float(amount)
                if new_used > float(limit):
                    raise ValueError("quota limit would be exceeded")
                totals.append(new_used)
            for (name, period, _amount, _limit), new_used in zip(reservations, totals):
                conn.execute(
                    "INSERT INTO quota_ledger(name,period,used) VALUES(?,?,?) "
                    "ON CONFLICT(name,period) DO UPDATE SET used=excluded.used",
                    (name, period, new_used))
        return totals

    def quota_usage(self, name: str, period: str) -> float:
        check_identifier(name)
        check_identifier(period)
        with self._tx() as conn:
            row = conn.execute(
                "SELECT used FROM quota_ledger WHERE name=? AND period=?",
                (name, period)).fetchone()
            return float(row["used"]) if row else 0.0

    def process_event(self, event_id: str) -> str:
        with self._tx() as conn:
            event = conn.execute(
                "SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
            if not event:
                raise ValueError("unknown event")
            if event["processed"]:
                return event["outcome"]
            data = json.loads(event["data_json"])
            outcome = self._apply(conn, data)
            conn.execute(
                "UPDATE events SET processed=1,outcome=? WHERE event_id=?", (outcome, event_id))
            return outcome

    def _apply(self, conn, data: dict) -> str:
        event_type = data["event_type"]
        if event_type not in _LIFECYCLE_RANK:
            return "unsupported_event_stored"
        payload = data["payload"]
        # Never join by session id alone. Correlation requires either a
        # known control id, or a client_state hint to a claimed attempt,
        # and if both are present they must name the same attempt.
        control_id = payload.get("call_control_id")
        hint = None
        if payload.get("client_state"):
            try:
                hint = decode_client_state(payload["client_state"])
            except Exception:
                return "quarantined_invalid_correlation"
        by_control = None
        if control_id:
            by_control = conn.execute(
                "SELECT * FROM attempts WHERE control_id=?", (control_id,)).fetchone()
        by_hint = None
        if hint:
            by_hint = conn.execute(
                "SELECT * FROM attempts WHERE attempt_id=?", (hint,)).fetchone()
        if by_control is not None and by_hint is not None and by_control["attempt_id"] != by_hint["attempt_id"]:
            return "quarantined_correlation_mismatch"
        row = by_control or by_hint
        if row is None or row["state"] in ("prepared", "cancelled_before_submit"):
            return "quarantined_unknown_or_unclaimed"
        if hint and hint != row["attempt_id"]:
            return "quarantined_correlation_mismatch"
        expected = json.loads(row["payload_json"])
        if any(payload.get(k) != expected[k] for k in ("connection_id", "from", "to")):
            return "quarantined_route_mismatch"
        try:
            ids = response_ids({"data": payload})
        except Exception:
            return "quarantined_invalid_correlation"
        if row["control_id"] and (row["control_id"], row["leg_id"], row["session_id"]) != ids:
            return "quarantined_leg_mismatch"
        self._bind(conn, row, ids)
        rank = _LIFECYCLE_RANK[event_type]
        if rank > _STATE_RANK[row["state"]]:
            new_state = _STATE_FROM_EVENT[event_type]
            conn.execute(
                "UPDATE attempts SET state=? WHERE attempt_id=?",
                (new_state, row["attempt_id"]))
            if new_state == "hangup":
                # Terminal: the reservation converts to recorded spend
                # now; reconcile_bill() records the actual bill into
                # both fields when it arrives.
                self._release_job_reservation(conn, row["job_id"])
            return "applied"
        return "nonregressing_event_stored"

    def list_quarantined(self) -> list[dict]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT event_id,outcome FROM events WHERE processed=1 AND outcome LIKE 'quarantined%'"
                " ORDER BY event_id").fetchall()
            return [dict(r) for r in rows]

    # -- stop and hangup ---------------------------------------------------

    def request_stop(self, *, attempt_id: str | None = None, job_id: str | None = None,
                     reason: str = "") -> dict:
        if attempt_id is None and job_id is None:
            raise ValueError("stop needs an attempt_id or a job_id")
        with self._tx() as conn:
            resolved_job = job_id
            if attempt_id:
                row = conn.execute(
                    "SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
                if not row:
                    raise ValueError("unknown attempt")
                if job_id is not None and job_id != row["job_id"]:
                    raise ValueError("attempt does not belong to job")
                resolved_job = resolved_job or row["job_id"]
                new_state = "cancelled_before_submit" if row["state"] == "prepared" else row["state"]
                conn.execute(
                    "UPDATE attempts SET stop=1,state=? WHERE attempt_id=?", (new_state, attempt_id))
            if resolved_job:
                conn.execute(
                    "INSERT OR IGNORE INTO pending_stops(job_id,reason) VALUES(?,?)",
                    (resolved_job, reason))
                conn.execute(
                    "UPDATE jobs SET stop=1 WHERE job_id=?", (resolved_job,))
                conn.execute(
                    "UPDATE attempts SET stop=1,"
                    "state=CASE WHEN state='prepared' THEN 'cancelled_before_submit' ELSE state END "
                    "WHERE job_id=?", (resolved_job,))
            if resolved_job:
                # A stop that lands before submission is terminal for
                # the attempt (it can never be claimed): convert the
                # reservation. A stop on a live attempt changes no
                # state and releases nothing.
                cancelled = conn.execute(
                    "SELECT 1 FROM attempts WHERE job_id=? "
                    "AND state='cancelled_before_submit' LIMIT 1",
                    (resolved_job,)).fetchone()
                if cancelled:
                    self._release_job_reservation(conn, resolved_job)
            return {"job_id": resolved_job, "attempt_id": attempt_id, "stop": True}

    def hangup_command(self, attempt_id: str):
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if not row or not row["stop"] or not row["control_id"] or row["state"] == "hangup":
                return None
            command = row["hangup_command"]
            if command:
                if self.clock() - row["hangup_at"] > self.config.hangup_reuse_window_seconds:
                    raise RetryWindowElapsed(
                        "hangup reuse window elapsed: reconcile, do not generate a new command")
            else:
                command = str(uuid5(NAMESPACE_URL, attempt_id + ":hangup:" + row["control_id"]))
                conn.execute(
                    "UPDATE attempts SET hangup_command=?,hangup_at=? WHERE attempt_id=?",
                    (command, self.clock(), attempt_id))
            return hangup_request(row["control_id"], command)

    def acknowledge_hangup(self, attempt_id: str, response: dict) -> None:
        if response != {"data": {"result": "ok"}}:
            raise ValueError("unexpected hangup command response")
        with self._tx() as conn:
            row = conn.execute(
                "SELECT hangup_command FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if not row or not row["hangup_command"]:
                raise ValueError("no hangup command was prepared")
            conn.execute(
                "UPDATE attempts SET hangup_ack=1 WHERE attempt_id=?", (attempt_id,))

    # -- billing reconciliation (separate from hangup) ----------------------

    def reconcile_bill(self, attempt_id: str, actual_cents: int, bill_ref: str) -> dict:
        if not isinstance(actual_cents, int) or actual_cents < 0:
            raise ValueError("actual cost must be a nonnegative integer")
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if not row or row["state"] != "hangup":
                raise ValueError("billing reconciliation requires a hangup event first")
            job = conn.execute(
                "SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone()
            conn.execute(
                "UPDATE jobs SET spent_cents=?,reserved_cents=?,bill_ref=?,"
                "billing_reconciled=1 WHERE job_id=?",
                (actual_cents, actual_cents, bill_ref, row["job_id"]))
            return {"attempt_id": attempt_id, "job_id": row["job_id"],
                    "estimated_cents": job["estimated_cents"], "actual_cents": actual_cents,
                    "overage_cents": max(0, actual_cents - job["estimated_cents"]),
                    "billing_reconciled": True}
