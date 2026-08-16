"""Tests for the DB-backed API endpoints (src/api/main.py).

Uses FastAPI dependency overrides so the endpoints run against an in-memory
SQLite database instead of Postgres, keeping the tests fast and hermetic.
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.database.connection import build_engine, build_session_factory, get_db_dependency
from src.database.models import (
    Base,
    Conversation,
    Message,
    OutageReport,
    ReportStatus,
    User,
    UtilityType,
)
from src.database.service import (
    DEMO_USER_ID,
    create_conversation,
    get_or_create_demo_user,
    persist_chat_turn,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_test_db():
    """Build an in-memory SQLite engine + session factory, with FK pragma on."""
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


@pytest.fixture()
def api_env():
    """Yields (TestClient, session_factory) sharing one in-memory SQLite DB."""
    engine, factory = _make_test_db()

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_dependency] = override_get_db
    with TestClient(app) as test_client:
        yield test_client, factory
    app.dependency_overrides.clear()

    async def _teardown():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    asyncio.run(_teardown())


@pytest.fixture()
def client(api_env):
    return api_env[0]


# ---------------------------------------------------------------------------
# Outage reports
# ---------------------------------------------------------------------------

def test_outages_list_empty(client):
    response = client.get("/api/outages")
    assert response.status_code == 200
    assert response.json() == []


def test_outages_create_and_list(client):
    payload = {
        "utility": "STEG",
        "region": "Sousse",
        "latitude": 35.8256,
        "longitude": 10.6083,
        "description": "Coupure de courant au centre-ville.",
    }
    response = client.post("/api/outages", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["region"] == "Sousse"
    assert data["status"] == "PENDING"  # new reports default to PENDING
    assert data["utility"] == "STEG"

    outage_id = data["id"]

    listed = client.get("/api/outages").json()
    assert len(listed) == 1
    assert listed[0]["id"] == outage_id


def test_outages_filter_by_status(client):
    for status in ("PENDING", "VERIFIED"):
        client.post(
            "/api/outages",
            json={
                "utility": "SONEDE",
                "region": "Sfax",
                "latitude": 34.74,
                "longitude": 10.76,
            },
        )
        if status == "VERIFIED":
            # flip the just-created report to VERIFIED
            created = client.get("/api/outages").json()[0]
            client.patch(f"/api/outages/{created['id']}/status", json={"status": "VERIFIED"})

    pending = client.get("/api/outages", params={"status": "PENDING"}).json()
    verified = client.get("/api/outages", params={"status": "VERIFIED"}).json()
    assert len(pending) == 1
    assert len(verified) == 1
    assert pending[0]["status"] == "PENDING"
    assert verified[0]["status"] == "VERIFIED"


def test_outages_status_update_404(client):
    response = client.patch(
        "/api/outages/00000000-0000-0000-0000-000000000999/status",
        json={"status": "RESOLVED"},
    )
    assert response.status_code == 404


def test_outages_validation(client):
    # missing region
    assert client.post(
        "/api/outages",
        json={"utility": "STEG", "latitude": 36.8, "longitude": 10.18},
    ).status_code == 422
    # out-of-range latitude
    assert client.post(
        "/api/outages",
        json={"utility": "STEG", "region": "Tunis", "latitude": 99.0, "longitude": 10.0},
    ).status_code == 422


# ---------------------------------------------------------------------------
# Admin: outage purge stats (API-key protected)
# ---------------------------------------------------------------------------

ADMIN_KEY = "test-admin-key-123"


def _admin_headers():
    return {"X-Admin-Key": ADMIN_KEY}


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    """Set the admin API key for the duration of the admin tests."""
    import src.api.main as api_main

    monkeypatch.setattr(api_main, "ADMIN_API_KEY", ADMIN_KEY)


def test_admin_requires_key(client):
    """Without a valid X-Admin-Key header, admin endpoints are refused."""
    # No header at all
    assert client.get("/api/admin/purge-stats").status_code == 401
    assert client.post("/api/admin/purge").status_code == 401
    # Wrong key
    assert (
        client.get(
            "/api/admin/purge-stats", headers={"X-Admin-Key": "wrong"}
        ).status_code
        == 401
    )


def test_admin_401_is_identical_for_missing_and_wrong_key(client):
    """No oracle: the 401 response must look the same whether the header is
    absent or wrong, so attackers cannot tell the two cases apart."""
    missing = client.get("/api/admin/purge-stats")
    wrong = client.get("/api/admin/purge-stats", headers={"X-Admin-Key": "wrong-key"})
    assert missing.status_code == wrong.status_code == 401
    assert missing.text == wrong.text
    # The error must never echo the attempted key.
    assert "wrong-key" not in wrong.text
    assert "test-admin-key-123" not in wrong.text


def test_admin_accepts_header_with_different_case(client):
    """HTTP header names are case-insensitive; lowercase must work too."""
    response = client.get(
        "/api/admin/purge-stats", headers={"x-admin-key": ADMIN_KEY}
    )
    assert response.status_code == 200


def test_admin_rejects_whitespace_and_empty_key(client):
    """Empty or whitespace-only keys are invalid, never a match."""
    assert (
        client.get("/api/admin/purge-stats", headers={"X-Admin-Key": ""}).status_code
        == 401
    )
    assert (
        client.get(
            "/api/admin/purge-stats", headers={"X-Admin-Key": "   "}
        ).status_code
        == 401
    )


def test_admin_rejects_prefix_matching_key(client):
    """A key that merely starts with the real key must be rejected (guards
    against a naive startswith()-style comparison and proves the check is
    full-string and constant-time)."""
    response = client.get(
        "/api/admin/purge-stats",
        headers={"X-Admin-Key": f"{ADMIN_KEY}-suffix"},
    )
    assert response.status_code == 401


def test_admin_uses_constant_time_comparison(client, monkeypatch):
    """The guard must compare keys via hmac.compare_digest, not plain ==."""
    import hmac

    import src.api.main as api_main

    calls = []
    original = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return original(a, b)

    monkeypatch.setattr(api_main.hmac, "compare_digest", spy)
    assert client.get("/api/admin/purge-stats", headers=_admin_headers()).status_code == 200
    assert len(calls) == 1
    a, b = calls[0]
    assert isinstance(a, bytes) and isinstance(b, bytes)
    assert b == ADMIN_KEY.encode()


def test_admin_key_not_leaked_in_openapi(client):
    """The real admin key must never appear in the public OpenAPI schema."""
    schema = client.get("/openapi.json").json()
    assert ADMIN_KEY not in json.dumps(schema)


def test_admin_refused_when_key_not_configured(client, monkeypatch):
    """If ADMIN_API_KEY is unset, admin endpoints return 503."""
    import src.api.main as api_main

    monkeypatch.setattr(api_main, "ADMIN_API_KEY", "")
    response = client.get("/api/admin/purge-stats", headers=_admin_headers())
    assert response.status_code == 503


def test_admin_purge_stats_shape(client):
    """The stats endpoint reports TTL config + recent purge runs."""
    response = client.get("/api/admin/purge-stats", headers=_admin_headers())
    assert response.status_code == 200
    data = response.json()
    assert data["ttl_hours"] == 5.0
    assert data["purge_interval_minutes"] == 30.0
    assert data["total_runs"] >= 0
    assert data["total_deleted"] >= 0
    assert isinstance(data["recent_runs"], list)
    # every entry has the expected fields
    for run in data["recent_runs"]:
        assert set(run) >= {"run", "at", "deleted", "error"}


def test_admin_config_defaults(client):
    """GET /api/admin/config returns env defaults for every known key."""
    response = client.get("/api/admin/config", headers=_admin_headers())
    assert response.status_code == 200
    data = response.json()
    assert data["outage_ttl_hours"] == "5"
    assert data["outage_purge_interval_minutes"] == "30"
    assert data["map_refresh_seconds"] == "60"


def test_admin_config_requires_key(client):
    """Config endpoints are admin-only."""
    assert client.get("/api/admin/config").status_code == 401
    assert client.put("/api/admin/config", json={"settings": {}}).status_code == 401


def test_admin_config_update_persists_and_reads_back(client):
    """PUT updates a setting; GET afterwards returns the stored value."""
    response = client.put(
        "/api/admin/config",
        headers=_admin_headers(),
        json={"settings": {"outage_ttl_hours": "12"}},
    )
    assert response.status_code == 200
    assert response.json()["outage_ttl_hours"] == "12"

    again = client.get("/api/admin/config", headers=_admin_headers()).json()
    assert again["outage_ttl_hours"] == "12"
    # untouched keys keep their defaults
    assert again["map_refresh_seconds"] == "60"


def test_admin_config_rejects_unknown_keys(client):
    response = client.put(
        "/api/admin/config",
        headers=_admin_headers(),
        json={"settings": {"not_a_real_key": "1"}},
    )
    assert response.status_code == 400
    assert "not_a_real_key" in response.json()["detail"]


def test_admin_config_affects_purge_stats(client):
    """Changing the TTL is reflected in purge stats without a restart."""
    client.put(
        "/api/admin/config",
        headers=_admin_headers(),
        json={"settings": {"outage_ttl_hours": "8", "outage_purge_interval_minutes": "45"}},
    )
    stats = client.get("/api/admin/purge-stats", headers=_admin_headers()).json()
    assert stats["ttl_hours"] == 8.0
    assert stats["purge_interval_minutes"] == 45.0


def test_admin_purge_now_deletes_expired(api_env):
    """POST /api/admin/purge deletes reports older than the TTL."""
    import src.api.main as api_main

    client, factory = api_env
    old_factory = api_main.AsyncSessionLocal
    api_main.AsyncSessionLocal = factory
    try:
        # Insert one old report directly through the factory.
        async def _seed_old_report():
            from datetime import datetime, timedelta, timezone

            from src.database.models import OutageReport

            async with factory() as session:
                session.add(
                    OutageReport(
                        region="Tunis",
                        latitude=36.8,
                        longitude=10.18,
                        created_at=datetime.now(timezone.utc) - timedelta(hours=6),
                    )
                )
                await session.commit()

        asyncio.run(_seed_old_report())

        response = client.post("/api/admin/purge", headers=_admin_headers())
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "deleted": 1}

        # The list endpoint no longer returns it.
        listed = client.get("/api/outages").json()
        assert listed == []
    finally:
        api_main.AsyncSessionLocal = old_factory


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def test_conversations_create_list_get(client):
    created = client.post("/api/conversations", json={"title": "Ma session"}).json()
    assert created["title"] == "Ma session"

    listed = client.get("/api/conversations").json()
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]

    detail = client.get(f"/api/conversations/{created['id']}").json()
    assert detail["title"] == "Ma session"
    assert detail["messages"] == []


def test_conversation_detail_includes_messages(api_env):
    import uuid

    client, factory = api_env
    created = client.post("/api/conversations", json={"title": "Historique"}).json()
    conversation_id = created["id"]

    sources = [{"source_file": "anme_rapport.pdf", "page": 4}]

    async def _persist():
        async with factory() as session:
            await persist_chat_turn(
                session, uuid.UUID(conversation_id), "Question", "Reponse", sources
            )

    asyncio.run(_persist())

    detail = client.get(f"/api/conversations/{conversation_id}").json()
    assert len(detail["messages"]) == 2
    assert detail["messages"][0]["role"] == "user"
    assert detail["messages"][0]["content"] == "Question"
    assert detail["messages"][1]["role"] == "assistant"
    assert detail["messages"][1]["sources"] == sources


def test_conversation_not_found(client):
    assert client.get("/api/conversations/00000000-0000-0000-0000-000000000999").status_code == 404


# ---------------------------------------------------------------------------
# SSE chat stream (contract only - no real LLM call)
# ---------------------------------------------------------------------------

def test_chat_stream_rejects_empty_query(client):
    response = client.post("/api/chat/stream", json={"query": "   "})
    assert response.status_code == 400


def test_chat_stream_emits_sse_frames(api_env):
    """The stream endpoint must return text/event-stream and parseable data frames.

    Avoids a real LLM call (stub stream_pipeline) and real Postgres
    (patch AsyncSessionLocal with the test factory).
    """
    import src.api.main as api_main

    client, factory = api_env

    async def fake_stream(user_query, chat_history=None):
        yield {"type": "status", "message": "contextualizing"}
        yield {"type": "status", "message": "searching"}
        yield {
            "type": "sources",
            "sources": [{"source_file": "doc.pdf", "page": 1, "content": "extrait"}],
        }
        yield {"type": "token", "content": "Bon"}
        yield {"type": "token", "content": "jour"}
        yield {"type": "done", "answer": "Bonjour", "sources": []}

    original_stream = api_main.stream_pipeline
    original_session = api_main.AsyncSessionLocal
    api_main.stream_pipeline = fake_stream
    api_main.AsyncSessionLocal = factory
    try:
        response = client.post(
            "/api/chat/stream",
            json={"query": "Bonjour", "chat_history": []},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        body = response.text
        assert "data: " in body
        # all 6 events present
        import json

        events = [
            json.loads(line[6:])
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        types = [e["type"] for e in events]
        assert types == ["status", "status", "sources", "token", "token", "done"]
        assert events[2]["sources"][0]["source_file"] == "doc.pdf"
        assert events[-1]["answer"] == "Bonjour"
    finally:
        api_main.stream_pipeline = original_stream
        api_main.AsyncSessionLocal = original_session
