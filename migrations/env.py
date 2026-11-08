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
from ensemble.vector_store import vector_metadata
from ensemble.index_store import index_metadata
from ensemble.knowledge_store import knowledge_metadata
from ensemble.memory_store import memory_metadata\nfrom ensemble.ledger import ledger_metadata
from ensemble.ga_store import ga_metadata
from ensemble.thompson_store import thompson_metadata
from ensemble.learning_store import learning_metadata
from ensemble.transcript_store import transcript_metadata
from ensemble.config import resolve_database_url

config = context.config
configured_url = config.attributes.get("database_url") or os.getenv("ENSEMBLE_DATABASE_URL")
ini_url = config.get_main_option("sqlalchemy.url")
if configured_url is None and ini_url != "sqlite+aiosqlite:///./.ensemble/ensemble.db":
    configured_url = ini_url
resolved_url = resolve_database_url(configured_url)
config.set_main_option("sqlalchemy.url", resolved_url.replace("%", "%%"))

database_url = make_url(resolved_url)
if database_url.get_backend_name() != "sqlite":
    raise RuntimeError("Ensemble migrations support SQLite only; alternative persistence implementations belong to FlossWare proper.")
if database_url.database not in (None, "", ":memory:"):
    Path(database_url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = [metadata, vector_metadata, index_metadata, knowledge_metadata, memory_metadata, ga_metadata, thompson_metadata, learning_metadata, transcript_metadata, ledger_metadata]


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
