"""Barrido de archivado ADR-07 (F-2, 2026-08-22) — la salida del corpus.

El sink delegaba en un barrido que no existía: el core acumuló ~4.000
vacantes activas más que el legacy en producción. Estos tests fijan las dos
ramas (muertas con gracia · rancias 120 d sin adjunto), la idempotencia y
las guardas (recién cerrada, con candidatura, sin encarnación alguna).
BD desechable vía core-migrate (mismo patrón que test_integration_sink).
"""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core.tests import dbcleanup
from jobhunt_core.archive import archive_sweep
from jobhunt_core.config import settings
from jobhunt_core.harvest.sink import RawListing, RawListingSink

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {"source": None, "scopes": [], "extra_sql": []}
    yield factory, created

    async def cleanup():
        async with factory() as s:
            # Primero lo ajeno al grafo del sink (candidaturas/perfiles del
            # caso PF.3), en orden FK-seguro; luego el grafo de la fuente.
            for stmt in reversed(created["extra_sql"]):
                await s.execute(sa.text(stmt))
            await dbcleanup.purge_source_graph(
                s, [created["source"]], created["scopes"]
            )
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


def _seed_scope(factory, created) -> str:
    async def go():
        async with factory() as s:
            source_id, scope_id = uuid.uuid4(), uuid.uuid4()
            created["source"] = source_id
            created["scopes"].append(scope_id)
            await s.execute(
                sa.text(
                    "INSERT INTO sources (id, name, tier) VALUES (:id, 'arbeitnow', 0)"
                ),
                {"id": source_id},
            )
            await s.execute(
                sa.text(
                    "INSERT INTO harvest_scopes (id, source_id, params, tier) "
                    "VALUES (:id, :src, '{}'::jsonb, 0)"
                ),
                {"id": scope_id, "src": source_id},
            )
            await s.commit()
            return str(scope_id)

    return asyncio.run(go())


def _ingest(factory, scope_id, ext) -> None:
    async def go():
        async with factory() as s:
            await RawListingSink().handle(
                s,
                scope_id,
                (
                    RawListing(
                        external_id=ext,
                        url=f"https://x/{ext}",
                        payload={"title": ext, "v": 1},
                    ),
                ),
            )
            await s.commit()

    asyncio.run(go())


def _sql(factory, stmt, **params):
    async def go():
        async with factory() as s:
            r = await s.execute(sa.text(stmt), params)
            await s.commit()
            return r

    return asyncio.run(go())


def _sweep(factory) -> dict:
    async def go():
        async with factory() as s:
            r = await archive_sweep(s)
            await s.commit()
            return r

    return asyncio.run(go())


def _estado(factory, ext):
    async def go():
        async with factory() as s:
            return (
                await s.execute(
                    sa.text(
                        "SELECT v.archived_at IS NOT NULL AS archivada, "
                        "  EXISTS (SELECT 1 FROM source_listing_incarnations x "
                        "          WHERE x.vacancy_id = v.id AND x.ended_at IS NULL) "
                        "    AS con_activa "
                        "FROM vacancies v "
                        "JOIN source_listing_incarnations i ON i.vacancy_id = v.id "
                        "JOIN source_listings l ON l.id = i.source_listing_id "
                        "WHERE l.external_id = :ext LIMIT 1"
                    ),
                    {"ext": ext},
                )
            ).one()

    return asyncio.run(go())


def test_muerta_con_gracia_cumplida_se_archiva_y_reciente_no(db):
    factory, created = db
    scope = _seed_scope(factory, created)
    _ingest(factory, scope, "vieja")
    _ingest(factory, scope, "reciente")
    # cerrar ambas encarnaciones: una hace 10 días, la otra hace 1 hora
    _sql(
        factory,
        "UPDATE source_listing_incarnations SET ended_at = now() - interval '10 days' "
        "WHERE id IN (SELECT i.id FROM source_listing_incarnations i "
        " JOIN source_listings l ON l.id=i.source_listing_id WHERE l.external_id='vieja')",
    )
    _sql(
        factory,
        "UPDATE source_listing_incarnations SET ended_at = now() - interval '1 hour' "
        "WHERE id IN (SELECT i.id FROM source_listing_incarnations i "
        " JOIN source_listings l ON l.id=i.source_listing_id WHERE l.external_id='reciente')",
    )
    r = _sweep(factory)
    assert r["archivadas_muertas"] >= 1
    assert _estado(factory, "vieja").archivada is True
    assert _estado(factory, "reciente").archivada is False  # gracia (3 d)
    # idempotencia: el segundo barrido no encuentra nada nuevo de esta fuente
    assert _estado(factory, "vieja").archivada is True
    _sweep(factory)
    assert _estado(factory, "reciente").archivada is False


def test_activa_fresca_no_se_toca(db):
    factory, created = db
    scope = _seed_scope(factory, created)
    _ingest(factory, scope, "viva")
    _sweep(factory)
    st = _estado(factory, "viva")
    assert st.archivada is False and st.con_activa is True


def test_rancia_120d_se_archiva_y_cierra_su_encarnacion(db):
    factory, created = db
    scope = _seed_scope(factory, created)
    _ingest(factory, scope, "rancia")
    _sql(
        factory,
        "UPDATE source_listing_incarnations SET last_seen_at = now() - interval '121 days' "
        "WHERE id IN (SELECT i.id FROM source_listing_incarnations i "
        " JOIN source_listings l ON l.id=i.source_listing_id WHERE l.external_id='rancia')",
    )
    r = _sweep(factory)
    assert r["archivadas_rancias"] >= 1
    st = _estado(factory, "rancia")
    # archivada Y con el invariante conservado: sin encarnación activa
    assert st.archivada is True and st.con_activa is False


def test_rancia_con_candidatura_se_conserva_pf3(db):
    factory, created = db
    scope = _seed_scope(factory, created)
    _ingest(factory, scope, "adjunta")
    _sql(
        factory,
        "UPDATE source_listing_incarnations SET last_seen_at = now() - interval '121 days' "
        "WHERE id IN (SELECT i.id FROM source_listing_incarnations i "
        " JOIN source_listings l ON l.id=i.source_listing_id WHERE l.external_id='adjunta')",
    )
    cid, pid, aid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _sql(factory, f"INSERT INTO consumers (id, name) VALUES ('{cid}', 'test-archive')")
    _sql(
        factory,
        f"INSERT INTO profiles (id, consumer_id, external_ref) VALUES ('{pid}', '{cid}', 'arch-t')",
    )
    _sql(
        factory,
        f"INSERT INTO applications (id, profile_id, vacancy_id) "
        f"SELECT '{aid}', '{pid}', i.vacancy_id FROM source_listing_incarnations i "
        "JOIN source_listings l ON l.id=i.source_listing_id WHERE l.external_id='adjunta'",
    )
    created["extra_sql"] += [
        f"DELETE FROM consumers WHERE id = '{cid}'",
        f"DELETE FROM profiles WHERE id = '{pid}'",
        f"DELETE FROM applications WHERE id = '{aid}'",
    ]
    _sweep(factory)
    st = _estado(factory, "adjunta")
    assert st.archivada is False and st.con_activa is True  # PF.3: se conserva
