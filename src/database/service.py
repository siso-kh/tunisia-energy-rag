"""Persistence helpers used by the FastAPI endpoints (src/api/main.py).

Keeps SQLAlchemy session logic out of the API layer so endpoints stay thin
and the queries are unit-testable against SQLite (see tests/test_database.py).

There is no auth in the app yet, so a stable "demo user" (fixed UUID) owns
conversations until real user management lands.
"""

import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    Conversation,
    Message,
    OutageReport,
    ReportStatus,
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
