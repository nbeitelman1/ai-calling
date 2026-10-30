"""Authenticate Telnyx's WebSocket HTTP upgrade using the exact public WSS URL.

Official contract: Ed25519(timestamp + '|' + stream_url). Do not reconstruct
scheme/host from untrusted proxy headers. The configured host must match the
stream_url used by the dialing process exactly, including an explicit port.
"""
from urllib.parse import urlsplit
from telnyx_adapter.verify import verify_webhook, WebhookRejected
from telnyx_adapter.wire import check_identifier


def canonical_stream_url(public_host: str, attempt_id: str, query: bytes = b'') -> str:
    check_identifier(attempt_id)
    host = public_host.strip()
    parsed = urlsplit('wss://' + host)
    if (not host or parsed.netloc != host or not parsed.hostname
            or parsed.username or parsed.password or parsed.path
            or parsed.query or parsed.fragment
            or any(c.isspace() for c in host) or '\\' in host):
        raise WebhookRejected('canonical_public_host_required')
    try:
        parsed.port
        suffix = query.decode('ascii')
    except (ValueError, UnicodeError) as exc:
        raise WebhookRejected('invalid_public_url') from exc
    return f'wss://{host}/telnyx/media/{attempt_id}' + ('?' + suffix if suffix else '')


def verify_media_upgrade(headers, public_key: str, public_host: str,
                         attempt_id: str, *, query: bytes = b'', now=None) -> bool:
    url = canonical_stream_url(public_host, attempt_id, query)
    return verify_webhook(url.encode('utf-8'), headers, public_key,
                          now=now, body_cap=8192, freshness_seconds=300)
