"""Deploy-phase webhook smoke runner (loopback only, no Telnyx, no tunnel).

Builds the canonical adapter with a locally generated Ed25519 keypair,
serves telnyx_webhook.make_handler on 127.0.0.1, and runs:
  1. valid signed event -> 200, accepted, quarantined_unknown_or_unclaimed
  2. redelivery of the same event -> 200, duplicate
  3. tampered body with the original signature -> 401, nothing stored
  4. valid signed event on the failover path -> 200, accepted
Prints one JSON line per case plus a summary. No key material is printed.
"""
import base64
import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from telnyx_adapter import AdapterConfig, TelnyxAdapter
from telnyx_webhook import make_handler


def sign(priv, raw: bytes):
    stamp = str(int(time.time()))
    sig = priv.sign(stamp.encode() + b"|" + raw)
    return stamp, base64.b64encode(sig).decode()


def envelope(event_id, event_type="call.initiated"):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps({
        "data": {
            "id": event_id,
            "record_type": "event",
            "event_type": event_type,
            "occurred_at": now,
            "payload": {
                "call_control_id": "v2:unknown-control-id",
                "call_leg_id": "leg-unknown",
                "call_session_id": "sess-unknown",
            },
        }
    }).encode()


def post(base, path, raw, stamp, sig):
    req = urllib.request.Request(base + path, data=raw, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Telnyx-Timestamp", stamp)
    req.add_header("Telnyx-Signature-Ed25519", sig)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def main():
    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(priv.public_key().public_bytes_raw()).decode()
    tmp = tempfile.mkdtemp(prefix="webhook-smoke-")
    adapter = TelnyxAdapter(f"{tmp}/smoke.db", pub_b64, AdapterConfig())
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(adapter))
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    results = []
    try:
        raw1 = envelope("evt-smoke-001")
        s1, sig1 = sign(priv, raw1)
        code, body = post(base, "/telnyx/webhooks", raw1, s1, sig1)
        results.append({"case": "valid_event", "http": code, "body": body,
                        "pass": code == 200 and body.get("outcome") == "quarantined_unknown_or_unclaimed"})
        code, body = post(base, "/telnyx/webhooks", raw1, s1, sig1)
        results.append({"case": "redelivery_duplicate", "http": code, "body": body,
                        "pass": code == 200 and body.get("duplicate") is True})
        tampered = raw1.replace(b"call.initiated", b"call.answered")
        code, body = post(base, "/telnyx/webhooks", tampered, s1, sig1)
        results.append({"case": "tampered_body_rejected", "http": code, "body": body,
                        "pass": code == 401})
        raw2 = envelope("evt-smoke-002")
        s2, sig2 = sign(priv, raw2)
        code, body = post(base, "/telnyx/webhooks-failover", raw2, s2, sig2)
        results.append({"case": "failover_path_valid", "http": code, "body": body,
                        "pass": code == 200 and body.get("accepted") is True})
    finally:
        server.shutdown()
    for r in results:
        print(json.dumps(r))
    print(json.dumps({"all_pass": all(r["pass"] for r in results),
                      "cases": len(results)}))
    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
