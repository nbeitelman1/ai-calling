"""Loopback HTTP fake carrier + signed webhook inbox. Never contacts Telnyx."""
import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from wire import dial_request, hangup_request, load_json
from fixtures_support import CONNECTION, FROM, TO, ATTEMPT, CONTROL, LEG, SESSION

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                return self.reply(413, {'error': 'body_limit'})
            self.connection.settimeout(2)
            body = self.rfile.read(length)
            if self.path == '/webhooks/telnyx':
                receipt = self.server.store.ingest(body, list(self.headers.items()))
                return self.reply(200, receipt)
            if self.headers.get('Authorization') != 'Bearer FIXTURE_ONLY':
                return self.reply(401, {'error': 'fixture_auth_required'})
            obj = load_json(body)
            if self.path == '/v2/calls':
                expected = dial_request(CONNECTION, FROM, TO, ATTEMPT)['json']
                if obj != expected:
                    return self.reply(422, {'error': 'unexpected_fixture_request'})
                with self.server.lock:
                    self.server.dial_count += 1
                if self.server.drop_response:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                return self.reply(200, {'data': {'call_control_id': CONTROL,
                    'call_leg_id': LEG, 'call_session_id': SESSION, 'is_alive': True}})
            if self.path == hangup_request(CONTROL, ATTEMPT)['path']:
                if set(obj) != {'command_id'}:
                    return self.reply(422, {'error': 'unexpected_hangup'})
                hangup_request(CONTROL, obj['command_id'])
                self.server.hangup_count += 1
                return self.reply(200, {'data': {'result': 'ok'}})
            self.reply(404, {'error': 'unknown_fixture_route'})
        except (ValueError, TypeError, KeyError, OSError):
            self.reply(400, {'error': 'invalid_or_unverified'})

class Lab:
    def __init__(self, store, drop_response=False):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.store = store
        self.server.drop_response = drop_response
        self.server.dial_count = self.server.hangup_count = 0
        self.server.lock = threading.Lock()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.01), daemon=True)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, body, headers=None):
        if not path.startswith('/') or '://' in path:
            raise ValueError('Only relative loopback paths')
        headers = dict(headers or {})
        headers.setdefault('Content-Type', 'application/json')
        req = urllib.request.Request(self.url + path, data=body, headers=headers, method='POST')
        try:
            with self.opener.open(req, timeout=3) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
