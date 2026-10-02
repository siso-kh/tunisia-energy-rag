"""Schema management via Alembic migrations — the single source of truth.

Replaces the old ad-hoc mechanisms:

* ``Base.metadata.create_all`` inside the seeder (could never alter an
  existing table),
* ``ensure_user_columns`` (a hand-rolled ``ALTER TABLE`` compat hack).

``ensure_schema`` applies pending migrations and is adopt-aware: a database
that was previously created by ``create_all`` (app tables exist but no
``alembic_version`` table) is *stamped* as already migrated instead of being
re-created. Both paths are idempotent.
"""

import os
import re
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(dotenv_path=str(PROJECT_ROOT / ".env"))

ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
ALEMBIC_DIR = PROJECT_ROOT / "alembic"

DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://postgres:postgres@localhost:5432/energie_tunisie"
)

# Every table the app owns (from src/database/models.py).
APP_TABLES = {"users", "conversations", "messages", "settings", "outage_reports"}


def _sync_url(url: str) -> str:
    """Convert an async driver URL to its sync driver for inspection."""
    # Strip the async driver segment (e.g. +asyncpg, +aiosqlite).
    url = url.replace("+asyncpg", "").replace("+aiosqlite", "")
    # Force the sync psycopg2 dialect. PSYCOPG/PSYCOPG2 INCOMPATIBILITY:
    # The app's DATABASE_URL is a POSTGRESQL+ASYNCPG URL, and psycopg2 is the
    # ONLY sync driver available (psycopg is async-only). When the URL keeps
    # '+asyncpg', SQLAlchemy resolves the dialect as 'postgresql+asyncpg' and
    # tries to import the async 'psycopg' module (which isn't in the image)
    # -> 'No module named psycopg'. Force the dialect to psycopg2 so the sync
    # engine uses psycopg2-binary (already in requirements.runtime.txt).
    url = url.replace("postgresql+asyncpg", "postgresql+psycopg2")
    # Remove the asyncpg-only query param (asyncpg accepts only 'ssl=require');
    # psycopg2 needs 'sslmode'. PRESERVE the DB name and credentials.
    url = re.sub(r"[?&]ssl(?:mode)?=require", "sslmode=require", url)
    return url


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", database_url)
    # Resolve the script location from this file so ensure_schema works
    # regardless of the caller's working directory.
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    return cfg


def ensure_schema(database_url: Optional[str] = None) -> None:
    """Apply migrations to the target database (idempotent, adopt-aware).

    * Fresh database (no tables)        -> ``alembic upgrade head``
    * ``create_all``-era database       -> ``alembic stamp head`` (existing
      schema adopted as migrated, no DDL re-run)
    * Already migrated                  -> ``alembic upgrade head`` is a no-op
    """
    url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)

    engine = create_engine(_sync_url(url))
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    cfg = _alembic_config(url)
    if tables & APP_TABLES and "alembic_version" not in tables:
        command.stamp(cfg, "head")
    else:
        command.upgrade(cfg, "head")
