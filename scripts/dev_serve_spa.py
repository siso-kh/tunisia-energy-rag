#!/usr/bin/env python
"""Serve the built React SPA and proxy the API — a stand-in for nginx.

Why this exists: ``nginx.hf.conf`` and ``frontend/nginx.conf`` handle the
``/api`` -> uvicorn proxy, including the settings SSE needs
(``proxy_buffering off``). That works in a container, but when running the
backend directly on Windows/macOS there is no nginx, so a tunnel can only
reach the API and the SPA appears dead.

This script does the same job with the standard library: serve
``frontend/dist`` as static files, SPA-fall back to ``index.html``, and
stream ``/api``, ``/health`` and ``/ready`` through to uvicorn **without
buffering**, so the chat stream arrives frame by frame.

It is a development convenience, not a production server: single process, no
TLS, no caching headers, no rate limiting.

Usage::

    python -m uvicorn src.api.main:app --port 8000     # in one shell
    python scripts/dev_serve_spa.py --port 8080         # in another
    ngrok http 8080

Then the tunnel exposes the full UI, same-origin, with no CORS involved.
"""

from __future__ import annotations

import argparse
import http.client
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIST = PROJECT_ROOT / "frontend" / "dist"

# Paths that must reach the API rather than the static bundle. The SPA calls
# /api/* with relative URLs, so keeping them same-origin avoids CORS entirely.
API_PREFIXES = ("/api", "/health", "/ready")

# SSE needs the socket kept open well past the default; uvicorn streams for as
# long as the model takes to answer.
UPSTREAM_TIMEOUT_S = 300


class SPAProxyHandler(BaseHTTPRequestHandler):
    """Static files from ``dist``, everything else streamed to uvicorn."""

    protocol_version = "HTTP/1.1"
    server_version = "dev-spa-proxy"

    # Injected by main().
    dist_dir: Path = DEFAULT_DIST
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    def log_message(self, fmt: str, *args) -> None:  # pragma: no cover
        sys.stderr.write("[spa] %s - %s\n" % (self.address_string(), fmt % args))

    # -- routing ---------------------------------------------------------
    def _is_api(self) -> bool:
        return self.path.startswith(API_PREFIXES)

    def do_GET(self) -> None:
        if self._is_api():
            self._proxy("GET", body=b"")
        else:
            self._serve_static()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if self._is_api():
            self._proxy(self.command, body)
        else:
            self.send_error(405, "Method Not Allowed")

    # -- static ----------------------------------------------------------
    def _resolve(self) -> Path | None:
        """Map the request path to a file inside dist, or None if outside."""
        raw = self.path.split("?", 1)[0].split("#", 1)[0]
        rel = raw.lstrip("/")
        candidate = (self.dist_dir / rel).resolve() if rel else self.dist_dir
        try:
            candidate.relative_to(self.dist_dir.resolve())
        except ValueError:
            return None
        if candidate.is_dir():
            candidate = candidate / "index.html"
        return candidate if candidate.is_file() else None

    def _serve_static(self) -> None:
        target = self._resolve()
        # SPA fallback: unknown paths render client-side routes (/map, /admin).
        if target is None:
            target = self.dist_dir / "index.html"
        if not target.is_file():
            self.send_error(
                404,
                "No frontend build found. Run `npm ci && npm run build` in frontend/.",
            )
            return

        data = target.read_bytes()
        ctype, _ = mimetypes.guess_type(str(target))
        if ctype is None:
            ctype = "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if "/assets/" in target.as_posix():
            # Hashed filenames are immutable.
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)

    # -- proxy -----------------------------------------------------------
    def _proxy(self, method: str, body: bytes) -> None:
        conn = http.client.HTTPConnection(
            self.api_host, self.api_port, timeout=UPSTREAM_TIMEOUT_S
        )
        headers = {
            k: v
            for k, v in self.headers.items()
            # Drop hop-by-hop and length headers; the body is forwarded by us.
            if k.lower()
            not in {"host", "connection", "content-length", "accept-encoding"}
        }
        headers["Host"] = f"{self.api_host}:{self.api_port}"
        if body:
            headers["Content-Length"] = str(len(body))

        try:
            conn.request(method, self.path, body=body or None, headers=headers)
            resp = conn.getresponse()
        except Exception as exc:  # upstream down / refused
            self.send_error(502, f"Upstream API unavailable: {exc}")
            return

        self.send_response(resp.status)
        chunked = False
        for key, value in resp.getheaders():
            lk = key.lower()
            if lk in {"transfer-encoding", "content-length", "connection"}:
                # Re-frame ourselves so streaming works for every upstream shape.
                chunked = chunked or lk == "transfer-encoding"
                continue
            self.send_header(key, value)
        # Tell intermediaries not to buffer (mirrors nginx X-Accel-Buffering: no).
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        # Stream in small chunks and flush, so SSE frames reach the client as
        # they are produced instead of arriving all at once at the end.
        #
        # read1() is essential: HTTPResponse.read(n) blocks until it has n
        # bytes or the response ends, which re-introduces exactly the buffering
        # nginx's `proxy_buffering off` avoids - frames would pile up and the
        # chat would appear frozen. read1() returns whatever has arrived.
        try:
            while True:
                chunk = resp.read1(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # Client navigated away mid-stream; nothing useful left to do.
            pass
        finally:
            conn.close()
            self.close_connection = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8080, help="port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind")
    parser.add_argument("--api-host", default="127.0.0.1")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument(
        "--dist",
        default=str(DEFAULT_DIST),
        help="path to the built frontend (default: frontend/dist)",
    )
    args = parser.parse_args()

    dist = Path(args.dist).resolve()
    if not (dist / "index.html").is_file():
        print(
            f"error: no frontend build at {dist}\n"
            "       run `npm ci && npm run build` inside frontend/ first",
            file=sys.stderr,
        )
        return 1

    SPAProxyHandler.dist_dir = dist
    SPAProxyHandler.api_host = args.api_host
    SPAProxyHandler.api_port = args.api_port

    server = ThreadingHTTPServer((args.host, args.port), SPAProxyHandler)
    server.daemon_threads = True
    print(
        f"SPA   : http://{args.host}:{args.port}  (serving {dist})\n"
        f"API   : http://{args.api_host}:{args.api_port}\n"
        "Tunnel: ngrok http %d" % args.port
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())