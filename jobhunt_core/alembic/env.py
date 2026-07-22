"""Entorno Alembic INDEPENDIENTE del core (A-01, ADR-08 / §15bis).

- version table PROPIA y dentro del esquema del core (jobhunt.alembic_version):
  no toca ni conoce la del backend legacy (public.alembic_version).
- conecta con la URL del core (rol de mínimo privilegio) en driver sync.
"""

import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# Garantiza que el paquete es importable aunque alembic se invoque directamente.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobhunt_core.config import settings  # noqa: E402
from jobhunt_core.database import Base  # noqa: E402

target_metadata = Base.metadata


def _sync_url() -> str:
    # Alembic corre en sync: asyncpg -> psycopg2.
    return settings.CORE_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        version_table="alembic_version",
        version_table_schema=settings.CORE_DB_SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_sync_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version",
            version_table_schema=settings.CORE_DB_SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
