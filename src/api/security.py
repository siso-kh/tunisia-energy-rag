"""Security hardening: response security headers + CORS origin whitelist.

Headers are always safe to send and never break the SPA (they only tighten
browser-side protections). HSTS and CSP are opt-in via env because:

* HSTS is meaningless (and harmful) on plain-HTTP dev servers.
* A Content-Security-Policy can break third-party resources (Leaflet map
  tiles, fonts...), so operators opt in with a policy they control.
"""

import os

from fastapi import FastAPI


def _bool_env(name: str, default: bool = False) -> bool:
    return os.getenv(name, "true" if default else "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

def parse_cors_origins(raw: str) -> list[str]:
    """Parse a comma-separated origin list; empty input falls back to \"*\"."""
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or ["*"]


CORS_ORIGINS = parse_cors_origins(os.getenv("CORS_ORIGINS", "*"))


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

# Always-on headers (safe, no functional side effects).
_SECURITY_HEADERS = [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
]

if _bool_env("SECURE_HSTS"):
    _SECURITY_HEADERS.append(
        ("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    )

_CSP = os.getenv("SECURITY_CSP", "").strip()
if _CSP:
    _SECURITY_HEADERS.append(("Content-Security-Policy", _CSP))


class SecurityHeadersMiddleware:
    """Pure-ASGI middleware appending the configured security headers.

    Written as plain ASGI (not BaseHTTPMiddleware) to avoid the extra
    streaming/background-task overhead on every request.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    (name.encode("latin-1"), value.encode("latin-1"))
                    for name, value in _SECURITY_HEADERS
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


def add_security_middlewares(app: FastAPI) -> None:
    """Add CORS (with env-driven origins) + security headers to the app."""
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
