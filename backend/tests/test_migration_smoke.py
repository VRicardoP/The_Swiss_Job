"""Smoke test de migraciones: modelo <-> migracion SIN divergencia (A.SEAM).

Aplica la cadena REAL de Alembic (subprocess, mismo patron que
jobhunt_core/tests/alembic_runner.py) sobre una BD desechable y compara el
esquema resultante de las tablas de la costura con el que produce
`Base.metadata.create_all` del modelo: columnas, PK (nombre incluido),
check constraints e indices.

Alcance deliberado — SOLO las tablas de A.SEAM (`jobhunt_routing` de
b7d1a5c9e402 y `jobhunt_profile_map` de c81f4d2e9a57): el resto del esquema
legacy tiene divergencias conocidas y deliberadas fuera del ORM (columna
tsvector `search_vector` + trigger creados por migracion/conftest, indice
HNSW de pgvector), asi que una comparacion global daria ruido, no señal.
"""

import os
import subprocess
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from config import settings
from database import Base

# Directorio del backend (en el contenedor: /app) — alembic.ini vive aqui.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ALEMBIC_INI = str(_BACKEND_DIR / "alembic.ini")

# BD desechable, hermana de la de tests (mismo servidor que DATABASE_URL).
_BASE_URL = settings.DATABASE_URL.rsplit("/", 1)[0]
_SMOKE_DB = "swissjobhunter_migration_smoke"
_SMOKE_URL = f"{_BASE_URL}/{_SMOKE_DB}"

# tabla de costura -> nombre de PK exigido por el plan/modelo
SEAM_TABLES = {
    "jobhunt_routing": "pk_jobhunt_routing",
    "jobhunt_profile_map": "pk_jobhunt_profile_map",
}


def run_alembic(db_url: str, *args: str) -> subprocess.CompletedProcess:
    """`alembic -c <ini> <args>` contra `db_url` (DATABASE_URL inyectada).

    env.py sobrescribe sqlalchemy.url con settings.DATABASE_URL y pydantic
    da prioridad a la env var sobre el .env => el subprocess migra la BD
    desechable, nunca la de la app.
    """
    env = {**os.environ, "DATABASE_URL": db_url}
    return subprocess.run(
        ["alembic", "-c", _ALEMBIC_INI, *args],
        check=False,
        capture_output=True,
        cwd=str(_BACKEND_DIR),  # script_location del ini es relativo
        env=env,
    )


async def _recreate_smoke_db() -> None:
    """(Re)crea la BD desechable vacia con la extension pgvector."""
    admin = create_async_engine(
        _BASE_URL + "/swissjobhunter", isolation_level="AUTOCOMMIT"
    )
    async with admin.connect() as conn:
        await conn.execute(text(f"DROP DATABASE IF EXISTS {_SMOKE_DB} WITH (FORCE)"))
        await conn.execute(text(f"CREATE DATABASE {_SMOKE_DB}"))
    await admin.dispose()

    smoke = create_async_engine(_SMOKE_URL, isolation_level="AUTOCOMMIT")
    async with smoke.connect() as conn:
        # La cadena crea columnas VECTOR e indice HNSW: extension requerida.
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    await smoke.dispose()


def _snapshot(sync_conn) -> dict:
    """Esquema observable de las tablas de costura: columnas + PK + checks +
    indices, por tabla."""
    insp = inspect(sync_conn)
    return {
        table: {
            "columns": {
                c["name"]: {
                    "type": repr(c["type"]),
                    "nullable": c["nullable"],
                    "server_default": c.get("default"),
                }
                for c in insp.get_columns(table)
            },
            "pk": {
                "name": insp.get_pk_constraint(table)["name"],
                "columns": insp.get_pk_constraint(table)["constrained_columns"],
            },
            "checks": {
                ck["name"]: ck["sqltext"] for ck in insp.get_check_constraints(table)
            },
            "indexes": {
                ix["name"]: {"columns": ix["column_names"], "unique": ix["unique"]}
                for ix in insp.get_indexes(table)
            },
        }
        for table in SEAM_TABLES
    }


async def _reflect_snapshot() -> dict:
    engine = create_async_engine(_SMOKE_URL)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(_snapshot)
    finally:
        await engine.dispose()


async def test_seam_tables_schema_migration_equals_model():
    """La migracion real y create_all del modelo producen el MISMO esquema."""
    # Fase 1: cadena Alembic completa sobre BD vacia.
    await _recreate_smoke_db()
    result = run_alembic(_SMOKE_URL, "upgrade", "head")
    assert result.returncode == 0, (
        f"alembic upgrade head fallo:\n{result.stderr.decode()}\n"
        f"{result.stdout.decode()}"
    )
    migrated = await _reflect_snapshot()

    # Fase 2: misma BD vacia, esquema del ORM (create_all del modelo).
    await _recreate_smoke_db()
    engine = create_async_engine(_SMOKE_URL)
    try:
        import models  # noqa: F401 — registra la metadata en Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()
    from_model = await _reflect_snapshot()

    # Las PK deben llevar el nombre del plan en AMBOS esquemas (era la
    # divergencia original: create_all generaba '<tabla>_pkey').
    for table, pk_name in SEAM_TABLES.items():
        assert migrated[table]["pk"]["name"] == pk_name, table
        assert from_model[table]["pk"]["name"] == pk_name, table
    assert migrated == from_model
