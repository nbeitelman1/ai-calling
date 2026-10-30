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
