import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Resolve .env from the project root regardless of the working directory
# (same convention as src/ingestion/collector.py and src/utils/triage.py).
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(dotenv_path=str(PROJECT_ROOT / ".env"))

# Format: postgresql+asyncpg://user:password@host:port/dbname
DEFAULT_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/energie_tunisie"


def build_engine(database_url: str | None = None, **kwargs) -> AsyncEngine:
    """Create an async SQLAlchemy engine.

    An explicit ``database_url`` wins; otherwise fall back to the ``DATABASE_URL``
    environment variable, then to ``DEFAULT_DATABASE_URL``. Extra keyword
    arguments (``pool_size``, ``max_overflow``, ``echo``, ...) are forwarded to
    ``create_async_engine`` so callers -- and tests, which can point at a local
    ``sqlite+aiosqlite`` database -- can tune the engine without touching the
    production defaults.
    """
    url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    return create_async_engine(url, **kwargs)


def build_session_factory(engine: AsyncEngine) -> "async_sessionmaker[AsyncSession]":
    """Create an async session factory bound to the given engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


engine = build_engine(
    echo=False,  # Set to True for SQL query debugging in development
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = build_session_factory(engine)


# Session dependency for direct use (async with / tests)
# asynccontextmanager guarantees the session (and its pooled connection) is
# released even if the surrounding code raises.
@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


# FastAPI Dependency for Session Injection
# FastAPI's Depends() expects a plain async generator (an asynccontextmanager
# is not auto-detected), so wrap get_db in a thin async generator.
async def get_db_dependency() -> AsyncGenerator[AsyncSession, None]:
    async with get_db() as session:
        yield session