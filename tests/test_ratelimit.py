"""Tests for rate limiting (src/api/ratelimit.py) and security hardening
(src/api/security.py).

The enforcement tests build a dedicated app through the SAME factory/wiring
functions ``src/api/main.py`` uses (``build_limiter`` / ``configure_limiter``
/ ``add_security_middlewares``), so they exercise production code paths
without polluting the real app or the shared limiter counters.
"""

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool

from src.api.ratelimit import build_limiter, configure_limiter
from src.api.security import add_security_middlewares, parse_cors_origins
from src.database.connection import build_engine, build_session_factory, get_db_dependency
from src.database.models import Base


def _make_limited_app(limit: str = "3/minute", trusted_proxy: bool = False):
    """Minimal app wired exactly like src/api/main.py."""
    app = FastAPI()
    add_security_middlewares(app)
    instance = build_limiter(trust_proxy=trusted_proxy)
    configure_limiter(app, instance)

    @app.get("/limited")
    @instance.limit(limit)
    def limited(request: Request):  # slowapi requires a request/websocket argument
        return {"ok": True}

    @app.get("/exempt")
    @instance.exempt
    def exempt(request: Request):
        return {"ok": True}

    return app


# ---------------------------------------------------------------------------
# Rate limiting enforcement
# ---------------------------------------------------------------------------

def test_requests_under_limit_pass():
    app = _make_limited_app("5/minute")
    with TestClient(app) as client:
        for _ in range(5):
            assert client.get("/limited").status_code == 200


def test_limit_exceeded_returns_429_with_retry_after():
    app = _make_limited_app("3/minute")
    with TestClient(app) as client:
        for _ in range(3):
            assert client.get("/limited").status_code == 200
        blocked = client.get("/limited")
        assert blocked.status_code == 429
        assert blocked.json()["detail"]  # sanitized message, no internals
        assert blocked.headers.get("retry-after")  # Retry-After present


def test_exempt_routes_are_not_limited():
    app = _make_limited_app("2/minute")
    with TestClient(app) as client:
        for _ in range(6):
            assert client.get("/exempt").status_code == 200


def test_trusted_proxy_keys_by_forwarded_ip():
    """With proxy trust on, each X-Forwarded-For gets its own budget."""
    app = _make_limited_app("2/minute", trusted_proxy=True)
    with TestClient(app) as client:
        for i in range(3):
            r = client.get("/limited", headers={"X-Forwarded-For": "1.2.3.4"})
            assert r.status_code == (429 if i >= 2 else 200)
        # A different forwarded IP is not limited.
        assert client.get("/limited", headers={"X-Forwarded-For": "5.6.7.8"}).status_code == 200


def test_limiter_can_be_disabled():
    """RATE_LIMIT_ENABLED=false turns checks off (used by tests/CI)."""
    import src.api.ratelimit as rl

    original = rl._bool_env
    rl._bool_env = lambda name, default=False: False  # force disabled
    try:
        app = _make_limited_app("1/minute")
    finally:
        rl._bool_env = original
    with TestClient(app) as client:
        for _ in range(5):
            assert client.get("/limited").status_code == 200


# ---------------------------------------------------------------------------
# Security headers + CORS
# ---------------------------------------------------------------------------

def test_security_headers_present():
    app = _make_limited_app()
    with TestClient(app) as client:
        r = client.get("/limited")
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("x-frame-options") == "DENY"
        assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
        assert r.headers.get("permissions-policy") is not None


def test_parse_cors_origins():
    assert parse_cors_origins("https://a.example, https://b.example") == [
        "https://a.example",
        "https://b.example",
    ]
    assert parse_cors_origins("") == ["*"]
    assert parse_cors_origins("  ") == ["*"]


def test_cors_origin_reflected_when_whitelisted():
    """With specific origins configured, a matching Origin is echoed back."""
    import src.api.security as sec

    original = sec.CORS_ORIGINS
    sec.CORS_ORIGINS = ["https://app.example"]
    try:
        app = _make_limited_app()
        with TestClient(app) as client:
            r = client.get("/limited", headers={"Origin": "https://app.example"})
            assert r.headers.get("access-control-allow-origin") == "https://app.example"
            # A non-whitelisted origin gets NO allow-origin header.
            r2 = client.get("/limited", headers={"Origin": "https://evil.example"})
            assert "access-control-allow-origin" not in r2.headers
    finally:
        sec.CORS_ORIGINS = original


# ---------------------------------------------------------------------------
# Real app smoke test: the wired limiter must not block normal traffic
# ---------------------------------------------------------------------------

def test_real_app_normal_traffic_not_limited():
    """The real app (src/api/main.py) runs every route through the limiter;
    with the conftest reset in place, ordinary requests all succeed."""
    from src.api.main import app

    engine = build_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    factory = build_session_factory(engine)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_dependency] = override_get_db
    try:
        with TestClient(app) as client:
            # Health is exempt; outage creation runs under the limiter.
            for _ in range(3):
                assert client.get("/health").status_code == 200
            payload = {
                "utility": "STEG",
                "region": "Tunis",
                "latitude": 36.8,
                "longitude": 10.18,
            }
            for _ in range(3):
                assert client.post("/api/outages", json=payload).status_code == 201
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
