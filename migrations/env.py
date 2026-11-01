from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

from ensemble.store import metadata

config = context.config
environment_url = os.getenv("ENSEMBLE_DATABASE_URL")
if environment_url:
    config.set_main_option("sqlalchemy.url", environment_url.replace("%", "%%"))

database_url = make_url(config.get_main_option("sqlalchemy.url"))
if database_url.get_backend_name() == "sqlite" and database_url.database not in (None, "", ":memory:"):
    Path(database_url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_sync_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(run_sync_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
