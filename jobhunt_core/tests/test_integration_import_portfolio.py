"""Integración de la síntesis de vacantes-sombra del portfolio (C-4, parte 1).

BD DESECHABLE (patrón test_integration_migration_rehearsal): CREATE DATABASE +
extensión vector + esquema + alembic head, DROP en finally. Cubre síntesis,
resolución por URL, idempotencia de re-importación y del alta del scope, y la
omisión de items sin url. Ejecutar vía core-migrate.
"""

import asyncio
import os
import uuid
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


def test_synthesize_and_resolve_shadow_vacancies():
    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    dbname = f"jobhunt_import_{uuid.uuid4().hex[:12]}"
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
            # search_path por CONEXIÓN (NullPool renueva la conexión tras cada
            # commit y un SET suelto se perdería).
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

    with_url = [
        {
            "url": "https://jobs.example.ch/dev-123",
            "title": "Backend Developer",
            "company": "ACME AG",
            "description": "Python backend",
        },
        {
            # Barra final + host en mayúsculas: la resolución pasa por
            # normalize_url y debe colapsar a la misma clave.
            "url": "https://Portal.example.ch/ofertas/456/",
            "title": "Data Engineer",
            "company": None,
            "description": None,
        },
    ]
    no_url = {"url": None, "title": "Sin URL", "company": "X", "description": "d"}

    async with factory() as s:
        scope_id = await ip.ensure_import_scope(s)
        await s.commit()

        # --- Síntesis: 2 con url crean vacante; el que no tiene url se omite.
        await ip.synthesize_vacancies(s, scope_id, with_url + [no_url])
        await s.commit()
        assert await _count(s, "vacancies") == 2

        resolved: dict[str, uuid.UUID] = {}
        for item in with_url:
            vid = await ip.resolve_vacancy_by_url(s, item["url"])
            assert vid is not None, item["url"]
            resolved[item["url"]] = vid
            # Canónica VIGENTE con el title del durable en el content.
            title = (
                await s.execute(
                    sa.text(
                        "SELECT o.content->>'title' FROM vacancies v "
                        "JOIN offer_revisions o "
                        "  ON o.id = v.current_offer_revision_id "
                        "WHERE v.id = :v"
                    ),
                    {"v": vid},
                )
            ).scalar_one()
            assert title == item["title"]
        # Dos URLs distintas → dos vacantes distintas.
        assert len(set(resolved.values())) == 2

        # --- IDEMPOTENCIA de la síntesis: mismas urls → ni una vacante más,
        # y cada URL sigue resolviendo a la MISMA vacante.
        await ip.synthesize_vacancies(s, scope_id, with_url + [no_url])
        await s.commit()
        assert await _count(s, "vacancies") == 2
        for url, vid in resolved.items():
            assert await ip.resolve_vacancy_by_url(s, url) == vid

        # --- IDEMPOTENCIA del alta: mismo scope_id, una sola source y scope.
        assert await ip.ensure_import_scope(s) == scope_id
        await s.commit()
        assert (
            await s.execute(
                sa.text("SELECT count(*) FROM sources WHERE name = :n"),
                {"n": ip.PORTFOLIO_IMPORT_SOURCE},
            )
        ).scalar_one() == 1
        assert await _count(s, "harvest_scopes") == 1

        # --- Solo items sin url: no revienta y no crea nada.
        await ip.synthesize_vacancies(s, scope_id, [no_url])
        await s.commit()
        assert await _count(s, "vacancies") == 2

        # --- URL desconocida: None.
        assert await ip.resolve_vacancy_by_url(s, "https://nunca.vista/x") is None

        # --- URL MALFORMADA (P1 análisis 1): CUARENTENA por-item — no aborta el
        # lote válido; el durable bueno del mismo lote SÍ se sintetiza, el
        # malformado se omite, y resolve() de una URL malformada devuelve None (no
        # lanza, honra el contrato uuid|None).
        malformed = {"url": "https://[invalid", "title": "Rota", "company": "X"}
        good = {
            "url": "https://jobs.example.ch/new-999",
            "title": "New Role",
            "company": "Beta",
            "description": "x",
        }
        await ip.synthesize_vacancies(s, scope_id, [malformed, good])
        await s.commit()
        assert await _count(s, "vacancies") == 3  # solo la buena se añadió (2 → 3)
        assert await ip.resolve_vacancy_by_url(s, good["url"]) is not None
        assert await ip.resolve_vacancy_by_url(s, "https://[invalid") is None

        # --- COLISIÓN de URL normalizada (P2 análisis 2): dos URLs DISTINTAS cuyo id
        # vive en el fragmento (que normalize_url descarta) → misma clave. El import
        # DETECTA y OMITE la 2ª (audit log), conservando la 1ª — en vez de la fusión
        # falsa silenciosa del sink (última gana). Solo se crea UNA vacante, con el
        # title de la PRIMERA (no de la segunda).
        collide = [
            {"url": "https://spa.example.ch/#/vacancy/aaa", "title": "First Offer", "company": "A"},
            {"url": "https://spa.example.ch/#/vacancy/bbb", "title": "Second Offer", "company": "B"},
        ]
        before = await _count(s, "vacancies")
        collided = await ip.synthesize_vacancies(s, scope_id, collide)
        await s.commit()
        assert await _count(s, "vacancies") == before + 1  # SOLO una (la 2ª omitida)
        # Devuelve la 2ª URL como colisionada (el llamador la enruta a staging en
        # vez de que resolve la mapee a la vacante de la 1ª — P1 análisis 2 parte 3).
        assert collided == {collide[1]["url"]}
        cvid = await ip.resolve_vacancy_by_url(s, collide[0]["url"])
        assert cvid is not None
        ctitle = (
            await s.execute(
                sa.text(
                    "SELECT o.content->>'title' FROM vacancies v JOIN offer_revisions o "
                    "ON o.id = v.current_offer_revision_id WHERE v.id = :v"
                ),
                {"v": cvid},
            )
        ).scalar_one()
        assert ctitle == "First Offer"  # gana la 1ª; fusión falsa evitada

        # --- resolve HONRA merged_into (P3 análisis 2): una vacante-sombra fundida
        # ya no se resuelve (nunca enlazar una candidatura a una vacante fuera de
        # corpus) → None, como toda otra resolución de vacancy_id del core.
        gvid = await ip.resolve_vacancy_by_url(s, with_url[0]["url"])
        assert gvid is not None
        winner = uuid.uuid4()
        await s.execute(sa.text("INSERT INTO vacancies (id) VALUES (:w)"), {"w": winner})
        await s.execute(
            sa.text("UPDATE vacancies SET merged_into = :w WHERE id = :v"),
            {"w": winner, "v": gvid},
        )
        await s.commit()
        assert await ip.resolve_vacancy_by_url(s, with_url[0]["url"]) is None


async def _count(session, table: str) -> int:
    return (
        await session.execute(sa.text(f"SELECT count(*) FROM {table}"))
    ).scalar_one()
