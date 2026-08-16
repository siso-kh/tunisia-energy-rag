"""Alembic environment — async engine, same URL resolution as the app.

The target URL is resolved in this priority order:
1. ``alembic -x url=...`` / ``Config.set_main_option("sqlalchemy.url", ...)``
   (used by ``src.database.schema.ensure_schema`` so the seeder can migrate
   whatever database it is pointed at)
2. the ``DATABASE_URL`` environment variable
3. the local Postgres default (same as ``src.database.connection``)
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from src.database.models import Base

# Import the models so their metadata (tables, enums, indexes) is registered
# on Base.metadata before autogenerate/upgrade runs.
from src.database import models as _models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://postgres:postgres@localhost:5432/energie_tunisie"
)


def _resolve_url() -> str:
    # Priority: programmatic (ensure_schema) -> `alembic -x url=...` -> env -> default
    explicit = config.get_main_option("sqlalchemy.url")
    if explicit:
        return explicit
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("url"):
        return x_args["url"]
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection (used for schema.sql)."""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": _resolve_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
