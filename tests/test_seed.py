"""Tests for the database seed script (src/database/seed.py).

Each test seeds into its own SQLite file in a tmp dir, then re-opens it with
a fresh engine to verify what was persisted. Uses the project's asyncio.run
pattern (no pytest-asyncio).
"""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import func, select

from src.database.connection import build_engine, build_session_factory
from src.database.models import (
    Conversation,
    Message,
    OutageReport,
    ReportStatus,
    User,
    UtilityType,
)
from src.database.seed import _CONVERSATIONS, _OUTAGE_REPORTS, seed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


def _run_seed(db_path: Path, **kwargs):
    return asyncio.run(seed(database_url=_url(db_path), verbose=False, **kwargs))


def _run_scenario(db_path: Path, scenario):
    """Run an async scenario against the seeded file DB with a fresh session."""
    async def wrapper():
        engine = build_engine(_url(db_path))
        factory = build_session_factory(engine)
        try:
            async with factory() as session:
                return await scenario(session)
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


async def _count(session, model):
    return await session.scalar(select(func.count()).select_from(model))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_seed_populates_all_tables(tmp_path):
    db_path = tmp_path / "seed.db"
    summary = _run_seed(db_path)

    assert summary["users"] == 3
    assert summary["conversations"] == len(_CONVERSATIONS)
    assert summary["messages"] == sum(len(c["messages"]) for c in _CONVERSATIONS)
    assert summary["outage_reports"] == len(_OUTAGE_REPORTS)

    async def check(session):
        assert await _count(session, User) == 3
        assert await _count(session, Conversation) == len(_CONVERSATIONS)
        assert await _count(session, Message) == summary["messages"]
        assert await _count(session, OutageReport) == len(_OUTAGE_REPORTS)

    _run_scenario(db_path, check)


def test_seed_is_idempotent(tmp_path):
    db_path = tmp_path / "seed.db"
    first = _run_seed(db_path)
    second = _run_seed(db_path)  # should skip

    assert second["users"] == 0  # nothing inserted on second run

    async def check(session):
        # counts still match the first run exactly (no duplication)
        assert await _count(session, User) == first["users"]
        assert await _count(session, Conversation) == first["conversations"]
        assert await _count(session, Message) == first["messages"]
        assert await _count(session, OutageReport) == first["outage_reports"]

    _run_scenario(db_path, check)


def test_seed_reset_reruns_cleanly(tmp_path):
    db_path = tmp_path / "seed.db"
    _run_seed(db_path)
    _run_seed(db_path)  # skip
    again = _run_seed(db_path, reset=True)  # drop + reseed

    assert again["users"] == 3
    assert again["conversations"] == len(_CONVERSATIONS)
    assert again["messages"] == sum(len(c["messages"]) for c in _CONVERSATIONS)
    assert again["outage_reports"] == len(_OUTAGE_REPORTS)

    async def check(session):
        # reset must not leave duplicates behind
        assert await _count(session, User) == 3
        assert await _count(session, OutageReport) == len(_OUTAGE_REPORTS)

    _run_scenario(db_path, check)


def test_seed_conversation_messages_and_sources(tmp_path):
    db_path = tmp_path / "seed.db"
    _run_seed(db_path)

    async def check(session):
        result = await session.execute(
            select(Conversation).where(Conversation.title == "Énergies renouvelables en Tunisie")
        )
        conv = result.scalar_one()

        # role alternation and order preserved (order_by created_at)
        roles = [m.role for m in conv.messages]
        assert roles == ["user", "assistant", "user", "assistant"]

        # assistant answers carry RAG sources JSON, user questions do not
        assistant_msgs = [m for m in conv.messages if m.role == "assistant"]
        user_msgs = [m for m in conv.messages if m.role == "user"]
        assert all(m.sources for m in assistant_msgs)
        assert all(m.sources[0]["source_file"].endswith(".pdf") for m in assistant_msgs)
        assert all(m.sources is None for m in user_msgs)

        # messages are linked back to the conversation
        assert assistant_msgs[0].conversation.id == conv.id

    _run_scenario(db_path, check)


def test_seed_outage_reports_have_real_coords_and_enums(tmp_path):
    db_path = tmp_path / "seed.db"
    _run_seed(db_path)

    async def check(session):
        reports = (await session.execute(select(OutageReport))).scalars().all()
        assert len(reports) == len(_OUTAGE_REPORTS)

        for report in reports:
            # valid enum values
            assert report.utility in (UtilityType.STEG, UtilityType.SONEDE, UtilityType.OTHER)
            assert report.status in (ReportStatus.PENDING, ReportStatus.VERIFIED, ReportStatus.RESOLVED)
            # plausible Tunisian coordinates
            assert 30.0 <= report.latitude <= 38.0
            assert 7.0 <= report.longitude <= 12.0

        # some reports are anonymous (user_id NULL), some belong to a seeded user
        assert sum(r.user_id is not None for r in reports) > 0
        assert sum(r.user_id is None for r in reports) > 0

        # both utilities and all statuses appear in the dataset
        assert {r.utility for r in reports} == {UtilityType.STEG, UtilityType.SONEDE, UtilityType.OTHER}
        assert {r.status for r in reports} == {
            ReportStatus.PENDING,
            ReportStatus.VERIFIED,
            ReportStatus.RESOLVED,
        }

    _run_scenario(db_path, check)


def test_seed_with_custom_user_count(tmp_path):
    db_path = tmp_path / "seed.db"
    summary = _run_seed(db_path, num_users=5)
    assert summary["users"] == 5

    async def check(session):
        assert await _count(session, User) == 5

    _run_scenario(db_path, check)


def test_seed_cli_help_runs():
    """The module must expose a working --help entry point (argparse)."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "src.database.seed", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "--reset" in result.stdout
    assert "--url" in result.stdout
    assert "--users" in result.stdout


def test_seed_without_reset_does_not_create_duplicate_users(tmp_path):
    """A second run with reset=False must not touch existing rows at all."""
    db_path = tmp_path / "seed.db"
    _run_seed(db_path)

    async def read_ids(session):
        return (await session.execute(select(User.id))).scalars().all()

    ids_before = _run_scenario(db_path, read_ids)
    _run_seed(db_path)
    ids_after = _run_scenario(db_path, read_ids)

    assert ids_before == ids_after
