import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from config import settings
from database import Base
import models  # noqa: F401 — registers model metadata on Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Sobrescribe el sqlalchemy.url del alembic.ini con el de settings (pydantic).
# Así Alembic comparte la misma fuente de verdad que la app (env vars > .env)
# y NO depende del valor hard-coded de desarrollo del alembic.ini.
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata


def _include_object(_object, name, type_, reflected, compare_to):
    # These local runtimes were retired after Core became authoritative, but
    # their migrations remain part of the immutable schema history. Preserve
    # the orphaned tables until a dedicated data-retention migration removes
    # them; otherwise ``alembic check`` proposes destructive drops merely
    # because their application models no longer exist.
    retired_runtime_tables = {
        "jobhunt_routing",
        "source_compliance",
        "source_cursors",
        "source_health",
    }
    return not (
        type_ == "table"
        and reflected
        and compare_to is None
        and name in retired_runtime_tables
    )


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
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
