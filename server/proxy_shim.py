#!/usr/bin/env python3
"""Loopback CONNECT-normalizing shim for the AI calling live server.

Why this exists (Oct 29, 2026): the VM's egress transparently
intercepts direct TLS, so provider websockets must go through the
egress proxy via HTTP CONNECT. The egress proxy's CONNECT response
carries a `Connection: close` header, which websockets 17.3 (used by
both Pipecat provider services) strictly rejects during the websocket
upgrade ("invalid Connection header: close"). This shim sits between
local websocket clients and the egress proxy: it forwards the CONNECT
to the egress proxy, then answers the local client with a clean
`HTTP/1.1 200 Connection Established` response carrying no proxy
headers, and splices bytes bidirectionally until either side closes.

This is NOT a proxy bypass: direct TLS from this VM is proven
impossible (WRONG_VERSION_NUMBER), and all traffic still traverses
the egress proxy. The shim only normalizes the CONNECT response
headers the local client sees.

Security properties:
- Binds ONLY to 127.0.0.1. No other host can reach it.
- stdlib only. No credentials anywhere: the upstream CONNECT is
  unauthenticated (proven to work) and no auth material is read,
  stored, or logged.
- Logs nothing about traffic contents: only startup/shutdown and
  per-connection errors (hostnames are connection metadata, not
  contents, and are logged only on error).
"""

import socket
import sys
import threading

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 18080
UPSTREAM_PROXY_HOST = "hatch-egress-proxy"
UPSTREAM_PROXY_PORT = 3128
BUF_SIZE = 65536
CONNECT_TIMEOUT = 30.0
MAX_CONNECTIONS = 32
CONNECTIONS = threading.BoundedSemaphore(MAX_CONNECTIONS)


def _read_headers(sock):
    """Read until the end of an HTTP header block. Returns the bytes."""
    buf = b""
    sock.settimeout(CONNECT_TIMEOUT)
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(BUF_SIZE)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 65536:
            break
    return buf


def _splice(src, dst, stop):
    try:
        while not stop.is_set():
            try:
                data = src.recv(BUF_SIZE)
            except OSError:
                break
            if not data:
                break
            try:
                dst.sendall(data)
            except OSError:
                break
    finally:
        stop.set()
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _handle_client(client):
    upstream = None
    try:
        request = _read_headers(client)
        if not request:
            return
        first_line = request.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = first_line.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            return
        authority = parts[1]
        # Forward a minimal CONNECT upstream (no auth, no extra headers).
        upstream = socket.create_connection(
            (UPSTREAM_PROXY_HOST, UPSTREAM_PROXY_PORT),
            timeout=CONNECT_TIMEOUT)
        upstream.sendall(
            f"CONNECT {authority} HTTP/1.1\r\n"
            f"Host: {authority}\r\n\r\n".encode("latin-1"))
        response = _read_headers(upstream)
        if b" 200 " not in response.split(b"\r\n", 1)[0]:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            print(f"shim: upstream CONNECT refused for {authority}",
                  file=sys.stderr, flush=True)
            return
        # Clean response: no Connection header, no other proxy headers.
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        client.settimeout(None)
        upstream.settimeout(None)
        stop = threading.Event()
        t1 = threading.Thread(target=_splice, args=(client, upstream, stop),
                              daemon=True)
        t2 = threading.Thread(target=_splice, args=(upstream, client, stop),
                              daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
    except Exception as exc:  # noqa: BLE001 - log and drop the connection
        print(f"shim: connection error: {type(exc).__name__}: {exc}",
              file=sys.stderr, flush=True)
    finally:
        for s in (client, upstream):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        CONNECTIONS.release()


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(64)
    print(f"shim: listening on {LISTEN_HOST}:{LISTEN_PORT}, "
          f"upstream {UPSTREAM_PROXY_HOST}:{UPSTREAM_PROXY_PORT}",
          file=sys.stderr, flush=True)
    try:
        while True:
            client, _addr = server.accept()
            if not CONNECTIONS.acquire(blocking=False):
                client.sendall(b"HTTP/1.1 503 Service Unavailable\r\n\r\n")
                client.close()
                continue
            threading.Thread(target=_handle_client, args=(client,),
                             daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


if __name__ == "__main__":
    main()
