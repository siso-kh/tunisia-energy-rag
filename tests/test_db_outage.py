"""The API must keep serving when Postgres is unreachable.

The container entrypoint previously treated a failed `alembic upgrade head` as
fatal and called `exit 1`. A bad DATABASE_URL therefore killed the container,
Render had no live deploy, and every route -- including chat and /health, none
of which touch the database -- answered 502 with `x-render-routing: no-deploy`.

This pins the opposite behaviour: chat, retrieval and /health keep working with
the database down, and only the DB-backed features degrade.
"""

import json

import pytest


@pytest.fixture(scope="module")
def real_app():
    """Import the app once for the offline checks (heavy: loads the index)."""
    from src.api.main import app

    return app


def test_health_does_not_require_the_database(real_app):
    from fastapi.testclient import TestClient

    with TestClient(real_app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_chat_streams_tokens_without_the_database(real_app, monkeypatch):
    """Chat must answer even with no DB: persistence is best-effort."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("WARM_RERANKER_ON_STARTUP", "false")

    with TestClient(real_app) as client:
        response = client.post(
            "/api/chat/stream",
            json={"query": "Quel est le role de l'ANME ?", "chat_history": []},
        )

    assert response.status_code == 200

    tokens = 0
    errors = []
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if event.get("type") == "token":
            tokens += 1
        elif event.get("type") == "error":
            errors.append(event.get("message", ""))

    assert not errors, "chat errored without a database: %s" % errors
    assert tokens > 0, "chat produced no tokens without a database"


def test_retrieval_does_not_require_the_database():
    """Hybrid retrieval is ChromaDB-only; Postgres is irrelevant to it."""
    from src.rag import hybrid

    sources = hybrid.retrieve_hybrid("transition energetique", n_results=3)

    assert isinstance(sources, list)
    assert sources, "retrieval returned nothing"
    assert {"content", "source_file", "page"} <= set(sources[0])