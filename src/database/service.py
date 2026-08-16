"""Persistence helpers used by the FastAPI endpoints (src/api/main.py).

Keeps SQLAlchemy session logic out of the API layer so endpoints stay thin
and the queries are unit-testable against SQLite (see tests/test_database.py).

Registered users own their conversations and outage reports. Anonymous
sessions fall back to a stable "demo user" (fixed UUID) so the app remains
usable without an account.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    Conversation,
    Message,
    OutageReport,
    ReportStatus,
    Setting,
    User,
    UtilityType,
)

DEMO_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def get_or_create_demo_user(session: AsyncSession) -> User:
    """Return the demo user, creating it on first use."""
    user = await session.get(User, DEMO_USER_ID)
    if user is None:
        user = User(id=DEMO_USER_ID)
        session.add(user)
        await session.flush()
    return user


async def create_user(
    session: AsyncSession,
    email: str,
    password_hash: str,
    display_name: Optional[str] = None,
) -> User:
    """Create a registered user. Caller passes the *already-hashed* password.

    Emails are normalized (lowercased + trimmed) here at the data layer so
    uniqueness checks behave the same regardless of caller.
    """
    user = User(
        email=email.lower().strip(),
        password_hash=password_hash,
        display_name=display_name,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def get_user_by_email(session: AsyncSession, email: str) -> Optional[User]:
    result = await session.execute(
        select(User).where(User.email == email.lower().strip())
    )
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> Optional[User]:
    return await session.get(User, user_id)


async def get_conversation(session: AsyncSession, conversation_id: uuid.UUID) -> Optional[Conversation]:
    return await session.get(Conversation, conversation_id)


async def create_conversation(
    session: AsyncSession, user_id: uuid.UUID, title: Optional[str] = None
) -> Conversation:
    conversation = Conversation(user_id=user_id, title=title or "Nouvelle conversation")
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def list_conversations(session: AsyncSession, user_id: uuid.UUID) -> List[Conversation]:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
    )
    return list(result.scalars().all())


async def persist_chat_turn(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    user_query: str,
    answer: str,
    sources: Optional[List[dict]] = None,
) -> None:
    """Append a user question + assistant answer (with sources) to a conversation."""
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} does not exist")

    conversation.messages.append(Message(role="user", content=user_query))
    conversation.messages.append(
        Message(role="assistant", content=answer, sources=sources or None)
    )
    await session.commit()


async def list_outages(
    session: AsyncSession, status: Optional[ReportStatus] = None
) -> List[OutageReport]:
    query = select(OutageReport).order_by(OutageReport.created_at.desc())
    if status is not None:
        query = query.where(OutageReport.status == status)
    result = await session.execute(query)
    return list(result.scalars().all())


async def create_outage(
    session: AsyncSession,
    utility: UtilityType,
    region: str,
    latitude: float,
    longitude: float,
    description: Optional[str] = None,
    user_id: Optional[uuid.UUID] = None,
) -> OutageReport:
    report = OutageReport(
        user_id=user_id,
        utility=utility,
        region=region,
        latitude=latitude,
        longitude=longitude,
        description=description,
        status=ReportStatus.PENDING,
    )
    session.add(report)
    await session.commit()
    return report


async def update_outage_status(
    session: AsyncSession, outage_id: uuid.UUID, status: ReportStatus
) -> Optional[OutageReport]:
    report = await session.get(OutageReport, outage_id)
    if report is None:
        return None
    report.status = status
    await session.commit()
    return report


async def delete_expired_outages(
    session: AsyncSession, max_age: timedelta
) -> int:
    """Delete outage reports older than ``max_age``; returns the number removed.

    Used by the app's background cleanup task so crowdsourced reports do not
    accumulate forever (the outage map shows a rolling window of reports).
    """
    cutoff = datetime.now(timezone.utc) - max_age
    result = await session.execute(
        delete(OutageReport).where(OutageReport.created_at < cutoff)
    )
    await session.commit()
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# Runtime configuration (admin-editable, env- fallback)
# ---------------------------------------------------------------------------

# All editable settings, with their environment-variable default. Stored rows
# in the `settings` table override these at runtime; unset keys fall back here.
DEFAULT_SETTINGS: dict[str, str] = {
    "outage_ttl_hours": os.getenv("OUTAGE_TTL_HOURS", "5"),
    "outage_purge_interval_minutes": os.getenv(
        "OUTAGE_PURGE_INTERVAL_MINUTES", "30"
    ),
    "map_refresh_seconds": os.getenv("MAP_REFRESH_SECONDS", "60"),
}


def setting_default(key: str) -> Optional[str]:
    """Environment fallback for a setting key (None for unknown keys)."""
    return DEFAULT_SETTINGS.get(key)


async def get_all_settings(session: AsyncSession) -> dict[str, str]:
    """Merge DB-stored settings over env defaults (DB wins)."""
    result = await session.execute(select(Setting))
    stored = {row.key: row.value for row in result.scalars().all()}
    merged = dict(DEFAULT_SETTINGS)
    merged.update(stored)
    return merged


async def get_setting(session: AsyncSession, key: str) -> Optional[str]:
    """Return a single setting value (DB row, falling back to env default)."""
    row = await session.get(Setting, key)
    if row is not None:
        return row.value
    return DEFAULT_SETTINGS.get(key)


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    """Upsert a runtime setting. Only known keys are accepted by callers."""
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    await session.commit()
