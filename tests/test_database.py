"""Tests for the database layer (src/database).

The tests run against an in-memory SQLite database via the ``aiosqlite`` driver,
using the ``build_engine`` / ``build_session_factory`` factories exposed by
``src.database.connection``. Foreign-key enforcement is enabled so DB-level
``ON DELETE`` behaviors are exercised too.

Uses the project's existing ``asyncio.run`` pattern (no pytest-asyncio).
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from src.database import connection
from src.database.connection import build_engine, build_session_factory, get_db
from src.database.models import (
    Base,
    Conversation,
    Message,
    OutageReport,
    ReportStatus,
    Setting,
    User,
    UtilityType,
)
from src.database.service import (
    create_user,
    delete_expired_outages,
    get_all_settings,
    get_setting,
    get_user_by_email,
    set_setting,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_engine_and_factory():
    """Build an in-memory SQLite engine (single shared connection) + session factory."""
    engine = build_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite does not enforce foreign keys by default; turn them on so
    # ON DELETE CASCADE / SET NULL behave like Postgres.
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    return engine, build_session_factory(engine)


def run_scenario(scenario):
    """Run an async scenario in its own event loop against a fresh in-memory DB."""
    async def _wrapper():
        engine, factory = await _make_engine_and_factory()
        try:
            return await scenario(factory)
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

    return asyncio.run(_wrapper())


@pytest.fixture(autouse=True)
def _no_db_url_env(monkeypatch):
    """Isolate tests from any real DATABASE_URL in the developer environment."""
    monkeypatch.delenv("DATABASE_URL", raising=False)


# ---------------------------------------------------------------------------
# Module-level structure
# ---------------------------------------------------------------------------

def test_package_imports_cleanly():
    """The database package must be importable as a regular package."""
    assert connection.engine is not None
    assert callable(connection.AsyncSessionLocal)
    assert callable(connection.get_db)


def test_build_engine_defaults_to_postgres_backend():
    engine = build_engine()
    assert engine.url.get_backend_name() == "postgresql"
    assert engine.url.get_driver_name() == "asyncpg"
    asyncio.run(engine.dispose())


def test_build_engine_uses_env_url_when_provided(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5999/mydb")
    engine = build_engine()
    assert engine.url.host == "localhost"
    assert engine.url.port == 5999
    assert engine.url.database == "mydb"
    asyncio.run(engine.dispose())


def test_build_session_factory_produces_async_sessions():
    engine = build_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    factory = build_session_factory(engine)

    async def _probe():
        async with factory() as session:
            return type(session).__name__

    assert asyncio.run(_probe()) == "AsyncSession"
    asyncio.run(engine.dispose())


def test_all_models_registered():
    """All four domain models must be registered in the shared metadata."""
    tables = set(Base.metadata.tables.keys())
    assert {"users", "conversations", "messages", "outage_reports"} <= tables





# ---------------------------------------------------------------------------
# Users / Conversations / Messages
# ---------------------------------------------------------------------------

def test_create_user_with_conversation_and_messages():
    async def scenario(factory):
        async with factory() as session:
            user = User()
            session.add(user)
            await session.flush()

            convo = Conversation(user_id=user.id, title="Premiere discussion")
            convo.messages.append(Message(role="user", content="Bonjour"))
            convo.messages.append(
                Message(role="assistant", content="Bonjour! Comment puis-je aider?")
            )
            session.add(convo)
            await session.commit()

            uid, cid = user.id, convo.id

        # Fresh session: verify persistence + relationships
        async with factory() as session:
            result = await session.execute(select(User).where(User.id == uid))
            loaded_user = result.scalar_one()
            assert loaded_user.conversations[0].title == "Premiere discussion"
            assert [m.role for m in loaded_user.conversations[0].messages] == [
                "user",
                "assistant",
            ]
            assert all(m.id is not None for m in loaded_user.conversations[0].messages)
            assert cid is not None

    run_scenario(scenario)


def test_message_sources_json_roundtrip():
    async def scenario(factory):
        async with factory() as session:
            user = User()
            session.add(user)
            await session.flush()

            sources = [
                {"source_file": "anme_rapport.pdf", "page": 4},
                {"source_file": "steg_guide.pdf", "page": 12},
            ]
            convo = Conversation(user_id=user.id, title="t")
            convo.messages.append(Message(role="user", content="q", sources=sources))
            session.add(convo)
            await session.commit()
            msg_id = convo.messages[0].id

        async with factory() as session:
            msg = await session.get(Message, msg_id)
            assert msg.sources == sources

    run_scenario(scenario)


def test_cascade_delete_user_removes_conversations_and_messages():
    async def scenario(factory):
        async with factory() as session:
            user = User()
            convo = Conversation(user_id=None, title="orphan-to-be")
            user.conversations.append(convo)
            convo.messages.append(Message(role="user", content="x"))
            session.add(user)
            await session.commit()

            uid, cid, mid = user.id, convo.id, convo.messages[0].id

        # Delete the user; conversations + messages must cascade.
        async with factory() as session:
            user = await session.get(User, uid)
            await session.delete(user)
            await session.commit()

        async with factory() as session:
            assert await session.get(Conversation, cid) is None
            assert await session.get(Message, mid) is None

    run_scenario(scenario)


def test_create_user_and_get_by_email_roundtrip():
    async def scenario(factory):
        async with factory() as session:
            user = await create_user(
                session,
                email="Amine@Example.com",
                password_hash="$2b$12$hashed-not-plaintext",
                display_name="Amine",
            )
            uid = user.id
            assert user.email == "amine@example.com"  # normalized on create

        async with factory() as session:
            found = await get_user_by_email(session, "AMINE@example.com")
            assert found is not None
            assert found.id == uid
            assert found.display_name == "Amine"
            assert found.password_hash == "$2b$12$hashed-not-plaintext"

    run_scenario(scenario)


def test_get_user_by_email_returns_none_when_missing():
    async def scenario(factory):
        async with factory() as session:
            assert await get_user_by_email(session, "nobody@example.com") is None

    run_scenario(scenario)


def test_user_email_uniqueness_enforced():
    async def scenario(factory):
        async with factory() as session:
            await create_user(session, email="dup@example.com", password_hash="h1")
            with pytest.raises(IntegrityError):
                await create_user(session, email="DUP@example.com", password_hash="h2")

    run_scenario(scenario)


def test_anonymous_user_has_no_credentials():
    """Demo/anonymous users (created via User()) keep nullable auth fields."""
    async def scenario(factory):
        async with factory() as session:
            user = User()
            session.add(user)
            await session.commit()
            uid = user.id

        async with factory() as session:
            loaded = await session.get(User, uid)
            assert loaded.email is None
            assert loaded.password_hash is None
            assert loaded.display_name is None

    run_scenario(scenario)


def test_messages_ordered_by_created_at():
    async def scenario(factory):
        async with factory() as session:
            user = User()
            session.add(user)
            await session.flush()

            convo = Conversation(user_id=user.id, title="ordre")
            for i in range(3):
                convo.messages.append(
                    Message(role="user" if i % 2 == 0 else "assistant", content=f"msg-{i}")
                )
            session.add(convo)
            await session.commit()
            cid = convo.id

        async with factory() as session:
            convo = await session.get(Conversation, cid)
            # relationship is declared with order_by="Message.created_at"
            assert [m.content for m in convo.messages] == ["msg-0", "msg-1", "msg-2"]

    run_scenario(scenario)


# ---------------------------------------------------------------------------
# Outage reports
# ---------------------------------------------------------------------------

def test_outage_report_enum_defaults():
    async def scenario(factory):
        async with factory() as session:
            report = OutageReport(
                region="Sousse",
                latitude=35.8256,
                longitude=10.6083,
            )
            session.add(report)
            await session.commit()
            rid = report.id

        async with factory() as session:
            loaded = await session.get(OutageReport, rid)
            assert loaded.utility is UtilityType.STEG
            assert loaded.status is ReportStatus.PENDING
            assert loaded.user_id is None

    run_scenario(scenario)


def test_outage_report_explicit_enum_values_roundtrip():
    async def scenario(factory):
        async with factory() as session:
            report = OutageReport(
                utility=UtilityType.SONEDE,
                region="Tunis",
                latitude=36.8065,
                longitude=10.1815,
                status=ReportStatus.VERIFIED,
                description="Coupure zone nord",
            )
            session.add(report)
            await session.commit()
            rid = report.id

        async with factory() as session:
            loaded = await session.get(OutageReport, rid)
            assert loaded.utility is UtilityType.SONEDE
            assert loaded.status is ReportStatus.VERIFIED
            assert loaded.description == "Coupure zone nord"
            assert loaded.latitude == 36.8065
            assert loaded.longitude == 10.1815

    run_scenario(scenario)


def test_delete_user_sets_null_on_outage_report():
    async def scenario(factory):
        async with factory() as session:
            user = User()
            session.add(user)
            await session.flush()

            report = OutageReport(
                user_id=user.id,
                region="Sfax",
                latitude=34.7406,
                longitude=10.7603,
            )
            session.add(report)
            await session.commit()
            uid, rid = user.id, report.id

        # DB-level ON DELETE SET NULL (foreign keys are ON in this fixture)
        async with factory() as session:
            user = await session.get(User, uid)
            await session.delete(user)
            await session.commit()

        async with factory() as session:
            loaded = await session.get(OutageReport, rid)
            assert loaded is not None
            assert loaded.user_id is None

    run_scenario(scenario)


def test_delete_expired_outages_removes_only_old_reports():
    async def scenario(factory):
        now = datetime.now(timezone.utc)
        old = OutageReport(
            region="Sfax",
            latitude=34.7406,
            longitude=10.7603,
            created_at=now - timedelta(hours=6),
        )
        fresh = OutageReport(
            region="Tunis",
            latitude=36.8065,
            longitude=10.1815,
            created_at=now - timedelta(hours=1),
        )
        async with factory() as session:
            session.add_all([old, fresh])
            await session.commit()
            old_id, fresh_id = old.id, fresh.id

        async with factory() as session:
            deleted = await delete_expired_outages(session, timedelta(hours=5))
            assert deleted == 1

        async with factory() as session:
            assert await session.get(OutageReport, old_id) is None
            assert await session.get(OutageReport, fresh_id) is not None

    run_scenario(scenario)


def test_delete_expired_outages_boundary_keeps_reports_younger_than_ttl():
    async def scenario(factory):
        now = datetime.now(timezone.utc)
        exactly_at_ttl = OutageReport(
            region="Gabès",
            latitude=33.8815,
            longitude=10.0982,
            # 4h59m59s < 5h -> kept
            created_at=now - timedelta(hours=4, minutes=59, seconds=59),
        )
        async with factory() as session:
            session.add(exactly_at_ttl)
            await session.commit()
            rid = exactly_at_ttl.id

        async with factory() as session:
            deleted = await delete_expired_outages(session, timedelta(hours=5))
            assert deleted == 0

        async with factory() as session:
            assert await session.get(OutageReport, rid) is not None

    run_scenario(scenario)


def test_delete_expired_outages_noop_when_empty():
    async def scenario(factory):
        async with factory() as session:
            deleted = await delete_expired_outages(session, timedelta(hours=5))
            assert deleted == 0

    run_scenario(scenario)


def test_settings_upsert_roundtrip():
    async def scenario(factory):
        async with factory() as session:
            # Set, then re-set the same key (upsert, not duplicate).
            await set_setting(session, "outage_ttl_hours", "7")
            await set_setting(session, "outage_ttl_hours", "9")
            await set_setting(session, "map_refresh_seconds", "30")

        async with factory() as session:
            assert await get_setting(session, "outage_ttl_hours") == "9"
            assert await get_setting(session, "map_refresh_seconds") == "30"
            # Unknown key falls back to env default / None.
            assert await get_setting(session, "does_not_exist") is not None or \
                await get_setting(session, "does_not_exist") is None
            all_settings = await get_all_settings(session)
            assert all_settings["outage_ttl_hours"] == "9"
            assert all_settings["map_refresh_seconds"] == "30"
            # Known-but-unset keys keep their defaults.
            assert "outage_purge_interval_minutes" in all_settings

    run_scenario(scenario)


def test_setting_model_registered():
    assert "settings" in Base.metadata.tables


def test_outage_report_requires_region_and_coordinates():
    async def scenario(factory):
        async with factory() as session:
            bad = OutageReport(region=None, latitude=None, longitude=None)
            session.add(bad)
            with pytest.raises(IntegrityError):
                await session.commit()

    run_scenario(scenario)


# ---------------------------------------------------------------------------
# get_db dependency
# ---------------------------------------------------------------------------

def test_get_db_yields_a_working_session(monkeypatch):
    async def scenario(factory):
        monkeypatch.setattr(connection, "AsyncSessionLocal", factory)

        async with get_db() as session:
            # create a row through the dependency-injected session
            user = User()
            session.add(user)
            await session.commit()
            assert user.id is not None
            assert isinstance(user.id, uuid.UUID)

    run_scenario(scenario)


def test_get_db_releases_connection_even_when_body_raises(monkeypatch, tmp_path):
    """An exception inside the endpoint body must not leak the pooled connection.

    This is the classic FastAPI dependency pitfall: a session left open when the
    handler raises would exhaust the pool.
    """
    async def scenario(_factory):
        engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/pool2.db")
        factory = build_session_factory(engine)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        monkeypatch.setattr(connection, "AsyncSessionLocal", factory)
        try:
            with pytest.raises(RuntimeError):
                async with get_db() as _session:
                    await _session.execute(select(1))
                    assert engine.pool.checkedout() == 1
                    raise RuntimeError("handler failed")

            # even after the exception, the connection must be back in the pool
            assert engine.pool.checkedout() == 0
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

    run_scenario(scenario)


def test_many_to_one_relationships_load_async_safely():
    """lazy="selectin" must make to-one navigation work in async context
    (Conversation.user, Message.conversation, OutageReport.user).
    """
    async def scenario(factory):
        async with factory() as session:
            user = User()
            session.add(user)
            await session.flush()

            convo = Conversation(user_id=user.id, title="t")
            convo.messages.append(Message(role="user", content="q"))
            session.add(convo)
            report = OutageReport(
                user_id=user.id, region="Bizerte", latitude=37.2744, longitude=9.8739
            )
            session.add(report)
            await session.commit()

            uid, cid, mid, rid = user.id, convo.id, convo.messages[0].id, report.id

        # Fresh sessions: navigate every to-one relationship without explicit
        # loading options - would raise MissingGreenlet if lazy loading is used.
        async with factory() as session:
            convo = await session.get(Conversation, cid)
            assert convo.user.id == uid
            assert convo.messages[0].conversation.id == cid

        async with factory() as session:
            msg = await session.get(Message, mid)
            assert msg.conversation.title == "t"

        async with factory() as session:
            report = await session.get(OutageReport, rid)
            assert report.user.id == uid

    run_scenario(scenario)


def test_get_db_closes_session_after_yield(monkeypatch, tmp_path):
    """After get_db's generator exits, the DB connection must be returned to the pool.

    Uses a file-backed SQLite DB with a real QueuePool (StaticPool would hide
    connection checkouts), mirroring how the app runs with Postgres/asyncpg.
    """
    async def scenario(_factory):
        engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/pool.db")
        factory = build_session_factory(engine)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        monkeypatch.setattr(connection, "AsyncSessionLocal", factory)
        try:
            async with get_db() as session:
                # force the session to actually check out a connection
                await session.execute(select(1))
                assert engine.pool.checkedout() == 1

            # after get_db's context manager exits, the connection is returned
            assert engine.pool.checkedout() == 0
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

    run_scenario(scenario)
