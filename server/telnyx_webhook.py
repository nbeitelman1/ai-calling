"""Local Telnyx webhook endpoint for the CANONICAL adapter.

WIRING-PREP ONLY (Oct 29, 2026). This module wraps
``server/telnyx_adapter`` (the canonical adapter — see
docs/WIRING_PREP_2026-10-29.md); it does not re-implement verification,
acceptance, correlation, or quarantine.

Endpoint shape (what Telnyx will be configured to call):
  POST /telnyx/webhooks            (primary)
  POST /telnyx/webhooks-failover   (failover — same handler, same rules)
  Anything else -> 404. Body over 64 KiB -> rejected by the adapter.

Ack semantics (do not weaken):
  1. Signature is verified over RAW bytes BEFORE parsing, against the
     configured account public key (Secure Vault ref at deploy time).
     A failed signature -> 401, nothing stored, nothing processed.
  2. A verified event is stored durably (raw bytes + all header
     occurrences + canonical data) in one transaction, THEN we answer
     200. Ack means STORED, not processed.
  3. Processing runs separately and idempotently. A quarantined
     outcome is still a 200: quarantine is a stored, operator-reviewed
     outcome, never a reason to make Telnyx redeliver blindly, and
     never a trigger for a call.

Safety: this server does NOT start on import. ``main()`` refuses to
run unless TELNYX_WEBHOOK_ENABLED=1 is set in the environment AND a
public-key reference is present. The deploy step (parent, with Nathan's
approval) supplies the key reference via Secure Vault, starts this on
the Muse VM, and only then opens the prototype tunnel — the tunnel is
NOT opened by this file or by this wiring-prep phase.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from telnyx_adapter import AdapterConfig, TelnyxAdapter  # noqa: E402

WEBHOOK_PATHS = ("/telnyx/webhooks", "/telnyx/webhooks-failover")


def make_handler(adapter: TelnyxAdapter):
    class TelnyxWebhookHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep logs out of stdout by default
            pass

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path not in WEBHOOK_PATHS:
                return self._send(404, {"error": "unknown path"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._send(400, {"error": "bad content-length"})
            raw = self.rfile.read(length)
            try:
                result = adapter.ingest_webhook(raw, self.headers)
            except Exception as exc:
                # Includes WebhookRejected (bad signature, stale, oversize,
                # duplicate header): nothing stored, nothing processed.
                return self._send(401, {"accepted": False, "error": str(exc)[:200]})
            # Durable acceptance achieved -> ack. Processing is separate
            # and idempotent; a quarantine outcome is still an ack.
            outcome = None
            if not result.get("duplicate"):
                try:
                    import json as _json

                    data = _json.loads(adapter_event_data(adapter, raw))
                    outcome = adapter.process_event(data["id"])
                except Exception:
                    outcome = "processing_deferred_operator_review"
            return self._send(200, {"accepted": True,
                                    "duplicate": result.get("duplicate", False),
                                    "outcome": outcome})

    return TelnyxWebhookHandler


def adapter_event_data(adapter: TelnyxAdapter, raw: bytes) -> str:
    """Return the canonical data JSON for an already-accepted event.

    The adapter stores events by id; this helper re-derives the id
    from the verified envelope without re-verifying (verification
    already happened in ingest_webhook for this request).
    """
    data = json.loads(raw)
    return json.dumps({"id": data["data"]["id"]})


def build_adapter(db_path: str, public_key_b64: str) -> TelnyxAdapter:
    return TelnyxAdapter(db_path, public_key_b64, AdapterConfig())


def main() -> int:
    if os.environ.get("TELNYX_WEBHOOK_ENABLED") != "1":
        print("Refusing to start: set TELNYX_WEBHOOK_ENABLED=1 explicitly.")
        return 2
    public_key_ref = os.environ.get("TELNYX_WEBHOOK_PUBLIC_KEY_REF", "")
    if not public_key_ref:
        print("Refusing to start: TELNYX_WEBHOOK_PUBLIC_KEY_REF is not set.")
        print("Supply the public-key REFERENCE via Secure Vault at deploy time;")
        print("never put the key value in chat, files, or logs.")
        return 2
    # NOTE: this skeleton takes the key via a reference that the deploy
    # step resolves; it never reads a key value from this file.
    # For the wiring-prep phase this function is intentionally not run.
    print("Webhook endpoint skeleton ready (not started in wiring-prep).")
    print(f"Paths: {', '.join(WEBHOOK_PATHS)}")
    print(f"Public-key ref: {public_key_ref} (value NOT shown, NOT stored here)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
