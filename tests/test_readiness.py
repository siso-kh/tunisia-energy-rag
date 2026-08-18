"""Tests for the /ready readiness endpoint (src/api/main.py).

Liveness (/health) only proves the process is up; /ready must also reach
Postgres and ChromaDB before reporting ready, so orchestrators never send
traffic to a backend whose dependencies are down.

The DB probe is exercised against an in-memory SQLite engine (patching the
module-level AsyncSessionLocal, same pattern as test_api_db) and the Chroma
probe against a stub with a .count() method -- no real ChromaDB needed.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.database.connection import build_engine, build_session_factory
from src.database.models import Base


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_test_db():
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
    return engine, build_session_factory(engine)


class _ChromaStub:
    """Stand-in for the real ChromaDB collection with a controllable .count()."""

    def __init__(self, count=5, fail=False):
        self._count = count
        self._fail = fail

    def count(self):
        if self._fail:
            raise RuntimeError("chroma unreachable")
        return self._count


@pytest.fixture()
def ready_env(monkeypatch):
    """Patches the app's DB session and Chroma collection; yields (client, factory)."""
    import src.api.main as api_main

    engine, factory = _make_test_db()
    monkeypatch.setattr(api_main, "AsyncSessionLocal", factory)
    monkeypatch.setattr(api_main, "collection", _ChromaStub())

    with TestClient(app) as test_client:
        yield test_client

    async def _teardown():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    asyncio.run(_teardown())


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

def test_ready_reports_200_when_db_and_chroma_ok(ready_env):
    response = ready_env.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"db": True, "chroma": True}


def test_ready_503_when_chroma_down(ready_env, monkeypatch):
    import src.api.main as api_main

    monkeypatch.setattr(api_main, "collection", _ChromaStub(fail=True))
    response = ready_env.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"] == {"db": True, "chroma": False}


def test_ready_503_when_db_down(ready_env, monkeypatch):
    import src.api.main as api_main

    class _BrokenSessionFactory:
        """Session factory whose __call__ raises -> the DB probe fails fast."""

        def __call__(self):
            raise RuntimeError("database unreachable")

    monkeypatch.setattr(api_main, "AsyncSessionLocal", _BrokenSessionFactory())
    response = ready_env.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"] == {"db": False, "chroma": True}


def test_health_still_liveness_only(ready_env, monkeypatch):
    """/health must not depend on the DB/Chroma (it stays 200 even degraded)."""
    import src.api.main as api_main

    class _BrokenSessionFactory:
        def __call__(self):
            raise RuntimeError("database unreachable")

    monkeypatch.setattr(api_main, "AsyncSessionLocal", _BrokenSessionFactory())
    response = ready_env.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
