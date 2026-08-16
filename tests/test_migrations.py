"""Tests for the Alembic schema workflow (alembic/, src/database/schema.py).

Covers the three database states ``ensure_schema`` must handle:

* fresh database  -> ``alembic upgrade head`` creates the full schema
* ``create_all``-era database -> adopted by ``stamp head`` (no DDL re-run)
* already migrated -> ``upgrade head`` is a no-op (idempotent)
"""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import inspect, select

from src.database.connection import build_engine, build_session_factory
from src.database.models import Base, Setting
from src.database.schema import APP_TABLES, ensure_schema

EXPECTED_TABLES = {"users", "conversations", "messages", "settings", "outage_reports"}


def _url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


def _table_names(db_path: Path) -> set:
    conn = sqlite3.connect(db_path)
    try:
        return {
            r[0]
            for r in conn.execute(
                "select name from sqlite_master where type='table'"
            )
        }
    finally:
        conn.close()


def _user_columns(db_path: Path) -> set:
    conn = sqlite3.connect(db_path)
    try:
        return {
            r[1]
            for r in conn.execute("PRAGMA table_info(users)")
        }
    finally:
        conn.close()


def test_fresh_database_upgrade_creates_full_schema(tmp_path):
    db_path = tmp_path / "fresh.db"
    ensure_schema(_url(db_path))

    tables = _table_names(db_path)
    assert EXPECTED_TABLES <= tables
    assert "alembic_version" in tables  # migration version recorded

    # Auth columns come from the initial migration (replaces ensure_user_columns).
    columns = _user_columns(db_path)
    assert {"email", "password_hash", "display_name"} <= columns


def test_upgrade_is_idempotent(tmp_path):
    db_path = tmp_path / "idem.db"
    ensure_schema(_url(db_path))
    ensure_schema(_url(db_path))  # second run must not fail or duplicate

    tables = _table_names(db_path)
    assert EXPECTED_TABLES <= tables
    assert "alembic_version" in tables


def test_create_all_era_database_is_adopted_by_stamp(tmp_path):
    """A pre-migration DB (tables via create_all, no alembic_version) is
    stamped head — NOT re-created — so existing data survives."""
    db_path = tmp_path / "legacy.db"

    async def create_legacy():
        engine = build_engine(_url(db_path))
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with build_session_factory(engine)() as session:
                session.add(Setting(key="map_refresh_seconds", value="60"))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(create_legacy())
    assert "alembic_version" not in _table_names(db_path)

    ensure_schema(_url(db_path))  # adopt: stamp, no DDL

    tables = _table_names(db_path)
    assert EXPECTED_TABLES <= tables
    assert "alembic_version" in tables

    # Pre-existing data survived the adoption.
    async def read_setting():
        engine = build_engine(_url(db_path))
        try:
            async with build_session_factory(engine)() as session:
                return await session.get(Setting, "map_refresh_seconds")
        finally:
            await engine.dispose()

    assert asyncio.run(read_setting()).value == "60"


def test_downgrade_base_drops_all_tables(tmp_path):
    db_path = tmp_path / "downgrade.db"
    ensure_schema(_url(db_path))
    assert EXPECTED_TABLES <= _table_names(db_path)

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", _url(db_path))
    cfg.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parent.parent / "alembic"),
    )
    command.downgrade(cfg, "base")

    assert not (_table_names(db_path) & EXPECTED_TABLES)
