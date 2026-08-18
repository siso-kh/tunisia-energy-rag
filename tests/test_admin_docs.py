"""Tests for the admin document-ingestion endpoints (upload file / ingest URL).

Follows the same hermetic pattern as ``tests/test_api_db.py``: in-memory
SQLite via dependency overrides + admin API key monkeypatched, with the
heavy triage/index steps stubbed so no LLM or ChromaDB is touched.
"""

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool

import src.ingestion.admin_ingest as admin_ingest
from src.api import main as api_main
from src.database.connection import build_engine, build_session_factory, get_db_dependency
from src.database.models import Base

ADMIN_KEY = "test-admin-key-123"

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF\n"


# ---------------------------------------------------------------------------
# Fixtures (mirrors tests/test_api_db.py)
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
    """Yields (TestClient, tmp raw/filtered/blacklisted dirs) with stubbed triage/index."""
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

    # Point the ingest dirs at the tmp dirs so tests never touch real data/.
    raw = tmp_path / "raw"
    filtered = tmp_path / "filtered"
    blacklisted = tmp_path / "blacklisted"
    monkeypatch.setattr(admin_ingest, "RAW_DIR", raw)
    monkeypatch.setattr(admin_ingest, "FILTERED_DIR", filtered)
    monkeypatch.setattr(admin_ingest, "BLACKLISTED_DIR", blacklisted)

    # Admin key for the duration of the test.
    monkeypatch.setattr(api_main, "ADMIN_API_KEY", ADMIN_KEY)

    with TestClient(app) as client:
        yield client, raw, filtered, blacklisted

    app.dependency_overrides.clear()

    async def _teardown():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    asyncio.run(_teardown())


def _admin_headers():
    return {"X-Admin-Key": ADMIN_KEY}


def _fake_triage(status: str, dest: str):
    """A triage_fn stub returning a fixed decision."""
    def _triage(pdf_path, client=None):
        return {
            "filename": Path(pdf_path).name,
            "status": status,
            "gate1_score": 92.0,
            "gate2_score": 88.0 if status == "PASSED" else 12.0,
            "master_score": 90.0 if status == "PASSED" else 10.0,
            "total_pages": 5,
            "shortcircuited": False,
            "dest": dest,
        }
    return _triage


def _fake_index(chunks: int = 7):
    def _index(pdf_path):
        return chunks
    return _index


# ---------------------------------------------------------------------------
# Auth & validation
# ---------------------------------------------------------------------------

def test_documents_upload_requires_admin_key(api_env):
    client, *_ = api_env
    response = client.post(
        "/api/admin/documents/upload",
        files={"file": ("doc.pdf", io.BytesIO(MINIMAL_PDF), "application/pdf")},
    )
    assert response.status_code == 401
    wrong = client.post(
        "/api/admin/documents/upload",
        headers={"X-Admin-Key": "wrong"},
        files={"file": ("doc.pdf", io.BytesIO(MINIMAL_PDF), "application/pdf")},
    )
    assert wrong.status_code == 401


def test_documents_from_url_requires_admin_key(api_env):
    client, *_ = api_env
    response = client.post("/api/admin/documents/from-url", json={"url": "https://example.com/doc.pdf"})
    assert response.status_code == 401


def test_documents_upload_rejects_non_pdf(api_env):
    client, *_ = api_env
    response = client.post(
        "/api/admin/documents/upload",
        headers=_admin_headers(),
        files={"file": ("doc.txt", io.BytesIO(b"not a pdf"), "text/plain")},
    )
    assert response.status_code == 400
    assert "not a valid PDF" in response.json()["detail"]


def test_documents_from_url_rejects_bad_scheme(api_env):
    client, *_ = api_env
    response = client.post(
        "/api/admin/documents/from-url",
        headers=_admin_headers(),
        json={"url": "ftp://example.com/doc.pdf"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Upload flow
# ---------------------------------------------------------------------------

def test_upload_accepted_indexes_and_moves_to_filtered(api_env, monkeypatch):
    client, raw, filtered, blacklisted = api_env
    monkeypatch.setattr(admin_ingest, "triage_file", _fake_triage("PASSED", "filtered"))
    monkeypatch.setattr(admin_ingest, "index_pdf_into_chroma", _fake_index(7))

    response = client.post(
        "/api/admin/documents/upload",
        headers=_admin_headers(),
        files={"file": ("guide_efficacite.pdf", io.BytesIO(MINIMAL_PDF), "application/pdf")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "PASSED"
    assert data["dest"] == "filtered"
    assert data["chunks_indexed"] == 7
    assert data["filename"] == "guide_efficacite.pdf"

    # File was moved raw -> filtered (not blacklisted).
    assert not (raw / "guide_efficacite.pdf").exists()
    assert (filtered / "guide_efficacite.pdf").exists()
    assert not (blacklisted / "guide_efficacite.pdf").exists()


def test_upload_rejected_moves_to_blacklisted_no_index(api_env, monkeypatch):
    client, raw, filtered, blacklisted = api_env
    monkeypatch.setattr(admin_ingest, "triage_file", _fake_triage("BLACKLISTED", "blacklisted"))
    indexed = monkeypatch.setattr(admin_ingest, "index_pdf_into_chroma", _fake_index(0))

    response = client.post(
        "/api/admin/documents/upload",
        headers=_admin_headers(),
        files={"file": ("rapport_aleatoire.pdf", io.BytesIO(MINIMAL_PDF), "application/pdf")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "BLACKLISTED"
    assert data["dest"] == "blacklisted"
    assert data["chunks_indexed"] == 0

    assert not (raw / "rapport_aleatoire.pdf").exists()
    assert (blacklisted / "rapport_aleatoire.pdf").exists()
    assert not (filtered / "rapport_aleatoire.pdf").exists()


def test_upload_keeps_original_filename_extension_rule(api_env, monkeypatch):
    """A non-.pdf filename still lands in filtered with a .pdf extension."""
    client, raw, filtered, _ = api_env
    monkeypatch.setattr(admin_ingest, "triage_file", _fake_triage("PASSED", "filtered"))
    monkeypatch.setattr(admin_ingest, "index_pdf_into_chroma", _fake_index(3))

    response = client.post(
        "/api/admin/documents/upload",
        headers=_admin_headers(),
        files={"file": ("weird name!.bin", io.BytesIO(MINIMAL_PDF), "application/pdf")},
    )
    assert response.status_code == 200
    # sanitize_filename: non [a-zA-Z0-9_-. ] chars -> _, no .pdf -> append .pdf
    assert response.json()["filename"] == "weird_name_.bin.pdf"


# ---------------------------------------------------------------------------
# From-URL flow
# ---------------------------------------------------------------------------

def test_from_url_accepted_downloads_and_indexes(api_env, monkeypatch):
    client, raw, filtered, _ = api_env

    def _fake_download(url):
        raw.mkdir(parents=True, exist_ok=True)
        dest = raw / "remote_doc.pdf"
        dest.write_bytes(MINIMAL_PDF)
        return dest

    # main.py imports download_pdf_from_url by name, so patch it there.
    monkeypatch.setattr(api_main, "download_pdf_from_url", _fake_download)
    monkeypatch.setattr(admin_ingest, "triage_file", _fake_triage("PASSED", "filtered"))
    monkeypatch.setattr(admin_ingest, "index_pdf_into_chroma", _fake_index(5))

    response = client.post(
        "/api/admin/documents/from-url",
        headers=_admin_headers(),
        json={"url": "https://example.org/rapports/remote_doc.pdf"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "PASSED"
    assert data["chunks_indexed"] == 5
    assert (filtered / "remote_doc.pdf").exists()


def test_from_url_download_failure_returns_400(api_env, monkeypatch):
    client, *_ = api_env

    def _fail_download(url):
        raise ValueError("URL did not return a PDF (Content-Type: text/html).")

    monkeypatch.setattr(api_main, "download_pdf_from_url", _fail_download)

    response = client.post(
        "/api/admin/documents/from-url",
        headers=_admin_headers(),
        json={"url": "https://example.org/not-a-pdf"},
    )
    assert response.status_code == 400
    assert "not return a PDF" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Response contract (OpenAPI shape)
# ---------------------------------------------------------------------------

def test_documents_ingest_out_shape_in_openapi(api_env):
    client, *_ = api_env
    schema = json.loads(client.get("/openapi.json").text)
    schema_str = json.dumps(schema)
    assert "/api/admin/documents/upload" in schema_str
    assert "/api/admin/documents/from-url" in schema_str
    assert ADMIN_KEY not in schema_str
