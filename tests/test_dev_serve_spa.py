"""The dev SPA proxy replaces nginx when running without Docker.

`nginx.hf.conf` sets `proxy_buffering off` for SSE. This script has to
reproduce that, because the failure it causes is silent: with buffering on,
frames queue up and the chat appears frozen, then dumps the whole answer at
once at the end. These tests pin the behaviours nginx was providing.
"""

import http.server
import socket
import threading
import time
from http.client import HTTPConnection

import pytest

from scripts.dev_serve_spa import SPAProxyHandler


@pytest.fixture
def dist(tmp_path):
    """A miniature built frontend: index, one asset, one client-side route."""
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (root / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    return root


@pytest.fixture
def serve(dist):
    """Run the handler on a real socket against a fake upstream API."""
    seen = {}

    class FakeAPI(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            seen["body"] = self.rfile.read(length) if length else b""
            seen["path"] = self.path
            # Two SSE frames with a pause between them, so a buffering proxy
            # would deliver them together instead of incrementally.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for payload in (b'{"type":"token","t":"a"}\n\n',
                            b'{"type":"done","answer":"ok"}\n\n'):
                self.wfile.write(b"%x\r\n" % len(payload) + payload + b"\r\n")
                self.wfile.flush()
                time.sleep(0.15)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

        def do_GET(self):
            seen["get_path"] = self.path
            body = b'{"status":"healthy"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    api = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
    api.daemon_threads = True
    threading.Thread(target=api.serve_forever, daemon=True).start()

    SPAProxyHandler.dist_dir = dist
    SPAProxyHandler.api_host = "127.0.0.1"
    SPAProxyHandler.api_port = api.server_address[1]

    proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SPAProxyHandler)
    proxy.daemon_threads = True
    threading.Thread(target=proxy.serve_forever, daemon=True).start()

    yield f"127.0.0.1:{proxy.server_address[1]}", seen

    proxy.shutdown()
    api.shutdown()


def _get(addr, path):
    host, port = addr.split(":")
    conn = HTTPConnection(host, int(port), timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    return resp.status, resp.read()


def test_serves_the_spa_index(serve):
    addr, _ = serve
    status, body = _get(addr, "/")
    assert status == 200
    assert b"spa" in body


def test_client_side_routes_fall_back_to_index(serve):
    """`/map` and `/admin` exist only in the router, so they must not 404."""
    addr, _ = serve
    for route in ("/map", "/admin", "/calculator"):
        status, body = _get(addr, route)
        assert status == 200, route
        assert b"spa" in body


def test_assets_are_served_and_marked_immutable(serve):
    addr, _ = serve
    status, body = _get(addr, "/assets/app-abc123.js")
    assert status == 200
    assert b"console.log" in body


def test_path_traversal_is_refused(serve):
    """A crafted path must not escape dist and read a file off the host."""
    addr, _ = serve
    for evil in ("/../../.env", "/..%2f..%2f.env"):
        status, body = _get(addr, evil)
        assert b"DATABASE_URL" not in body, evil
        # Falls back to the SPA rather than leaking anything.
        assert status == 200


def test_api_paths_proxy_to_upstream(serve):
    addr, seen = serve
    status, body = _get(addr, "/health")
    assert status == 200
    assert b"healthy" in body
    assert seen["get_path"] == "/health"


def test_sse_frames_are_not_buffered(serve):
    """The regression nginx prevented: frames arriving only at the very end.

    Uses a raw socket on purpose. ``http.client`` reads ahead into its own
    buffer, so ``read(1)`` would hand back bytes instantly and report every
    frame as arriving at the same instant - measuring the client, not the
    proxy. ``socket.recv`` returns whatever actually crossed the wire.
    """
    addr, _ = serve
    host, port = addr.split(":")

    request = (
        f"POST /api/chat/stream HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: 12\r\n"
        f"\r\n"
        f'{{"query":"hi"}}'
    ).encode()

    done_frame = b'{"type":"done","answer":"ok"}\n\n'
    arrivals = []
    body = b""

    with socket.create_connection((host, int(port)), timeout=20) as sock:
        sock.sendall(request)
        start = time.time()
        sock.settimeout(20)
        while time.time() - start < 20:
            chunk = sock.recv(4096)
            if not chunk:
                break
            arrivals.append((time.time() - start, chunk))
            body += chunk
            if done_frame in body:
                break

    assert b"200 OK" in body.split(b"\r\n", 1)[0], "the proxy must return 200"
    assert b'"type":"token"' in body, "the stream must reach the client"
    assert done_frame in body

    # Upstream sends the two frames 0.15s apart. If the proxy buffered, they
    # would land in a single recv.
    def _first_complete(marker):
        running = b""
        for ts, ch in arrivals:
            running += ch
            if marker in running:
                return ts
        return None

    token_at = _first_complete(b'{"type":"token"')
    done_at = _first_complete(b'{"type":"done"')
    assert token_at is not None and done_at is not None
    assert done_at - token_at > 0.05, (
        f"SSE frames arrived {done_at - token_at:.3f}s apart - "
        "the proxy is buffering"
    )


def test_request_body_is_forwarded(serve):
    addr, seen = serve
    host, port = addr.split(":")
    conn = HTTPConnection(host, int(port), timeout=10)
    conn.request(
        "POST",
        "/api/chat/stream",
        body=b'{"query":"Quel est le role de l ANME ?"}',
        headers={"Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    resp.read()
    assert seen["path"] == "/api/chat/stream"
    assert b"ANME" in seen["body"]


def test_upstream_down_returns_502_not_a_hang(dist):
    """A dead API must surface as an error, not an empty 200 the SPA trusts."""
    SPAProxyHandler.dist_dir = dist
    SPAProxyHandler.api_host = "127.0.0.1"
    # Port 1 is reserved and never listening.
    SPAProxyHandler.api_port = 1

    proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SPAProxyHandler)
    proxy.daemon_threads = True
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    try:
        status, body = _get(f"127.0.0.1:{proxy.server_address[1]}", "/health")
        assert status == 502
        assert b"Upstream" in body
    finally:
        proxy.shutdown()