"""Rate limiting for the API (slowapi).

All limits are read from environment variables at import time so operators
can tune them without touching code:

* ``RATE_LIMIT_DEFAULT``        - default limit applied to every non-exempt route
* ``RATE_LIMIT_CHAT``           - /api/chat and /api/chat/stream (LLM cost guard)
* ``RATE_LIMIT_AUTH``           - /api/auth/register and /api/auth/login (brute force)
* ``RATE_LIMIT_OUTAGE_CREATE``  - POST /api/outages (crowdsourced reports)
* ``RATE_LIMIT_ADMIN``          - admin endpoints
* ``RATE_LIMIT_STORAGE_URI``    - storage backend ("memory://" default, "redis://..." in prod)
* ``RATE_LIMIT_ENABLED``        - "false" disables all checks (used by tests)
* ``TRUST_PROXY_HEADERS``       - "true" behind nginx/ngrok: key on the first
                                   X-Forwarded-For value instead of the socket IP.
                                   Only enable when a trusted proxy sets that
                                   header, otherwise clients could spoof it.
"""

import logging
import os
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)


def _bool_env(name: str, default: bool = False) -> bool:
    return os.getenv(name, "true" if default else "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# ---------------------------------------------------------------------------
# Configuration (env-driven, read once at import)
# ---------------------------------------------------------------------------

DEFAULT_LIMIT = os.getenv("RATE_LIMIT_DEFAULT", "60/minute")
CHAT_LIMIT = os.getenv("RATE_LIMIT_CHAT", "10/minute")
AUTH_LIMIT = os.getenv("RATE_LIMIT_AUTH", "10/minute")
OUTAGE_CREATE_LIMIT = os.getenv("RATE_LIMIT_OUTAGE_CREATE", "10/minute")
ADMIN_LIMIT = os.getenv("RATE_LIMIT_ADMIN", "30/minute")
STORAGE_URI = os.getenv("RATE_LIMIT_STORAGE_URI", "memory://")
TRUST_PROXY_HEADERS = _bool_env("TRUST_PROXY_HEADERS")


def _rate_key(request: Request, trust_proxy: bool) -> str:
    """Identify the client for rate limiting.

    Uses the first ``X-Forwarded-For`` value when ``trust_proxy`` is on
    (behind nginx/ngrok all sockets come from the proxy), otherwise the
    direct socket address. Captured per-limiter so tests can toggle it.
    """
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip() or get_remote_address(request)
    return get_remote_address(request)


def build_limiter(trust_proxy: Optional[bool] = None) -> Limiter:
    """Create the app limiter (single instance shared by the API + tests).

    ``trust_proxy`` overrides the ``TRUST_PROXY_HEADERS`` env flag (used by
    tests to exercise the proxy keying without touching global state).
    """
    trust = TRUST_PROXY_HEADERS if trust_proxy is None else trust_proxy
    limiter = Limiter(
        key_func=lambda request: _rate_key(request, trust),
        default_limits=[DEFAULT_LIMIT],
        storage_uri=STORAGE_URI,
        enabled=_bool_env("RATE_LIMIT_ENABLED", default=True),
    )
    if not limiter.enabled:
        logger.warning("Rate limiting is DISABLED (RATE_LIMIT_ENABLED=false)")
    return limiter


# The single instance used by the API. Tests import this to reset counters
# between cases (see tests/conftest.py).
limiter = build_limiter()


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Consistent 429 JSON response with a Retry-After header.

    slowapi stores the breached limit as ``(RateLimitItem, [key, scope])`` on
    ``request.state.view_rate_limit``; the storage backend answers with the
    window reset timestamp, from which the seconds-until-retry is derived.
    Mirrors slowapi's own header math so the value is correct on any backend.
    """
    import time

    response = JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please slow down and try again later."},
    )
    limiter_state = getattr(request.app.state, "limiter", None)
    view_limit = getattr(request.state, "view_rate_limit", None)
    if limiter_state is not None and view_limit is not None:
        try:
            item, args = view_limit
            reset_at, _ = limiter_state.limiter.get_window_stats(item, *args)
            retry_in = max(1, int(1 + reset_at - time.time()))
            response.headers["Retry-After"] = str(retry_in)
        except Exception:  # pragma: no cover - defensive; header is optional
            pass
    return response


def configure_limiter(app: FastAPI, limiter_instance: Limiter) -> None:
    """Wire slowapi into a FastAPI app.

    Shared by ``src/api/main.py`` and the tests so the test suite exercises
    exactly the same wiring as production.
    """
    app.state.limiter = limiter_instance
    app.add_middleware(SlowAPIMiddleware)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
