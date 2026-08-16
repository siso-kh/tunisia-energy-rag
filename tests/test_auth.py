"""Tests for user authentication (src/api/auth.py + src/utils/security.py).

Uses the same in-memory SQLite override pattern as test_api_db.py. The
JWT_SECRET is monkeypatched per-test so the suite is hermetic and the
"unconfigured -> 503" path is exercised explicitly.
"""

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.database.connection import build_engine, build_session_factory, get_db_dependency
from src.database.models import Base, OutageReport, User
from src.database.service import DEMO_USER_ID
from src.utils import security


TEST_JWT_SECRET = "test-jwt-secret-0123456789abcdef"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    monkeypatch.setattr(security, "JWT_SECRET", TEST_JWT_SECRET)


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


@pytest.fixture()
def api_env():
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


def _register(client, email="user@example.com", password="password123", **extra):
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": password, **extra},
    )


def _auth_headers(token: str):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

def test_register_returns_token_and_user(client):
    response = _register(client)
    assert response.status_code == 201
    data = response.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["user"]["email"] == "user@example.com"
    assert data["user"]["id"]
    # Never echo the password
    assert "password" not in json.dumps(data)


def test_register_normalizes_email_and_keeps_display_name(client):
    response = _register(client, email="  User@Example.COM ", display_name="Amine")
    assert response.status_code == 201
    data = response.json()
    assert data["user"]["email"] == "user@example.com"
    assert data["user"]["display_name"] == "Amine"


def test_register_duplicate_email_conflict(client):
    assert _register(client).status_code == 201
    response = _register(client, email="user@example.com")
    assert response.status_code == 409
    # Same email different case is still a duplicate
    response = _register(client, email="USER@EXAMPLE.COM")
    assert response.status_code == 409


def test_register_invalid_email_422(client):
    assert _register(client, email="not-an-email").status_code == 422
    assert _register(client, email="").status_code == 422


def test_register_short_password_422(client):
    assert _register(client, password="short").status_code == 422


def test_register_overlong_password_422(client):
    # bcrypt silently truncates at 72 bytes; we reject instead of truncating.
    assert _register(client, password="x" * 73).status_code == 422


def test_register_refused_when_jwt_unconfigured(client, monkeypatch):
    monkeypatch.setattr(security, "JWT_SECRET", "")
    response = _register(client)
    assert response.status_code == 503
    assert "JWT_SECRET" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def test_login_success(client):
    _register(client)
    response = client.post(
        "/api/auth/login", json={"email": "user@example.com", "password": "password123"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["access_token"]
    assert data["user"]["email"] == "user@example.com"


def test_login_wrong_password_401(client):
    _register(client)
    response = client.post(
        "/api/auth/login", json={"email": "user@example.com", "password": "wrong-password"}
    )
    assert response.status_code == 401


def test_login_unknown_email_401_identical_body(client):
    """No oracle: unknown email and wrong password must look identical."""
    _register(client)
    wrong_pw = client.post(
        "/api/auth/login", json={"email": "user@example.com", "password": "wrong-password"}
    )
    unknown = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"}
    )
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.text == unknown.text


def test_login_case_insensitive_email(client):
    _register(client, email="user@example.com")
    response = client.post(
        "/api/auth/login", json={"email": "USER@example.com", "password": "password123"}
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# /me (Bearer token)
# ---------------------------------------------------------------------------

def _token(client, email="user@example.com", password="password123") -> str:
    _register(client, email=email, password=password)
    data = client.post(
        "/api/auth/login", json={"email": email, "password": password}
    ).json()
    return data["access_token"]


def test_me_returns_user(client):
    token = _token(client)
    response = client.get("/api/auth/me", headers=_auth_headers(token))
    assert response.status_code == 200
    assert response.json()["email"] == "user@example.com"


def test_me_without_token_401(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_invalid_token_401(client):
    assert client.get("/api/auth/me", headers=_auth_headers("garbage.token.here")).status_code == 401


def test_me_expired_token_401(client):
    token = _token(client)
    payload = pyjwt.decode(token, TEST_JWT_SECRET, algorithms=["HS256"])
    payload["exp"] = datetime.now(timezone.utc) - timedelta(hours=1)
    expired = pyjwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")
    response = client.get("/api/auth/me", headers=_auth_headers(expired))
    assert response.status_code == 401


def test_me_rejects_token_for_deleted_user(api_env):
    client, factory = api_env
    token = _token(client)
    # Delete the user straight from the DB; the token must no longer resolve.
    async def _wipe():
        async with factory() as session:
            user = await session.execute(
                select(User).where(User.email == "user@example.com")
            )
            await session.delete(user.scalar_one())
            await session.commit()

    asyncio.run(_wipe())
    assert client.get("/api/auth/me", headers=_auth_headers(token)).status_code == 401


def test_me_401_identical_for_missing_and_invalid(client):
    missing = client.get("/api/auth/me")
    invalid = client.get("/api/auth/me", headers=_auth_headers("nope"))
    assert missing.status_code == invalid.status_code == 401
    assert missing.text == invalid.text


# ---------------------------------------------------------------------------
# Password storage
# ---------------------------------------------------------------------------

def test_password_stored_as_bcrypt_hash(api_env):
    client, factory = api_env
    _register(client)

    async def _check():
        async with factory() as session:
            user = await session.execute(
                select(User).where(User.email == "user@example.com")
            )
            row = user.scalar_one()
            assert row.password_hash
            assert row.password_hash != "password123"
            assert row.password_hash.startswith("$2")  # bcrypt marker
            assert security.verify_password("password123", row.password_hash)
            assert not security.verify_password("wrong", row.password_hash)

    asyncio.run(_check())


# ---------------------------------------------------------------------------
# Ownership: authenticated user vs anonymous demo user
# ---------------------------------------------------------------------------

def test_conversations_scoped_to_authenticated_user(api_env):
    client, factory = api_env
    token = _token(client)

    # Anonymous: conversation lands on the shared demo user.
    anon = client.post("/api/conversations", json={"title": "Anonyme"}).json()
    assert anon["id"]

    # Authenticated: conversation lands on the registered user.
    authed = client.post(
        "/api/conversations", json={"title": "Connecté"}, headers=_auth_headers(token)
    ).json()
    assert authed["id"]

    async def _owner_ids():
        from src.database.models import Conversation

        async with factory() as session:
            anon_conv = await session.get(Conversation, uuid.UUID(anon["id"]))
            authed_conv = await session.get(Conversation, uuid.UUID(authed["id"]))
            return str(anon_conv.user_id), str(authed_conv.user_id)

    anon_owner, authed_owner = asyncio.run(_owner_ids())
    assert anon_owner == str(DEMO_USER_ID)
    assert authed_owner != str(DEMO_USER_ID)

    # Listing: the authenticated user sees only their own conversation.
    listed = client.get("/api/conversations", headers=_auth_headers(token)).json()
    assert [c["id"] for c in listed] == [authed["id"]]


def test_outage_report_attaches_authenticated_user(api_env):
    client, factory = api_env
    token = _token(client)
    created = client.post(
        "/api/outages",
        json={
            "utility": "STEG",
            "region": "Tunis",
            "latitude": 36.8,
            "longitude": 10.18,
            "description": "Coupure test",
        },
        headers=_auth_headers(token),
    ).json()

    async def _reporter():
        async with factory() as session:
            report = await session.get(OutageReport, uuid.UUID(created["id"]))
            return str(report.user_id) if report.user_id else None

    reporter_id = asyncio.run(_reporter())
    assert reporter_id is not None
    assert reporter_id != str(DEMO_USER_ID)
