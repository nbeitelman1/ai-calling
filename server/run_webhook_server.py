"""Deploy-phase webhook server (loopback listener + tunnel target).

TEST-KEY MODE ONLY: this runner generates a fresh Ed25519 keypair at
startup for reachability smoke tests. It is NOT the Telnyx account
public key. Before any Telnyx application is created, the parent must
restart this server (or an equivalent) configured with the real
Telnyx webhook public-key reference from the portal; events signed
with the real Telnyx key will 401 against this test-key instance,
by design.

Serves POST /telnyx/webhooks and /telnyx/webhooks-failover via the
canonical adapter, plus GET /healthz (test-mode status only).
Usage: python3 run_webhook_server.py [port]  (default 8001)
"""
import base64
import json
import os
import sys
import tempfile
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from telnyx_adapter import AdapterConfig, TelnyxAdapter
from telnyx_webhook import make_handler


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    real_pub = os.environ.get("TELNYX_WEBHOOK_PUBLIC_KEY", "").strip()
    if real_pub:
        pub_b64 = real_pub
        mode = "telnyx-key"
    else:
        priv = Ed25519PrivateKey.generate()
        pub_b64 = base64.b64encode(priv.public_key().public_bytes_raw()).decode()
        mode = "test-key-only"
    tmp = tempfile.mkdtemp(prefix="webhook-server-")
    adapter = TelnyxAdapter(f"{tmp}/webhook.db", pub_b64, AdapterConfig())
    base_handler = make_handler(adapter)

    class Handler(base_handler):
        def do_GET(self):
            if self.path == "/healthz":
                body = json.dumps({"status": "ok", "mode": mode}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"webhook server listening on 127.0.0.1:{port} (TEST KEY ONLY)", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
