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
