"""Strict offline Telnyx V2 Ed25519 verifier. No API credentials needed."""
import base64
import re
import time
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

class WebhookRejected(ValueError):
    pass


def verify(body, headers, public_key, *, now=None):
    if not isinstance(body, bytes) or not 0 < len(body) <= 65536:
        raise WebhookRejected('raw_body_required_or_oversized')
    pairs = headers.items() if hasattr(headers, 'items') else headers
    normalized = {}
    for key, value in pairs:
        key = str(key).lower()
        if key in ('telnyx-timestamp', 'telnyx-signature-ed25519'):
            if key in normalized or not isinstance(value, str):
                raise WebhookRejected('duplicate_or_invalid_signing_header')
            normalized[key] = value
    stamp = normalized.get('telnyx-timestamp', '')
    signature = normalized.get('telnyx-signature-ed25519', '')
    if not re.fullmatch(r'[0-9]{1,12}', stamp):
        raise WebhookRejected('invalid_timestamp')
    current = time.time() if now is None else now
    if abs(current - int(stamp)) > 300:
        raise WebhookRejected('outside_300_second_window')
    try:
        key = base64.b64decode(public_key, validate=True)
        sig = base64.b64decode(signature, validate=True)
        if len(key) != 32 or len(sig) != 64:
            raise ValueError('length')
        Ed25519PublicKey.from_public_bytes(key).verify(sig, stamp.encode('ascii') + b'|' + body)
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise WebhookRejected('signature_or_encoding_invalid') from exc
    return True
