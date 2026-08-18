"""Tests for the admin sources API (URL research + ingest).

Follows the same hermetic pattern as ``tests/test_admin_docs.py``: in-memory
SQLite via dependency overrides + admin API key monkeypatched, with heavy
download/triage/index steps stubbed.
"""

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

import src.ingestion.research as research_mod
from src.api import main as api_main
from src.database.connection import build_engine, build_session_factory, get_db_dependency
from src.database.models import Base, Source, SourceStatus

ADMIN_KEY = "test-admin-key-123"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _make_test_db():
    engine = build_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    return engine, build_session_factory(engine)


@pytest.fixture()
def api_env(tmp_path, monkeypatch):
    """Yields (TestClient, tmp raw dir) with stubbed research."""
    import asyncio

    engine, factory = _make_test_db()

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    async def override_get_db():
        async with factory() as session:
            yield session

    app = api_main.app
    app.dependency_overrides[get_db_dependency] = override_get_db

    raw = tmp_path / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(research_mod, "RAW_DIR", raw)

    monkeypatch.setattr(api_main, "ADMIN_API_KEY", ADMIN_KEY)

    with TestClient(app) as client:
        yield client, raw

    app.dependency_overrides.clear()

    async def _teardown():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    asyncio.run(_teardown())


def _admin_headers():
    return {"X-Admin-Key": ADMIN_KEY}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def test_sources_requires_admin_key(api_env):
    client, *_ = api_env
    assert client.get("/api/admin/sources").status_code == 401
    assert client.post("/api/admin/sources", json={"url": "https://x.com/a.pdf"}).status_code == 401
    assert client.post("/api/admin/sources/research").status_code == 401
    assert client.post("/api/admin/sources/ingest").status_code == 401


def test_sources_wrong_key(api_env):
    client, *_ = api_env
    headers = {"X-Admin-Key": "wrong"}
    assert client.get("/api/admin/sources", headers=headers).status_code == 401


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def test_add_and_list_source(api_env):
    client, *_ = api_env
    resp = client.post(
        "/api/admin/sources",
        headers=_admin_headers(),
        json={"url": "https://example.org/doc.pdf"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["url"] == "https://example.org/doc.pdf"
    assert data["status"] == "pending"
    assert "id" in data

    resp = client.get("/api/admin/sources", headers=_admin_headers())
    assert resp.status_code == 200
    sources = resp.json()
    assert len(sources) == 1
    assert sources[0]["url"] == "https://example.org/doc.pdf"


def test_add_duplicate_url_returns_409(api_env):
    client, *_ = api_env
    client.post(
        "/api/admin/sources",
        headers=_admin_headers(),
        json={"url": "https://example.org/doc.pdf"},
    )
    resp = client.post(
        "/api/admin/sources",
        headers=_admin_headers(),
        json={"url": "https://example.org/doc.pdf"},
    )
    assert resp.status_code == 409


def test_delete_source(api_env):
    client, *_ = api_env
    resp = client.post(
        "/api/admin/sources",
        headers=_admin_headers(),
        json={"url": "https://example.org/doc.pdf"},
    )
    source_id = resp.json()["id"]

    resp = client.delete(f"/api/admin/sources/{source_id}", headers=_admin_headers())
    assert resp.status_code == 200

    resp = client.get("/api/admin/sources", headers=_admin_headers())
    assert len(resp.json()) == 0


def test_delete_nonexistent_returns_404(api_env):
    client, *_ = api_env
    fake_id = "00000000-0000-0000-0000-000000000000"
    resp = client.delete(f"/api/admin/sources/{fake_id}", headers=_admin_headers())
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Research (stubbed download)
# ---------------------------------------------------------------------------
def test_research_downloads_pending_sources(api_env, monkeypatch):
    client, raw = api_env

    client.post(
        "/api/admin/sources",
        headers=_admin_headers(),
        json={"url": "https://example.org/test.pdf"},
    )

    # Stub download_pdf_from_source to simulate a successful download.
    def _fake_download(url):
        return {"filename": "test.pdf", "file_size": 1024, "error": None}

    monkeypatch.setattr(api_main, "download_pdf_from_source", _fake_download)

    resp = client.post("/api/admin/sources/research", headers=_admin_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["downloaded"] == 1
    assert data["failed"] == 0
    assert data["total"] == 1

    # Verify status updated in DB.
    resp = client.get("/api/admin/sources", headers=_admin_headers())
    assert resp.json()[0]["status"] == "downloaded"
    assert resp.json()[0]["filename"] == "test.pdf"


def test_research_handles_download_failure(api_env, monkeypatch):
    client, *_ = api_env

    client.post(
        "/api/admin/sources",
        headers=_admin_headers(),
        json={"url": "https://example.org/bad.pdf"},
    )

    def _fail_download(url):
        return {"filename": None, "file_size": None, "error": "Download failed: 404"}

    monkeypatch.setattr(api_main, "download_pdf_from_source", _fail_download)

    resp = client.post("/api/admin/sources/research", headers=_admin_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["downloaded"] == 0
    assert data["failed"] == 1


def test_research_skips_already_downloaded(api_env, monkeypatch):
    """After a successful research, a second research call skips the source."""
    client, raw = api_env

    client.post(
        "/api/admin/sources",
        headers=_admin_headers(),
        json={"url": "https://example.org/already.pdf"},
    )

    # Stub download to succeed.
    def _fake_download(url):
        return {"filename": "already.pdf", "file_size": 1024, "error": None}

    monkeypatch.setattr(api_main, "download_pdf_from_source", _fake_download)

    # First research: downloads the source (status → downloaded).
    resp = client.post("/api/admin/sources/research", headers=_admin_headers())
    assert resp.json()["total"] == 1
    assert resp.json()["downloaded"] == 1

    # Second research: source is no longer pending, so total=0.
    resp = client.post("/api/admin/sources/research", headers=_admin_headers())
    assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Ingest (stubbed triage + index)
# ---------------------------------------------------------------------------
def test_ingest_processes_downloaded_sources(api_env, monkeypatch):
    """Use the research endpoint to download, then ingest."""
    client, *_ = api_env

    client.post(
        "/api/admin/sources",
        headers=_admin_headers(),
        json={"url": "https://example.org/good.pdf"},
    )

    # Stub download to succeed.
    def _fake_download(url):
        return {"filename": "good.pdf", "file_size": 1024, "error": None}

    monkeypatch.setattr(api_main, "download_pdf_from_source", _fake_download)

    # Stub ingest to simulate successful indexing.
    def _fake_ingest(filename):
        return {
            "status": "indexed",
            "total_pages": 10,
            "gate1_score": 85.0,
            "master_score": 82.0,
            "chunks_indexed": 15,
            "error": None,
        }

    monkeypatch.setattr(api_main, "ingest_downloaded_pdf", _fake_ingest)

    # Research: download the source.
    resp = client.post("/api/admin/sources/research", headers=_admin_headers())
    assert resp.json()["downloaded"] == 1

    # Ingest: triage + index.
    resp = client.post("/api/admin/sources/ingest", headers=_admin_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["indexed"] == 1
    assert data["rejected"] == 0
    assert data["total"] == 1

    # Verify status and scores in DB.
    resp = client.get("/api/admin/sources", headers=_admin_headers())
    src = resp.json()[0]
    assert src["status"] == "indexed"
    assert src["chunks_indexed"] == 15
    assert src["master_score"] == 82.0


def test_ingest_handles_triage_rejection(api_env, monkeypatch):
    """Use research to download, then ingest rejects the document."""
    client, *_ = api_env

    client.post(
        "/api/admin/sources",
        headers=_admin_headers(),
        json={"url": "https://example.org/bad_content.pdf"},
    )

    # Stub download to succeed.
    def _fake_download(url):
        return {"filename": "bad_content.pdf", "file_size": 1024, "error": None}

    monkeypatch.setattr(api_main, "download_pdf_from_source", _fake_download)

    # Stub ingest to simulate triage rejection.
    def _fake_reject(filename):
        return {
            "status": "triage_rejected",
            "total_pages": 5,
            "gate1_score": 15.0,
            "master_score": 12.0,
            "chunks_indexed": 0,
            "error": None,
        }

    monkeypatch.setattr(api_main, "ingest_downloaded_pdf", _fake_reject)

    # Research: download the source.
    resp = client.post("/api/admin/sources/research", headers=_admin_headers())
    assert resp.json()["downloaded"] == 1

    # Ingest: triage rejects it.
    resp = client.post("/api/admin/sources/ingest", headers=_admin_headers())
    assert resp.json()["rejected"] == 1


def test_ingest_skips_non_downloaded(api_env):
    client, *_ = api_env

    client.post(
        "/api/admin/sources",
        headers=_admin_headers(),
        json={"url": "https://example.org/pending.pdf"},
    )

    resp = client.post("/api/admin/sources/ingest", headers=_admin_headers())
    assert resp.json()["total"] == 0  # nothing downloaded


# ---------------------------------------------------------------------------
# OpenAPI shape
# ---------------------------------------------------------------------------
def test_sources_endpoints_in_openapi(api_env):
    client, *_ = api_env
    schema = json.loads(client.get("/openapi.json").text)
    schema_str = json.dumps(schema)
    assert "/api/admin/sources" in schema_str
    assert "/api/admin/sources/research" in schema_str
    assert "/api/admin/sources/ingest" in schema_str
    assert ADMIN_KEY not in schema_str
