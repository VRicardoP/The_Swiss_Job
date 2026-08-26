"""Barrido de archivado ADR-07 (F-2, 2026-08-22) — la salida del corpus.

El sink delegaba en un barrido que no existía: el core acumuló ~4.000
vacantes activas más que el legacy en producción. Estos tests fijan las dos
ramas (muertas con gracia · rancias 120 d sin adjunto), la idempotencia y
las guardas (recién cerrada, con candidatura en la rama 2, sin encarnación
alguna) y, en la rama 1, que el adjunto NO retiene la vacante muerta en el
corpus pero sigue siendo resoluble para su perfil (G4-P2-3).
BD desechable vía core-migrate (mismo patrón que test_integration_sink).
"""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import applications
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


def _vac_id(factory, ext):
    return _sql(
        factory,
        "SELECT i.vacancy_id FROM source_listing_incarnations i "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "WHERE l.external_id = :e LIMIT 1",
        e=ext,
    ).scalar_one()


def test_g4_muerta_con_adjunto_se_archiva_pero_sigue_resoluble_para_su_perfil(db):
    """Regresión G4-P2-3 y G4-P3-2. El guard PF.3 que G3-A-P2-3 añadió a la
    rama de MUERTAS no estaba scopeado a perfil ni a consumer y, como no hay
    otra ruta que ponga `archived_at`, dejaba la oferta RETIRADA circulando
    COMO ACTIVA en el catálogo global y en el corpus de matching/dedup de
    TODOS los tenants, para siempre (ningún consumidor exige encarnación
    activa) — la patología de las ~4.000 vacantes de más, con ancla
    CROSS-TENANT. Y tampoco cubría el BOOKMARK PURO, la población mayoritaria
    del cutover y el síntoma que citaba su propio comentario.

    Ahora la vacante muerta SALE del corpus siempre, y lo que el guard
    protegía —que el adjunto no quede irresoluble— lo da `resolve_direct`,
    scopeado AL PERFIL que ya la tiene adjunta (candidatura o bookmark puro).
    """
    factory, created = db
    scope = _seed_scope(factory, created)
    for ext in ("muerta-adjunta", "muerta-marcada", "muerta-sola"):
        _ingest(factory, scope, ext)
    _sql(
        factory,
        "UPDATE source_listing_incarnations SET ended_at = now() - interval '30 days' "
        "WHERE id IN (SELECT i.id FROM source_listing_incarnations i "
        " JOIN source_listings l ON l.id=i.source_listing_id "
        " WHERE l.external_id IN ('muerta-adjunta', 'muerta-marcada', 'muerta-sola'))",
    )
    cid, pid, otro_pid, aid = (uuid.uuid4() for _ in range(4))
    _sql(factory, f"INSERT INTO consumers (id, name) VALUES ('{cid}', 'test-arch-m')")
    for p_id, ref in ((pid, "arch-m"), (otro_pid, "arch-m-otro")):
        _sql(
            factory,
            f"INSERT INTO profiles (id, consumer_id, external_ref) "
            f"VALUES ('{p_id}', '{cid}', '{ref}')",
        )
    v_adjunta = _vac_id(factory, "muerta-adjunta")
    v_marcada = _vac_id(factory, "muerta-marcada")
    v_sola = _vac_id(factory, "muerta-sola")
    _sql(
        factory,
        f"INSERT INTO applications (id, profile_id, vacancy_id) "
        f"VALUES ('{aid}', '{pid}', '{v_adjunta}')",
    )
    # Bookmark PURO: fila de profile_vacancy_state con saved_at y SIN
    # application (lo que produce el cutover de durables).
    _sql(
        factory,
        f"INSERT INTO profile_vacancy_state (profile_id, vacancy_id, saved_at) "
        f"VALUES ('{pid}', '{v_marcada}', now())",
    )
    created["extra_sql"] += [
        f"DELETE FROM consumers WHERE id = '{cid}'",
        f"DELETE FROM profiles WHERE consumer_id = '{cid}'",
        f"DELETE FROM applications WHERE id = '{aid}'",
        f"DELETE FROM profile_vacancy_state WHERE profile_id = '{pid}'",
    ]

    assert _sweep(factory)["archivadas_muertas"] == 3  # antes del fix: 2
    for ext in ("muerta-adjunta", "muerta-marcada", "muerta-sola"):
        # Las TRES salen del corpus: nada muerto se sirve como activo.
        assert _estado(factory, ext).archivada is True

    async def _resolver(vid, profile_id):
        async with factory() as s:
            return await applications.resolve_direct(s, vid, profile_id)

    # El adjunto del perfil sigue RESOLUBLE pese al archivado — candidatura…
    assert asyncio.run(_resolver(v_adjunta, pid)) == v_adjunta
    # …y bookmark PURO (lo que el guard nunca cubrió).
    assert asyncio.run(_resolver(v_marcada, pid)) == v_marcada
    # Sin adjunto sigue siendo 404, y el adjunto es del PERFIL: ni siquiera
    # otro perfil del MISMO consumer la resuelve (antes el guard era global).
    assert asyncio.run(_resolver(v_sola, pid)) is None
    assert asyncio.run(_resolver(v_adjunta, otro_pid)) is None
    assert asyncio.run(_resolver(v_adjunta, None)) is None


def test_b3_sink_no_refresca_snapshot_archivado_por_el_barrido(db):
    """Regresión B-3 (auditoría externa 2026-08-23): el sink leía las
    encarnaciones activas ANTES de _lock_vacancies; si archive_sweep ganaba
    la carrera (cerraba la encarnación rancia y archivaba la vacante), el
    sink refrescaba el snapshot OBSOLETO y la cosecha fresca quedaba
    enterrada en una vacante archivada sin encarnación activa. Tras el fix,
    la revalidación bajo el lock trata el slot como huérfano/reaparición:
    vacante NUEVA vigente con encarnación activa; la archivada no revive."""
    factory, created = db
    scope = _seed_scope(factory, created)
    _ingest(factory, scope, "carrera")
    _sql(
        factory,
        "UPDATE source_listing_incarnations SET last_seen_at = now() - interval '121 days' "
        "WHERE id IN (SELECT i.id FROM source_listing_incarnations i "
        " JOIN source_listings l ON l.id=i.source_listing_id WHERE l.external_id='carrera')",
    )

    class SinkConCarrera(RawListingSink):
        """Reproduce el interleaving del auditor: el barrido corre y COMMITEA
        en otra conexión en la ventana entre la lectura del snapshot y la
        adquisición de los locks."""

        async def _lock_vacancies(self, session, vacancy_ids) -> None:
            async with factory() as s2:
                await archive_sweep(s2)
                await s2.commit()
            await super()._lock_vacancies(session, vacancy_ids)

    async def go():
        async with factory() as s:
            await SinkConCarrera().handle(
                s,
                scope,
                (
                    RawListing(
                        external_id="carrera",
                        url="https://x/carrera",
                        payload={"title": "carrera", "v": 1},
                    ),
                ),
            )
            await s.commit()

    asyncio.run(go())

    async def check():
        async with factory() as s:
            return (
                await s.execute(
                    sa.text(
                        "SELECT count(*) FILTER (WHERE i.ended_at IS NULL "
                        "         AND v.archived_at IS NULL) AS vivas, "
                        "       count(*) FILTER (WHERE v.archived_at IS NOT NULL) "
                        "         AS archivadas, "
                        "       count(DISTINCT v.id) AS vacantes "
                        "FROM source_listing_incarnations i "
                        "JOIN vacancies v ON v.id = i.vacancy_id "
                        "JOIN source_listings l ON l.id = i.source_listing_id "
                        "WHERE l.external_id = 'carrera'"
                    )
                )
            ).one()

    st = asyncio.run(check())
    # pre-fix: vivas=0, vacantes=1 (la cosecha enterrada en la archivada)
    assert st.vivas == 1 and st.archivadas == 1 and st.vacantes == 2
