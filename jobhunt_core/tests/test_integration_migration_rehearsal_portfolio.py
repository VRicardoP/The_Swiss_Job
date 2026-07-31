"""Ensayo LOCAL de la migración de durables del portfolio (C-4, parte 3).

Arnés de reconciliación del cutover del piloto: orquesta la migración completa
(partes 1+2 vía import_portfolio_migrate) sobre una BD DESECHABLE poblada con un
dataset REPRESENTATIVO y verifica las propiedades que el ensayo REAL (sobre copia
del NAS, gated) exigirá:

- ORDEN y encadenado por usuario (provision→síntesis→mapeo).
- VACANTE COMPARTIDA entre usuarios: una URL común resuelve a UNA vacante-sombra,
  con una application por perfil (UNIQUE(profile_id, vacancy_id) intacto).
- DRY-RUN REVERSIBLE: migrar, ver checksums NO vacíos, y ROLLBACK ⇒ nada persiste
  (el origen jamás se muta; la migración es todo-o-nada del llamador).
- RECONCILIACIÓN: conteos agregados + count/checksum por tabla; los checksums son
  != md5('') (DoD: "checksums de cero = confianza falsa").
- IDEMPOTENCIA a nivel de DATOS: re-migrar el mismo origen NO cambia los checksums
  (sin duplicar, sin divergir), aunque saved_searches desplace migrated→existing.

El ensayo REAL sobre los datos del NAS queda GATED (mismo criterio que las demás
validaciones NAS). Ejecutar vía core-migrate.
"""

import asyncio
import os
import uuid
from datetime import date, datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core.config import settings
from jobhunt_core.tests.alembic_runner import run_alembic

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)

U_APPLIED = "https://jobs.example.ch/u1-applied"
U_SAVED = "https://jobs.example.ch/u1-saved"
U_SAVED_FU = "https://jobs.example.ch/u1-saved-fu"
U_BOTH = "https://jobs.example.ch/u1-both"
U_SHARED = "https://jobs.example.ch/shared"
U_U2_SAVED = "https://jobs.example.ch/u2-saved"


def _representative_users() -> list[dict]:
    """Dataset REPRESENTATIVO: 2 usuarios que ejercen todas las ramas (applied,
    saved, saved+follow_up, consolidación, sin url, URL compartida) + búsquedas
    (válida, filters inválido, homónimas con filtros distintos)."""
    return [
        {
            "external_ref": 1,
            "applications": [
                {"url": U_APPLIED, "status": "applied", "title": "Backend Dev",
                 "company": "ACME", "description": "py", "notes": "enviado",
                 "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
                {"url": U_SAVED, "status": "saved", "title": "Data Eng",
                 "company": "Beta", "notes": "mirar",
                 "created_at": datetime(2026, 6, 2, tzinfo=timezone.utc)},
                {"url": U_SAVED_FU, "status": "saved", "title": "ML Eng",
                 "company": "Gamma", "follow_up_date": date(2026, 9, 1),
                 "created_at": datetime(2026, 6, 3, tzinfo=timezone.utc)},
                # Consolidación: saved+fu y applied a la MISMA url → 1 application.
                {"url": U_BOTH, "status": "saved", "title": "DevOps",
                 "company": "Delta", "follow_up_date": date(2026, 9, 5),
                 "created_at": datetime(2026, 6, 4, tzinfo=timezone.utc)},
                {"url": U_BOTH, "status": "applied", "title": "DevOps",
                 "company": "Delta", "description": "IaC",
                 "created_at": datetime(2026, 6, 5, tzinfo=timezone.utc)},
                # Sin url → unresolved (no persiste, se audita).
                {"url": None, "status": "offer", "title": "Sin URL",
                 "created_at": datetime(2026, 6, 6, tzinfo=timezone.utc)},
                # URL compartida con el usuario 2.
                {"url": U_SHARED, "status": "applied", "title": "Shared Role",
                 "company": "Omega",
                 "created_at": datetime(2026, 6, 7, tzinfo=timezone.utc)},
            ],
            "saved_searches": [
                {"name": "python", "filters": '{"q": "python"}', "min_score": 60,
                 "is_active": True, "last_notified_at": None},
                {"name": "broken", "filters": "{no json", "min_score": 0,
                 "is_active": False, "last_notified_at": None},
                # Homónimas con filtros distintos = DOS búsquedas legítimas.
                {"name": "zurich", "filters": '{"q": "a"}', "min_score": 0,
                 "is_active": True, "last_notified_at": None},
                {"name": "zurich", "filters": '{"q": "b"}', "min_score": 0,
                 "is_active": True, "last_notified_at": None},
            ],
        },
        {
            "external_ref": 2,
            "applications": [
                # MISMA url que el usuario 1 → misma vacante, application propia.
                {"url": U_SHARED, "status": "rejected", "title": "Shared Role",
                 "company": "Omega",
                 "created_at": datetime(2026, 6, 8, tzinfo=timezone.utc)},
                {"url": U_U2_SAVED, "status": "saved", "title": "Frontend",
                 "company": "Sigma",
                 "created_at": datetime(2026, 6, 9, tzinfo=timezone.utc)},
            ],
            "saved_searches": [
                {"name": "rust", "filters": '{"q": "rust"}', "min_score": 0,
                 "is_active": True, "last_notified_at": None},
            ],
        },
    ]


# Conteos agregados esperados (clasificación de las partes 1+2).
EXPECTED_APPS = {
    "applications": 5, "bookmarks": 4, "unresolved": 1,
    "consolidated": 1, "invalid_status": 0,
}
EXPECTED_SS = {"migrated": 5, "existing": 0, "invalid_filters": 1, "no_name": 0}
# Filas resultantes por tabla de tracking.
EXPECTED_ROWS = {
    "applications": 5, "application_status_events": 5,
    "profile_vacancy_state": 4, "saved_searches": 5,
}


def test_migration_rehearsal_portfolio_local():
    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    dbname = f"jobhunt_rehearsal_pf_{uuid.uuid4().hex[:12]}"
    parts = urlsplit(admin_url)
    temp_url = urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))
    admin_engine = create_async_engine(
        admin_url, poolclass=sa.pool.NullPool, isolation_level="AUTOCOMMIT"
    )

    async def create_db():
        async with admin_engine.connect() as c:
            await c.execute(sa.text(f'CREATE DATABASE "{dbname}"'))

    asyncio.run(create_db())
    try:
        temp_engine = create_async_engine(
            temp_url, poolclass=sa.pool.NullPool,
            connect_args={
                "server_settings": {
                    "search_path": f"{settings.CORE_DB_SCHEMA}, public"
                }
            },
        )
        factory = async_sessionmaker(temp_engine, expire_on_commit=False)

        async def bootstrap():
            async with temp_engine.begin() as c:
                await c.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
                await c.execute(
                    sa.text(f'CREATE SCHEMA IF NOT EXISTS "{settings.CORE_DB_SCHEMA}"')
                )

        asyncio.run(bootstrap())
        run_alembic(temp_url, "upgrade", "head")
        asyncio.run(_scenario(factory))
        asyncio.run(temp_engine.dispose())
    finally:

        async def drop_db():
            async with admin_engine.connect() as c:
                await c.execute(
                    sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
                )
            await admin_engine.dispose()

        asyncio.run(drop_db())


async def _scenario(factory):
    from jobhunt_core import import_portfolio as ip
    from jobhunt_core import import_portfolio_migrate as ipm

    users = _representative_users()

    # --- DRY-RUN REVERSIBLE: migrar en una sesión, verificar checksums NO vacíos
    # (la migración corrió contra datos reales), y ROLLBACK ⇒ nada persiste.
    async with factory() as s:
        await ipm.migrate_portfolio(s, users)
        chk_dry = await ipm.table_checksums(s)
        assert all(t["count"] > 0 for t in chk_dry.values()), chk_dry
        assert all(
            t["checksum"] != ipm.EMPTY_CHECKSUM for t in chk_dry.values()
        ), chk_dry
        await s.rollback()
    async with factory() as s:
        for table in ipm.CORE_TRACKING_TABLES:
            assert await _count(s, table) == 0  # rollback ⇒ migración descartada

    # --- CUTOVER real: migrar + commit + reconciliar.
    async with factory() as s:
        report = await ipm.migrate_portfolio(s, users)
        await s.commit()
        assert report["users"] == 2
        assert report["applications"] == EXPECTED_APPS, report["applications"]
        assert report["saved_searches"] == EXPECTED_SS, report["saved_searches"]

        chk1 = await ipm.table_checksums(s)
        for table, rows in EXPECTED_ROWS.items():
            assert chk1[table]["count"] == rows, (table, chk1[table])
            assert chk1[table]["checksum"] != ipm.EMPTY_CHECKSUM, table

        # VACANTE COMPARTIDA: la URL común resuelve a UNA vacante, con DOS
        # applications (una por perfil) — UNIQUE(profile_id, vacancy_id) intacto.
        vid_shared = await ip.resolve_vacancy_by_url(s, U_SHARED)
        assert vid_shared is not None
        n_shared = (
            await s.execute(
                sa.text("SELECT count(*) FROM applications WHERE vacancy_id = :v"),
                {"v": vid_shared},
            )
        ).scalar_one()
        assert n_shared == 2
        # Solo se sintetizó UNA vacante por URL distinta (6 URLs → 6 vacantes).
        assert await _count(s, "vacancies") == 6

    # --- IDEMPOTENCIA a nivel de DATOS: re-migrar NO cambia los checksums ni las
    # filas (sin duplicar, sin divergir); applications mantiene su clasificación,
    # saved_searches desplaza migrated→existing (el resultado es el mismo).
    async with factory() as s:
        report2 = await ipm.migrate_portfolio(s, users)
        await s.commit()
        assert report2["applications"] == EXPECTED_APPS
        assert report2["saved_searches"] == {
            "migrated": 0, "existing": 5, "invalid_filters": 1, "no_name": 0,
        }
        chk2 = await ipm.table_checksums(s)
        assert chk2 == chk1  # MISMOS conteos Y checksums: idempotente de verdad


async def _count(session, table: str) -> int:
    return (
        await session.execute(sa.text(f"SELECT count(*) FROM {table}"))
    ).scalar_one()
