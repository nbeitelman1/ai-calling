# Source: server/telnyx_adapter/ (canonical Telnyx adapter)

## server/telnyx_adapter/__init__.py

```python
"""Muse's own Telnyx adapter (offline build, no live transport)."""
from .adapter import RetryWindowElapsed, SubmissionUnknown, TelnyxAdapter
from .config import AdapterConfig, Approval
from .verify import WebhookRejected

__all__ = [
    "AdapterConfig",
    "Approval",
    "RetryWindowElapsed",
    "SubmissionUnknown",
    "TelnyxAdapter",
    "WebhookRejected",
]
```

## server/telnyx_adapter/config.py

```python
"""Production configuration and approval gates for Muse's Telnyx adapter.

This module is Muse's own implementation. It is not the reference test
pack, and it carries no live credentials: credential *references* (env-var
names / secure-provisioning refs) are the most this layer ever holds.
Values are supplied only through supported secure provisioning at deploy
time, never in chat, files, or code.

Gates enforced here, upstream of any carrier request:
- live_enabled is False by default. A fake transport is the only
  transport that runs without an explicit live enablement.
- Every dial needs a separate Approval object: job id, exact destination,
  max minutes, and an estimated cost. Approval is not inferred from a
  job, an attempt id, or a previous call.
- Budget is reserved at prepare time, not spent at dial time. The
  monthly cap ($20.00) and per-call soft cap ($5.00) come from the
  Oct 27 scaffold and are only changed by Nathan, explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Approval:
    """One explicit, per-job approval. Created by the operator (Nathan)
    approving an exact job; the adapter only checks it, never invents it."""

    job_id: str
    destination: str
    max_minutes: int
    estimated_cost_cents: int
    approver_ref: str = "nathan"  # who approved, as a reference, not a secret


@dataclass
class AdapterConfig:
    live_enabled: bool = False
    # Credential references only -- never values.
    api_credential_ref: str | None = None
    webhook_public_key_ref: str | None = None
    monthly_cap_cents: int = 2000  # $20.00
    per_call_cap_cents: int = 500  # $5.00
    authorized_destinations: tuple[str, ...] = ()
    # Conservative local policy, not a provider guarantee: a persisted
    # hangup command may be re-sent only inside this window. After it,
    # reconciliation is required -- no new command is invented.
    hangup_reuse_window_seconds: int = 60
    webhook_body_cap_bytes: int = 65536
    webhook_freshness_seconds: int = 300
    # Telnyx Voice API v2, pinned for this implementation.
    api_base_path: str = "/v2/calls"
    api_version: str = "v2"
    # SDK pin is for signature-parity reference only; this adapter does
    # not import the Telnyx SDK. See STATUS.md.
    sdk_reference_commit: str = "269609c283e4ecd9774127a1686c41f695654708"
    sdk_reference_file_sha256: str = (
        "2c5135b23b772c86af79c9ab621f17b67f55228a40e33bc520db2c2c2d5e4bed"
    )
    cryptography_version_pin: str = "46.0.6"
```

## server/telnyx_adapter/wire.py

```python
"""Pure Telnyx Voice v2 wire shapes for Muse's adapter.

No HTTP, no keys, no dialling. Builders and parsers only, so they can be
tested without any carrier.

Contract pinned Oct 29, 2026 against developers.telnyx.com:
- Dial: POST /v2/calls, required connection_id / from / to. `from` is
  +E.164. `to` in Telnyx generally may be an array or SIP URI; this
  adapter deliberately accepts only one +E.164 string -- that is an
  application policy, not a Telnyx limit.
- client_state is Base64 text carrying only {'attempt_id': <uuid>}.
  It is correlation metadata, never authentication, and never carries
  secrets, numbers, or business details.
- A create response identifies a leg by data.call_control_id,
  data.call_leg_id, data.call_session_id. Commands address
  call_control_id. A session id may span legs, so it is never used
  alone as a join key.
- Hangup: POST /v2/calls/{call_control_id}/actions/hangup with a
  command_id. Reusing the same command_id for the same intent and leg
  is the documented duplicate scope for commands.
- Webhook envelope: data.record_type == 'event', data.id,
  data.event_type, data.occurred_at (timezone-aware ISO 8601),
  data.payload, meta delivery info. Deduplication is on the canonical
  DATA, because meta.attempt can change across redeliveries.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from urllib.parse import quote
from uuid import UUID

_E164 = re.compile(r"\+[1-9][0-9]{7,14}\Z")
_MAX_ID = 512


def _strict_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON member")
        obj[key] = value
    return obj


def parse_json_bytes(raw: bytes):
    if not isinstance(raw, bytes):
        raise ValueError("raw body must be bytes")
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_strict_object,
        parse_constant=lambda _x: (_ for _ in ()).throw(ValueError("non-finite JSON")),
    )


def check_identifier(value) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_ID:
        raise ValueError("missing or oversized identifier")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("control character in identifier")
    return value


def check_e164(value) -> str:
    if not isinstance(value, str) or not _E164.fullmatch(value):
        raise ValueError("single +E.164 number required")
    return value


def encode_client_state(attempt_id: str) -> str:
    UUID(attempt_id)
    payload = json.dumps({"attempt_id": attempt_id}, separators=(",", ":"))
    return base64.b64encode(payload.encode()).decode()


def decode_client_state(value) -> str:
    if not isinstance(value, str) or len(value) > 1024:
        raise ValueError("invalid client_state")
    obj = parse_json_bytes(base64.b64decode(value, validate=True))
    if not isinstance(obj, dict) or set(obj) != {"attempt_id"}:
        raise ValueError("client_state carries attempt_id only")
    UUID(obj["attempt_id"])
    return obj["attempt_id"]


def dial_request(connection_id: str, from_number: str, to_number: str,
                 attempt_id: str, max_minutes: int = 10) -> dict:
    check_identifier(connection_id)
    check_e164(from_number)
    check_e164(to_number)
    if not isinstance(max_minutes, int) or not 1 <= max_minutes <= 60:
        raise ValueError("max_minutes must be 1..60")
    return {
        "method": "POST",
        "path": "/v2/calls",
        "json": {
            "connection_id": connection_id,
            "from": from_number,
            "to": to_number,
            "client_state": encode_client_state(attempt_id),
            "command_id": attempt_id,
            "time_limit_secs": max_minutes * 60,
        },
    }


def hangup_request(call_control_id: str, command_id: str) -> dict:
    UUID(command_id)
    return {
        "method": "POST",
        "path": "/v2/calls/" + quote(check_identifier(call_control_id), safe="") + "/actions/hangup",
        "json": {"command_id": command_id},
    }


def parse_event(raw: bytes):
    """Return (data_dict, occurred_epoch). Raises on any shape problem."""
    obj = parse_json_bytes(raw)
    if not isinstance(obj, dict):
        raise ValueError("event envelope must be an object")
    data = obj.get("data")
    if not isinstance(data, dict) or data.get("record_type") != "event":
        raise ValueError("Voice v2 event required")
    if not isinstance(data.get("payload"), dict):
        raise ValueError("event payload required")
    for name in ("id", "event_type", "occurred_at"):
        check_identifier(data[name])
    occurred = datetime.fromisoformat(data["occurred_at"].replace("Z", "+00:00"))
    if occurred.tzinfo is None:
        raise ValueError("occurred_at must carry a timezone")
    return data, occurred.timestamp()


def response_ids(response: dict) -> tuple[str, str, str]:
    data = response["data"]
    return tuple(check_identifier(data[k]) for k in ("call_control_id", "call_leg_id", "call_session_id"))


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
```

## server/telnyx_adapter/adapter.py

```python
"""Muse's Telnyx adapter: durable job/attempt state, gated dialling,
idempotent webhook acceptance, and bounded hangup.

State machine (attempts):
    prepared -> submit_unknown -> initiated -> answered -> hangup
    prepared -> cancelled_before_submit   (stop before claim only)

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
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from uuid import NAMESPACE_URL, uuid5

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
_STATE_RANK = {"submit_unknown": 0, "initiated": 1, "answered": 2, "hangup": 3}
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
"""


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
        return request

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
            for name, period, amount, limit in reservations:
                check_identifier(name)
                check_identifier(period)
                if amount < 0 or limit < 0:
                    raise ValueError("quota values must be nonnegative")
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
                settled = max(0.0, used - float(reserved) + float(actual.get(name, 0.0)))
                if settled > float(limit):
                    raise ValueError("actual quota usage exceeds configured limit")
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
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT data_json FROM events WHERE processed=1").fetchall()
            for row in rows:
                data = json.loads(row["data_json"])
                payload = data.get("payload", {})
                if control_id and payload.get("call_control_id") == control_id:
                    answered = answered or data.get("event_type") == "call.answered"
                    hangup = hangup or data.get("event_type") == "call.hangup"
        return {"attempt_id": attempt_id, "answered_event": answered,
                "hangup_event": hangup, "state": attempt["state"]}

    def reserve_quota(self, name: str, period: str, amount: float,
                      limit: float) -> float:
        return self.reserve_quotas([(name, period, amount, limit)])[0]

    def reserve_quotas(self, reservations: list[tuple[str, str, float, float]]) -> list[float]:
        for name, period, amount, limit in reservations:
            check_identifier(name)
            check_identifier(period)
            if amount < 0 or limit < 0:
                raise ValueError("quota values must be nonnegative")
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
            conn.execute(
                "UPDATE attempts SET state=? WHERE attempt_id=?",
                (_STATE_FROM_EVENT[event_type], row["attempt_id"]))
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
                "UPDATE jobs SET spent_cents=?,bill_ref=?,billing_reconciled=1 WHERE job_id=?",
                (actual_cents, bill_ref, row["job_id"]))
            return {"attempt_id": attempt_id, "job_id": row["job_id"],
                    "estimated_cents": job["estimated_cents"], "actual_cents": actual_cents,
                    "overage_cents": max(0, actual_cents - job["estimated_cents"]),
                    "billing_reconciled": True}
```

## server/telnyx_adapter/transport.py

```python
"""Transports for Muse's Telnyx adapter.

Only two transports exist in this file:
- FakeCarrier: an in-memory fake, for tests. It never touches a network.
- LoopbackFakeCarrier: a local HTTP server on 127.0.0.1, for the one
  test that proves the adapter works over real HTTP, including a
  dropped-response case. It still never contacts Telnyx.
- LiveTransport is deliberately not implemented: live_enabled must be
  True AND a credential reference must be supplied through secure
  provisioning before any live transport is even constructed, and
  this handoff authorizes no live transport at all. Constructing it
  here raises, by design, until that separate live gate is satisfied.
"""
from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .wire import dial_request, hangup_request, parse_json_bytes


class LiveTransportNotEnabled(RuntimeError):
    pass


class LiveTransport:
    def __init__(self, config, credential_resolver=None):
        if not config.live_enabled or not config.api_credential_ref:
            raise LiveTransportNotEnabled(
                "live transport requires live_enabled and a secure credential reference")
        if credential_resolver is None:
            raise LiveTransportNotEnabled("live transport needs a credential resolver")
        self.api_key = credential_resolver(config.api_credential_ref)
        if not self.api_key:
            raise LiveTransportNotEnabled("credential resolver returned no value")

    def dial(self, path, body):
        if path != "/v2/calls":
            raise ValueError("live dial path is not allowed")
        request = urllib.request.Request(
            "https://api.telnyx.com" + path,
            data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.api_key})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8", "replace") or "{}")


class FakeCarrier:
    """In-memory fake carrier. Expected dial payload is fixed per test."""

    is_test_transport = True

    def __init__(self, *, expected_connection, expected_from, expected_to,
                 expected_attempt, control_id, leg_id, session_id):
        self.expected = dial_request(
            expected_connection, expected_from, expected_to, expected_attempt,
            max_minutes=10)["json"]
        self.control_id = control_id
        self.leg_id = leg_id
        self.session_id = session_id
        self.dial_count = 0
        self.hangup_count = 0
        self.drop_response = False

    def dial(self, path, body):
        if path != "/v2/calls":
            return 404, {"error": "unknown_route"}
        if body != self.expected:
            return 422, {"error": "unexpected_request"}
        self.dial_count += 1
        if self.drop_response:
            raise ConnectionError("fake carrier accepted, then dropped the response")
        return 200, {"data": {"call_control_id": self.control_id,
                              "call_leg_id": self.leg_id,
                              "call_session_id": self.session_id,
                              "is_alive": True}}

    def hangup(self, path, body):
        expected_path = hangup_request(self.control_id, body["command_id"])["path"]
        if path != expected_path or set(body) != {"command_id"}:
            return 422, {"error": "unexpected_hangup"}
        self.hangup_count += 1
        return 200, {"data": {"result": "ok"}}


def _make_handler(adapter, fake):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    return self._reply(413, {"error": "body_limit"})
                self.connection.settimeout(2)
                body = self.rfile.read(length)
                if self.path == "/webhooks/telnyx":
                    receipt = adapter.ingest_webhook(body, list(self.headers.items()))
                    return self._reply(200, receipt)
                obj = parse_json_bytes(body)
                if self.path == "/v2/calls":
                    if fake.drop_response and obj == fake.expected:
                        # Carrier accepted, then the response was lost.
                        fake.dial_count += 1
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    status, resp = fake.dial(self.path, obj)
                    return self._reply(status, resp)
                if self.path.startswith("/v2/calls/") and self.path.endswith("/actions/hangup"):
                    status, resp = fake.hangup(self.path, obj)
                    return self._reply(status, resp)
                return self._reply(404, {"error": "unknown_route"})
            except Exception:
                try:
                    self._reply(400, {"error": "invalid_or_unverified"})
                except Exception:
                    pass

    return Handler


class LoopbackFakeCarrier:
    """HTTP loopback fake: adapter + fake carrier on 127.0.0.1 only."""

    def __init__(self, adapter, fake):
        handler = _make_handler(adapter, fake)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, body: bytes, headers=None):
        if not path.startswith("/") or "://" in path:
            raise ValueError("only relative loopback paths")
        hdrs = dict(headers or {})
        hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(self.url + path, data=body, headers=hdrs, method="POST")
        try:
            with self.opener.open(req, timeout=3) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
```

## server/telnyx_adapter/verify.py

```python
"""Telnyx webhook Ed25519 verification for Muse's adapter.

Verification runs over the exact raw bytes, before any JSON parsing,
and fails closed: no public key, no verification, no acceptance.
There is no development bypass in this module.

Signed string, per Telnyx docs: ``{timestamp}|{raw_body}``.
Headers: Telnyx-Signature-Ed25519 (Base64, 64 bytes) and
Telnyx-Timestamp (Unix seconds). Freshness window: 300 seconds.
All signing-header occurrences are preserved by the caller as a list
of pairs; a duplicate occurrence (case-insensitive) is a rejection,
not a "pick the first one".
"""
from __future__ import annotations

import base64
import re
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_TIMESTAMP_RE = re.compile(r"[0-9]{1,12}\Z")


class WebhookRejected(ValueError):
    pass


def _header_pairs(headers):
    if hasattr(headers, "items"):
        return list(headers.items())
    return list(headers)


def verify_webhook(raw_body: bytes, headers, public_key_b64: str, *, now: float | None = None,
                   body_cap: int = 65536, freshness_seconds: int = 300) -> bool:
    if not isinstance(raw_body, bytes) or not 0 < len(raw_body) <= body_cap:
        raise WebhookRejected("raw_body_required_or_oversized")
    if not public_key_b64:
        raise WebhookRejected("public_key_required")
    seen: dict[str, str] = {}
    for raw_key, value in _header_pairs(headers):
        key = str(raw_key).lower()
        if key in ("telnyx-timestamp", "telnyx-signature-ed25519"):
            if key in seen or not isinstance(value, str):
                raise WebhookRejected("duplicate_or_invalid_signing_header")
            seen[key] = value
    stamp = seen.get("telnyx-timestamp", "")
    signature = seen.get("telnyx-signature-ed25519", "")
    if not _TIMESTAMP_RE.fullmatch(stamp):
        raise WebhookRejected("invalid_timestamp")
    current = time.time() if now is None else now
    if abs(current - int(stamp)) > freshness_seconds:
        raise WebhookRejected("outside_300_second_window")
    try:
        key_bytes = base64.b64decode(public_key_b64, validate=True)
        sig_bytes = base64.b64decode(signature, validate=True)
        if len(key_bytes) != 32 or len(sig_bytes) != 64:
            raise ValueError("key_or_signature_length")
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(
            sig_bytes, stamp.encode("ascii") + b"|" + raw_body
        )
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise WebhookRejected("signature_or_encoding_invalid") from exc
    return True
```

## server/model_select.py

```python
"""Dynamic model selection and cost ledger — NO hard-pinned models.

"Dynamic always" (Nathan, Oct 29, 2026) means: no model name is baked
into pipeline code. Pools come from environment variables at call-build
time, and the resolver picks the first pool entry the provider actually
offers (when an available-model list is supplied) or the first pool
entry otherwise. An empty pool fails closed — the call does not build
— rather than silently falling back to an invented default.

Environment pools (comma-separated, set at deploy time by the parent):
  GEMINI_FAST_MODELS      fast brain pool (live talker)
  GEMINI_FRONTIER_MODELS  frontier brain pool (escalation)
  GROQ_STT_MODELS         request-based Whisper transcription pool
  ELEVENLABS_TTS_MODELS   TTS model pool (optional; empty = service default)

Cost rates below are ESTIMATES from the Oct 29 wiring doc (published-
rate arithmetic, not provider quotes). Measured provider metering at
call time governs; the ledger's job is to stop the call BEFORE the
caps, not to bill precisely. Caps (Nathan-approved): $1.00 hard budget
for the first test call, $5.00/call soft cap, $20.00/month ledger cap.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

TEST_CALL_BUDGET_CENTS = 100
PER_CALL_SOFT_CAP_CENTS = 500
MONTHLY_CAP_CENTS = 2000
MONTHLY_ALERT_FRACTION = 0.80

# Estimates only — see module docstring. Units: cents.
RATE_TELNYX_PER_MINUTE_CENTS = 0.7       # ~$0.007/min outbound voice
RATE_TELNYX_MEDIA_PER_MINUTE_CENTS = 0.35  # ~$0.0035/min media streaming
RATE_STT_PER_MINUTE_CENTS = 0.0          # Groq free-tier monetary estimate
RATE_TTS_PER_1000_CHARS_CENTS = 5.0      # ElevenLabs Flash API ~$0.05/1k
RATE_LLM_PER_1000_TOKENS_CENTS = 0.0     # Gemini free-tier monetary estimate
RATE_FRONTIER_PER_1000_TOKENS_CENTS = 0.0  # Gemini free-tier monetary estimate


def parse_pool(csv_value: str | None) -> list[str]:
    if not csv_value:
        return []
    seen: list[str] = []
    for item in csv_value.split(","):
        item = item.strip()
        if item and item not in seen:
            seen.append(item)
    return seen


def resolve_model(role: str, pool: list[str], available: list[str] | None = None) -> str:
    """Pick a model for `role` from `pool`. Fail closed on empty pool.

    When `available` (a provider model list, e.g. from a free
    GET /v1/models probe at deploy time) is given, the first pool entry
    present in it wins; if none is present, resolution fails rather
    than dialling with an unlisted model.
    """
    if not pool:
        raise ValueError(f"no {role} model pool configured; refusing to build call")
    if available is None:
        return pool[0]
    available_set = set(available)
    for candidate in pool:
        if candidate in available_set:
            return candidate
    raise ValueError(
        f"no {role} pool model is offered by the provider "
        f"(pool={pool}, offered={sorted(available_set)[:20]}...)")

def resolve_models_from_env(available: dict[str, list[str]] | None = None) -> dict[str, str]:
    """Resolve fast/frontier/STT models from environment pools."""
    return {
        "fast": resolve_model("fast", parse_pool(os.environ.get("GEMINI_FAST_MODELS")),
                              available.get("gemini") if available else None),
        "frontier": resolve_model("frontier", parse_pool(os.environ.get("GEMINI_FRONTIER_MODELS")),
                                  available.get("gemini") if available else None),
        "stt": resolve_model("stt", parse_pool(os.environ.get("GROQ_STT_MODELS")),
                             available.get("groq") if available else None),
    }


# ---------------------------------------------------------------------------
# Cost ledger + transcript log (JSONL, one record per call).
# ---------------------------------------------------------------------------

@dataclass
class CallCost:
    minutes: float = 0.0
    stt_minutes: float = 0.0
    tts_chars: int = 0
    llm_tokens: int = 0
    frontier_tokens: int = 0

    def total_cents(self) -> float:
        return sum(self.breakdown_cents().values())

    def breakdown_cents(self) -> dict[str, float]:
        return {
            "telnyx_voice_estimate": self.minutes * RATE_TELNYX_PER_MINUTE_CENTS,
            "telnyx_media_stream_estimate": (
                self.minutes * RATE_TELNYX_MEDIA_PER_MINUTE_CENTS),
            "groq_free_tier_estimate": self.stt_minutes * RATE_STT_PER_MINUTE_CENTS,
            "elevenlabs_estimate": (self.tts_chars / 1000.0)
            * RATE_TTS_PER_1000_CHARS_CENTS,
            "gemini_fast_free_tier_estimate": (self.llm_tokens / 1000.0)
            * RATE_LLM_PER_1000_TOKENS_CENTS,
            "gemini_frontier_free_tier_estimate": (self.frontier_tokens / 1000.0)
            * RATE_FRONTIER_PER_1000_TOKENS_CENTS,
        }


@dataclass
class CostLedger:
    """Enforces test budget / soft cap / monthly cap before spending."""
    spent_this_month_cents: float = 0.0
    test_call: bool = False

    def project(self, cost: CallCost) -> str:
        total = cost.total_cents()
        if self.test_call and total > TEST_CALL_BUDGET_CENTS:
            return "test_budget_exceeded"
        if total > PER_CALL_SOFT_CAP_CENTS:
            return "soft_cap"
        if self.spent_this_month_cents + total > MONTHLY_CAP_CENTS:
            return "monthly_cap"
        if self.spent_this_month_cents + total >= MONTHLY_CAP_CENTS * MONTHLY_ALERT_FRACTION:
            return "ok_alert_80_percent"
        return "ok"

    def record(self, path: str, record: dict) -> dict:
        """Append a transcript+cost record (JSONL). Returns the record."""
        entry = redact_record(record)
        entry["recorded_at"] = time.time()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        return entry


# ---------------------------------------------------------------------------
# Outgoing-text guard: the assistant must never speak a forbidden shape.
# Same patterns as voice_pipeline.FORBIDDEN_PATTERNS; applied to every
# assistant reply before it reaches TTS. A trip ends the reply (the
# caller hears a refusal, not the pattern).
# ---------------------------------------------------------------------------
import re as _re

_FORBIDDEN_OUTGOING = (
    _re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    _re.compile(r"\b\d{13,19}\b"),
    _re.compile(r"\b(?:otp|one[- ]time|verification|security)\s*(?:code)?\s*[:#-]?\s*\d{4,8}\b", _re.I),
    _re.compile(r"\b(?:routing|account)\s*(?:number|no\.?|#)?\s*[:#-]?\s*\d{6,17}\b", _re.I),
    _re.compile(r"\bpassword\s*[:=-]\s*\S+", _re.I),
    _re.compile(r"\b(?:authorization|x-goog-api-key|xi-api-key)\s*[:=]\s*(?:bearer\s+)?\S+", _re.I),
    _re.compile(r"\b(?:sk|gsk|AIza)[-_A-Za-z0-9]{16,}\b"),
    _re.compile(r"\bhsurr:[A-Za-z0-9_./+=-]{8,}\b"),
)

REFUSAL_LINE = "I can't share or repeat that kind of information on a call."


def sanitize_outgoing(text: str) -> str:
    """Return text, or the refusal line if it trips a forbidden pattern."""
    for pattern in _FORBIDDEN_OUTGOING:
        if pattern.search(text or ""):
            return REFUSAL_LINE
    return text


def redact_persisted_text(text: str) -> str:
    value = text or ""
    for pattern in _FORBIDDEN_OUTGOING:
        value = pattern.sub("[REDACTED]", value)
    return value


def redact_record(record: dict) -> dict:
    entry = dict(record)
    entry["transcript"] = [
        {**turn, "text": redact_persisted_text(turn.get("text", ""))}
        for turn in record.get("transcript", [])
    ]
    return entry
```

## server/provider_preflight.py

```python
"""No-generation provider/model preflight. Writes a readiness receipt only."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import aiohttp

from model_select import resolve_models_from_env


async def _get_json(session, url, headers):
    async with session.get(url, headers=headers) as response:
        if response.status != 200:
            raise RuntimeError(f"provider preflight returned HTTP {response.status}")
        return await response.json()


async def run_preflight() -> dict:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        groq, gemini, voice, eleven_models = await asyncio.gather(
            _get_json(session, "https://api.groq.com/openai/v1/models",
                      {"Authorization": "Bearer " + os.environ["GROQ_API_KEY"]}),
            _get_json(session,
                      "https://generativelanguage.googleapis.com/v1beta/models",
                      {"x-goog-api-key": os.environ["GEMINI_API_KEY"]}),
            _get_json(session,
                      "https://api.elevenlabs.io/v1/voices/"
                      + os.environ.get("ELEVENLABS_VOICE_ID",
                                       "cgSgspJ2msm6clMCkdW9"),
                      {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}),
            _get_json(session, "https://api.elevenlabs.io/v1/models",
                      {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}),
        )
    offered = {
        "groq": [item["id"] for item in groq.get("data", [])],
        "gemini": [item["name"].removeprefix("models/")
                   for item in gemini.get("models", [])],
    }
    resolved = resolve_models_from_env(offered)
    eleven_ids = {item.get("model_id") for item in eleven_models}
    tts_model = os.environ.get("ELEVENLABS_TTS_MODELS", "eleven_flash_v2_5").split(",")[0]
    if tts_model not in eleven_ids:
        raise RuntimeError("configured ElevenLabs model is unavailable")
    receipt = {"ready": True, "checked_at": time.time(), "models": resolved,
               "tts_model": tts_model, "voice_name": voice.get("name", "")}
    path = Path(os.environ.get(
        "AI_CALLING_PREFLIGHT_RECEIPT",
        str(Path(__file__).resolve().parent.parent / "logs" /
            "provider-preflight.json")))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    temporary.replace(path)
    return receipt


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_preflight()), indent=2))
```
