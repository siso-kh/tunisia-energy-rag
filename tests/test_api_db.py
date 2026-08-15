"""Tests for the DB-backed API endpoints (src/api/main.py).

Uses FastAPI dependency overrides so the endpoints run against an in-memory
SQLite database instead of Postgres, keeping the tests fast and hermetic.
"""

import asyncio

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
