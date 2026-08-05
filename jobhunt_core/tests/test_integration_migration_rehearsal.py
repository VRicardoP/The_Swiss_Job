"""Ensayo de migración sobre COPIA (A-12): down-migrations válidas.

DoD: el ciclo completo head→base→head corre limpio sobre una BD DESECHABLE
poblada con un grafo REPRESENTATIVO (todas las familias de tablas con datos:
cosecha, corpus, canónica, perfiles+activaciones, embeddings con partición,
matching, estado, outbox). "Copia" en Fase A = BD desechable poblada en head
(el clon por TEMPLATE exige cero conexiones; el ensayo del runbook real con
backup/restore llega con el cutover de Fase C — plan §15bis). Jamás toca la
BD compartida. Ejecutar vía core-migrate.
"""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import jobhunt_core.harvest.providers  # noqa: F401 — registra extractor/normalizador
from jobhunt_core.config import settings
from jobhunt_core.tests.alembic_runner import run_alembic

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)

SHA = "d" * 40


def test_full_downgrade_upgrade_cycle_on_populated_copy():
    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    dbname = f"jobhunt_rehearsal_{uuid.uuid4().hex[:12]}"
    from urllib.parse import urlsplit, urlunsplit

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
            # search_path fijado POR CONEXIÓN (NullPool renueva la
            # conexión tras cada commit y un SET suelto se perdería).
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

        # POBLAR en head un grafo REPRESENTATIVO usando los SERVICIOS reales
        # (no INSERTs sueltos): cosecha→canónica, perfil+activación, modelo
        # con PARTICIÓN, embeddings, evaluación+estado y outbox.
        async def seed():
            from jobhunt_core import embeddings, matching, profiles
            from jobhunt_core.harvest.sink import RawListingSink
            from jobhunt_core.harvest.types import RawListing

            async with factory() as s:
                source_id, scope_id = uuid.uuid4(), uuid.uuid4()
                await s.execute(
                    sa.text(
                        "INSERT INTO sources (id, name, tier) VALUES (:i, 'arbeitnow', 0)"
                    ),
                    {"i": source_id},
                )
                await s.execute(
                    sa.text(
                        "INSERT INTO harvest_scopes (id, source_id, params, tier) "
                        "VALUES (:i, :s, '{}'::jsonb, 0)"
                    ),
                    {"i": scope_id, "s": source_id},
                )
                await s.commit()
                await RawListingSink().handle(
                    s, str(scope_id),
                    (
                        RawListing(
                            external_id="j1", url="https://x/j1",
                            payload={
                                "title": "Backend Dev", "company_name": "ACME AG",
                                "description": "d", "tags": ["py"],
                            },
                        ),
                    ),
                )
                cid = await profiles.ensure_consumer(s, "rehearsal-tenant")
                pid = await profiles.upsert_profile(s, cid, "user-1")
                await profiles.save_profile_revision(
                    s, pid, {"title": "dev", "skills": ["py"]}
                )
                mid = await embeddings.register_model(s, "modelo-rehearsal", SHA)
                polid = await matching.ensure_policy(s, "cosine", "v1")
                await s.commit()
                # Vectores directos (sin ML): oferta por text_hash + perfil.
                th = (
                    await s.execute(
                        sa.text("SELECT text_hash FROM offer_revisions LIMIT 1")
                    )
                ).scalar_one()
                await embeddings.store_offer_embeddings(
                    s, mid, [{"text_hash": th, "vector": [1.0] + [0.0] * 383}]
                )
                rev = await profiles.current_revision(s, pid)
                await embeddings.store_profile_embeddings(
                    s, mid,
                    [{"revision_id": rev.id, "profile_id": pid,
                      "vector": [1.0] + [0.0] * 383}],
                )
                await s.commit()
                r = await matching.evaluate_profile(s, pid, mid, polid)
                await s.commit()
                assert r["evaluated"] == 1 and r["new_evals"] == 1
                # C-ESQ (core0011): dato real también en las 4 tablas de
                # seguimiento para validar SU down-migration con filas delante.
                vid = (
                    await s.execute(sa.text("SELECT id FROM vacancies LIMIT 1"))
                ).scalar_one()
                app_id = uuid.uuid4()
                await s.execute(
                    sa.text(
                        "INSERT INTO applications (id, profile_id, vacancy_id, snapshot) "
                        "VALUES (:a, :p, :v, '{\"title\": \"Backend Dev\"}'::jsonb)"
                    ),
                    {"a": app_id, "p": pid, "v": vid},
                )
                await s.execute(
                    sa.text(
                        "INSERT INTO application_status_events (application_id, status) "
                        "VALUES (:a, 'applied')"
                    ),
                    {"a": app_id},
                )
                await s.execute(
                    sa.text(
                        "INSERT INTO saved_searches (profile_id, name) "
                        "VALUES (:p, 'rehearsal')"
                    ),
                    {"p": pid},
                )
                await s.execute(
                    sa.text(
                        "INSERT INTO idempotency_records "
                        "(consumer_id, key, route, request_hash, expires_at) "
                        "VALUES (:c, 'k1', 'PUT /v1/profiles/x', 'h1', now())"
                    ),
                    {"c": cid},
                )
                await s.commit()
                counts = {}
                for tbl in (
                    "vacancies", "offer_revisions", "profile_revision_activations",
                    "offer_embeddings", "profile_embeddings", "match_evaluations",
                    "profile_vacancy_state", "integration_outbox",
                    "integration_outbox_deliveries", "applications",
                    "application_status_events", "saved_searches",
                    "idempotency_records",
                ):
                    counts[tbl] = (
                        await s.execute(sa.text(f"SELECT count(*) FROM {tbl}"))
                    ).scalar_one()
                return counts

        counts = asyncio.run(seed())
        assert all(n >= 1 for n in counts.values()), counts  # grafo REPRESENTATIVO

        # FRONTERA DE DATOS de core0005 (rev. 1ª A-12): la ÚNICA down
        # data-sensitive (VARCHAR(100)→60) debe FALLAR CONTROLADAMENTE — sin
        # truncar — con un destino de 61+ chars delante; después se retira la
        # fila y el ciclo limpio continúa.
        long_dest = "x" * 61

        async def seed_long_destination():
            async with factory() as s:
                eid = uuid.uuid4()
                await s.execute(
                    sa.text(
                        "INSERT INTO integration_outbox "
                        "(event_id, aggregate, aggregate_id, version, type, payload) "
                        "VALUES (:e, 'test', 'frontera', 1, 'match.evaluated', '{}'::jsonb)"
                    ),
                    {"e": eid},
                )
                await s.execute(
                    sa.text(
                        "INSERT INTO integration_outbox_deliveries (event_id, destination) "
                        "VALUES (:e, :d)"
                    ),
                    {"e": eid, "d": long_dest},
                )
                await s.commit()
                return eid

        eid_long = asyncio.run(seed_long_destination())
        blocked = run_alembic(temp_url, "downgrade", "core0004", check=False)
        assert blocked.returncode != 0  # FALLO CONTROLADO: jamás truncar
        assert b"character varying(60)" in blocked.stderr + blocked.stdout

        async def remove_long_destination():
            async with factory() as s:
                await s.execute(
                    sa.text("DELETE FROM integration_outbox WHERE event_id = :e"),
                    {"e": eid_long},
                )
                await s.commit()

        asyncio.run(remove_long_destination())

        # DOWNGRADE COMPLETO paso a paso (valida CADA down-migration con
        # datos reales delante) y vuelta a head.
        for target in (
            "core0012", "core0011", "core0010", "core0009", "core0008b",
            "core0008a", "core0007", "core0006", "core0005", "core0004",
            "core0003", "core0002", "core0001", "base",
        ):
            run_alembic(temp_url, "downgrade", target)
        run_alembic(temp_url, "upgrade", "head")

        async def verify_after_cycle():
            async with factory() as s:
                version = (
                    await s.execute(sa.text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
                assert version == "core0023"
                # El esquema re-creado FUNCIONA: smoke de escritura real.
                await s.execute(
                    sa.text("INSERT INTO consumers (id, name) VALUES (:i, 'post-cycle')"),
                    {"i": uuid.uuid4()},
                )
                await s.commit()
                # Particiones/índices clave re-creados.
                idx = (
                    await s.execute(
                        sa.text(
                            "SELECT count(*) FROM pg_indexes WHERE schemaname = :s "
                            "AND indexname IN ('ix_source_listings_url_normalized', "
                            "'ix_pract_profile_seq', 'ix_profrev_text_hash_id', "
                            "'ix_outbox_deliv_pending', 'ix_outbox_deliv_inflight', "
                            "'ix_incarnation_vacancy_active', 'uq_labeled_dedup_pair', "
                            "'ix_shadow_change_unapplied', 'ix_saved_searches_profile', "
                            "'ix_idem_expires_at', 'ix_vacancies_feed_keyset')"
                        ),
                        {"s": settings.CORE_DB_SCHEMA},
                    )
                ).scalar_one()
                assert idx == 11

        asyncio.run(verify_after_cycle())
        asyncio.run(temp_engine.dispose())
    finally:

        async def drop_db():
            async with admin_engine.connect() as c:
                await c.execute(
                    sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
                )
            await admin_engine.dispose()

        asyncio.run(drop_db())
