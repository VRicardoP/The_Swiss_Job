"""Entorno Alembic INDEPENDIENTE del core (A-01, ADR-08 / §15bis).

- version table PROPIA y dentro del esquema del core (jobhunt.alembic_version):
  no toca ni conoce la del backend legacy (public.alembic_version).
- conecta con la URL del core (rol de mínimo privilegio) en driver sync.

REGLA (revisión A-02): una revisión APLICADA en cualquier entorno es INMUTABLE —
Alembic solo registra el ID, no un checksum, así que editarla diverge en
silencio. Toda corrección de esquema = NUEVA revisión (coreNNNN). Editar una
revisión solo es admisible si JAMÁS se aplicó fuera de la rama privada, y
entonces se recrean explícitamente las BD que la ejecutaron.
"""

import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# Garantiza que el paquete es importable aunque alembic se invoque directamente.
# Condicional (O-2, 2026-08-27): con Alembic EN PROCESO este env.py se ejecuta
# una vez por invocación, y el insert incondicional acumulaba una entrada
# duplicada en sys.path por cada BD desechable de la suite.
_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from jobhunt_core.config import settings  # noqa: E402
from jobhunt_core.database import Base  # noqa: E402

target_metadata = Base.metadata


def _sync_url() -> str:
    # Alembic corre en sync: asyncpg -> psycopg2.
    #
    # O-2 (2026-08-27): la URL puede venir INYECTADA por quien invoca Alembic
    # EN PROCESO (`Config.attributes["core_url"]` — tests/alembic_runner.py).
    # Por subproceso bastaba el entorno, porque `settings` nacía de cero en
    # cada intérprete; en proceso `settings` es un singleton ya construido y
    # apuntando a OTRA base (la de sesión de conftest), así que sin esta
    # costura la migración iría a parar a la base equivocada. La costura es
    # explícita y por invocación: no hay estado global que reapuntar.
    url = context.config.attributes.get("core_url") or settings.CORE_DATABASE_URL
    return url.replace("postgresql+asyncpg://", "postgresql://")


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
