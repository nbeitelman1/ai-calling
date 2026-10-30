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
