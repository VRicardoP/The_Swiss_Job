"""Integración del ROLLBACK FK-safe de la importación del portfolio (§4, parte 4).

Verifica que `rollback_migration` (a) borra exactamente lo migrado (vacante creada + durables
+ corpus + fuente/scope), (b) PRESERVA una vacante reutilizada de otra fuente, (c) es no-op con
procedencia vacía, (d) ABORTA sin borrar si una vacante NO-procedencia apunta a un offer_revision
de la procedencia, y (e) round-trip migrate→rollback→migrate vuelve a 'verified'. Postgres
desechable; ejecutar vía core-migrate.
"""

import asyncio
import os
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from jobhunt_core import import_portfolio_manifest as man
from jobhunt_core.import_portfolio_rollback import rollback_migration
from jobhunt_core.tests.test_integration_import_portfolio_ledger import _seed_other_source
from jobhunt_core.tests.test_integration_migration_rehearsal_portfolio import _on_disposable_db

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


def _user(url: str, ref: int = 1) -> dict:
    return {
        "external_ref": ref,
        "applications": [
            {"url": url, "status": "applied", "title": "T", "company": "C",
             "description": "d", "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
        ],
        "saved_searches": [],
    }


async def _count(s, table: str) -> int:
    return (await s.execute(sa.text(f"SELECT count(*) FROM {table}"))).scalar_one()


def test_rollback_removes_created_migration():
    """Una migración fresca borrada por completo: 0 vacantes, 0 applications, 0 corpus
    portfolio-import, y la fuente/scope portfolio-import también se van."""

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, [_user("https://r.example.ch/1")])
            assert await _count(s, "vacancies") == 1
            assert await _count(s, "applications") == 1
            result = await rollback_migration(s, manifest["id"])
            assert result["status"] == "rolled_back"
            assert result["deleted"]["vacancies"] == 1
            assert result["deleted"]["applications"] == 1
            assert await _count(s, "vacancies") == 0
            assert await _count(s, "applications") == 0
            assert await _count(s, "source_listings") == 0
            assert await _count(s, "offer_revisions") == 0
            assert (
                await s.execute(
                    sa.text("SELECT count(*) FROM sources WHERE name = 'portfolio-import'")
                )
            ).scalar_one() == 0
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_rollback_preserves_reused_other_source_vacancy():
    """El rollback borra el enlace portfolio-import y los durables, pero la vacante
    REUTILIZADA de otra fuente PERMANECE (no la creó C-4)."""

    async def _run(factory):
        async with factory() as s:
            url = "https://reuse-rb.example.ch/x"
            await _seed_other_source(s, url, "rb-x")
            before_vac = await _count(s, "vacancies")  # 1 (la de arbeitnow)
            manifest = await man.migrate_and_reconcile(s, [_user(url)])
            result = await rollback_migration(s, manifest["id"])
            assert result["status"] == "rolled_back"
            # La vacante reutilizada sigue; el enlace portfolio-import y la application, no.
            assert await _count(s, "vacancies") == before_vac
            assert await _count(s, "applications") == 0
            assert (
                await s.execute(
                    sa.text(
                        "SELECT count(*) FROM source_listings sl JOIN sources s "
                        "ON s.id = sl.source_id AND s.name = 'portfolio-import'"
                    )
                )
            ).scalar_one() == 0
            # La incarnación de arbeitnow sigue activa (vacante intacta).
            assert (
                await s.execute(
                    sa.text(
                        "SELECT count(*) FROM source_listing_incarnations i JOIN source_listings sl "
                        "ON sl.id = i.source_listing_id JOIN sources s ON s.id = sl.source_id "
                        "AND s.name = 'arbeitnow' WHERE i.ended_at IS NULL"
                    )
                )
            ).scalar_one() == 1
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


async def _manifest_status(s, mid: str) -> str:
    return (
        await s.execute(
            sa.text("SELECT status FROM portfolio_migration_manifest WHERE id = :i"), {"i": mid}
        )
    ).scalar_one()


def test_rollback_marks_manifest_rolled_back():
    """REGRESIÓN P1 rev. externa: tras el rollback, la fila durable del manifiesto pasa a
    'rolled_back' — un verdict='ok' obsoleto ya no puede atestarse como GATE-C verde."""

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, [_user("https://ml.example.ch/1")])
            mid = manifest["id"]
            await s.commit()
            assert await _manifest_status(s, mid) == "applied"  # recién persistido
            r = await rollback_migration(s, mid)
            assert r["status"] == "rolled_back"
            await s.commit()
            assert await _manifest_status(s, mid) == "rolled_back"

    asyncio.run(_on_disposable_db(_run))


def test_rollback_aborted_marks_manifest_rollback_aborted():
    """REGRESIÓN P1 rev. externa: un rollback ABORTADO marca la fila 'rollback_aborted' (ni
    'applied' ni 'rolled_back') — el estado real queda registrado durablemente."""

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, [_user("https://ab2.example.ch/1")])
            mid = manifest["id"]
            await s.commit()
            # Tamper la procedencia ALMACENADA (el rollback usa ESA, no la del llamador): quita
            # las vacancies → la vacante creada queda "no-procedencia" pero apunta a su
            # offer_revision de la procedencia → unsafe abort.
            await s.execute(
                sa.text(
                    "UPDATE portfolio_migration_manifest "
                    "SET manifest = jsonb_set(manifest, '{provenance,vacancies}', '[]') "
                    "WHERE id = :id"
                ),
                {"id": mid},
            )
            await s.commit()
            r = await rollback_migration(s, mid)
            assert r["status"] == "aborted"
            await s.commit()
            assert await _manifest_status(s, mid) == "rollback_aborted"

    asyncio.run(_on_disposable_db(_run))


def test_core0015_upgrade_from_core0014_adds_seq():
    """REGRESIÓN P1 ronda 4: una BD ya en core0014 (status, SIN seq) al hacer upgrade a head
    obtiene `seq` vía core0015 — core0014 NO se reescribió. Prueba el CAMINO de upgrade
    incremental, no solo una BD creada desde cero."""
    import uuid as _uuid
    from urllib.parse import urlsplit, urlunsplit

    from sqlalchemy.ext.asyncio import create_async_engine

    from jobhunt_core.config import settings
    from jobhunt_core.tests.alembic_runner import run_alembic

    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    dbname = f"jobhunt_upg_{_uuid.uuid4().hex[:12]}"
    parts = urlsplit(admin_url)
    temp_url = urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))
    admin_engine = create_async_engine(
        admin_url, poolclass=sa.pool.NullPool, isolation_level="AUTOCOMMIT"
    )

    async def _create():
        async with admin_engine.connect() as c:
            await c.execute(sa.text(f'CREATE DATABASE "{dbname}"'))

    asyncio.run(_create())
    try:
        temp_engine = create_async_engine(
            temp_url, poolclass=sa.pool.NullPool,
            connect_args={"server_settings": {"search_path": f"{settings.CORE_DB_SCHEMA}, public"}},
        )

        async def _bootstrap():
            async with temp_engine.begin() as c:
                await c.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
                await c.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{settings.CORE_DB_SCHEMA}"'))

        async def _has_seq() -> int:
            async with temp_engine.connect() as c:
                return (
                    await c.execute(
                        sa.text(
                            "SELECT count(*) FROM information_schema.columns WHERE "
                            "table_schema = :s AND table_name = 'portfolio_migration_manifest' "
                            "AND column_name = 'seq'"
                        ),
                        {"s": settings.CORE_DB_SCHEMA},
                    )
                ).scalar_one()

        pre_id = _uuid.uuid4()

        async def _seed():
            async with temp_engine.begin() as c:
                await c.execute(
                    sa.text(
                        "INSERT INTO portfolio_migration_manifest (id, verdict, manifest, status) "
                        "VALUES (:i, 'ok', '{}'::jsonb, 'applied')"
                    ),
                    {"i": pre_id},
                )

        async def _pre_status() -> str:
            async with temp_engine.connect() as c:
                return (
                    await c.execute(
                        sa.text("SELECT status FROM portfolio_migration_manifest WHERE id = :i"),
                        {"i": pre_id},
                    )
                ).scalar_one()

        asyncio.run(_bootstrap())
        run_alembic(temp_url, "upgrade", "core0014")  # status (sin seq)
        assert asyncio.run(_has_seq()) == 0
        run_alembic(temp_url, "upgrade", "core0015")  # seq (SIN backfill, como se publicó)
        assert asyncio.run(_has_seq()) == 1
        # BD YA EN core0015 con una fila 'applied' (seq físico no fiable): tras core0016 debe
        # quedar 'unknown' — prueba que el backfill alcanza una BD parada en la revisión previa.
        asyncio.run(_seed())
        run_alembic(temp_url, "upgrade", "head")  # core0016 backfillea applied→unknown
        assert asyncio.run(_pre_status()) == "unknown"
        asyncio.run(temp_engine.dispose())
    finally:

        async def _drop():
            async with admin_engine.connect() as c:
                await c.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
            await admin_engine.dispose()

        asyncio.run(_drop())


def test_rollback_invalid_manifest_id_aborts_without_deleting():
    """REGRESIÓN P1 ronda 2: un manifest_id INEXISTENTE aborta ANTES de borrar (fail-closed);
    los datos siguen intactos (antes borraba todo y el manifiesto real quedaba 'applied')."""
    import uuid

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, [_user("https://iv.example.ch/1")])
            await s.commit()
            r = await rollback_migration(s, str(uuid.uuid4()))
            assert r["status"] == "aborted" and "no existe" in r["reason"]
            assert await _count(s, "vacancies") == 1  # NADA borrado
            assert await _count(s, "applications") == 1
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_rollback_uses_stored_provenance_bound_to_manifest_id():
    """REGRESIÓN P1 ronda 3/5: rollback_migration toma SOLO manifest_id y borra la procedencia
    ALMACENADA en ESE manifiesto — el ataque cross-manifest (procedencia de m1, id de m2) es
    IMPOSIBLE por construcción (no hay parámetro provenance). Deshacer m2 (idempotente, procedencia
    vacía) no borra nada y los datos de m1 siguen intactos."""

    async def _run(factory):
        users = [_user("https://bind.example.ch/1")]
        async with factory() as s:
            m1 = await man.migrate_and_reconcile(s, users)
            await s.commit()
            m2 = await man.migrate_and_reconcile(s, users)  # idempotente, procedencia VACÍA
            await s.commit()
            r = await rollback_migration(s, m2["id"])  # usa la procedencia de m2 (vacía)
            assert r["status"] == "rolled_back"
            assert sum(r["deleted"].values()) == 0  # m2 no borra nada (procedencia vacía)
            assert await _count(s, "vacancies") == 1  # los datos de m1 SIGUEN intactos
            assert await _manifest_status(s, m1["id"]) == "applied"  # m1 intacto
            await s.commit()

    asyncio.run(_on_disposable_db(_run))


def test_rollback_lifo_uses_seq_not_created_at():
    """REGRESIÓN P1 ronda 3: dos manifiestos en la MISMA transacción comparten created_at
    (now() es constante en la tx), pero `seq` (identity) los ordena. Deshacer el primero aborta
    (LIFO por seq, no por created_at empatado)."""

    async def _run(factory):
        users = [_user("https://seq.example.ch/1")]
        async with factory() as s:
            m1 = await man.migrate_and_reconcile(s, users)
            await man.migrate_and_reconcile(s, users)  # m2, MISMA tx (sin commit) → mismo now()
            r = await rollback_migration(s, m1["id"])
            assert r["status"] == "aborted" and "LIFO" in r["reason"]
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_rollback_refuses_older_manifest_lifo():
    """REGRESIÓN P1 ronda 2: con dos manifiestos 'applied' (m1 real, m2 idempotente), deshacer
    m1 aborta — hay un 'applied' posterior; hay que deshacer el más reciente primero (LIFO) o su
    evidencia obsoleta sobreviviría."""

    async def _run(factory):
        users = [_user("https://lifo.example.ch/1")]
        async with factory() as s:
            m1 = await man.migrate_and_reconcile(s, users)
            await s.commit()
            await man.migrate_and_reconcile(s, users)  # m2 idempotente, también 'applied'
            await s.commit()
            r = await rollback_migration(s, m1["id"])
            assert r["status"] == "aborted" and "LIFO" in r["reason"]
            assert await _count(s, "vacancies") == 1  # NADA borrado
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_rollback_aborts_on_manifest_missing_provenance():
    """REGRESIÓN P1 ronda 4: un manifiesto SIN la clave 'provenance' (malformado) → abort
    fail-closed; NO se marca rolled_back ni se borra (antes daba rolled_back con 0 borrados y el
    manifiesto quedaba deshecho con sus datos aún presentes)."""

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, [_user("https://mp.example.ch/1")])
            mid = manifest["id"]
            await s.commit()
            await s.execute(
                sa.text(
                    "UPDATE portfolio_migration_manifest SET manifest = manifest - 'provenance' "
                    "WHERE id = :id"
                ),
                {"id": mid},
            )
            await s.commit()
            r = await rollback_migration(s, mid)
            assert r["status"] == "aborted" and "provenance" in r["reason"]
            assert await _count(s, "vacancies") == 1  # datos INTACTOS
            assert await _manifest_status(s, mid) == "applied"  # NO marcado rolled_back
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_rollback_lifo_blocks_on_later_rollback_aborted():
    """REGRESIÓN P1 ronda 4: un manifiesto POSTERIOR en 'rollback_aborted' (rollback inseguro
    que NO borró sus datos) también bloquea el rollback de uno anterior — LIFO cubre applied Y
    rollback_aborted, no solo applied."""

    async def _run(factory):
        users = [_user("https://ra.example.ch/1")]
        async with factory() as s:
            m1 = await man.migrate_and_reconcile(s, users)
            await s.commit()
            m2 = await man.migrate_and_reconcile(s, users)  # idempotente, seq mayor
            await s.execute(
                sa.text(
                    "UPDATE portfolio_migration_manifest SET status = 'rollback_aborted' "
                    "WHERE id = :id"
                ),
                {"id": m2["id"]},
            )
            await s.commit()
            r = await rollback_migration(s, m1["id"])
            assert r["status"] == "aborted" and "LIFO" in r["reason"]
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_rollback_aborts_on_malformed_provenance_value():
    """REGRESIÓN P1 ronda 5: un VALOR de procedencia no-list[str] (p.ej. vacancies=null tras
    tamper) → abort fail-closed, NO rolled_back (antes lo interpretaba como 0 filas y marcaba
    deshecho sin borrar). Los datos siguen y el manifiesto sigue 'applied'."""

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, [_user("https://mv.example.ch/1")])
            mid = manifest["id"]
            await s.commit()
            await s.execute(
                sa.text(
                    "UPDATE portfolio_migration_manifest "
                    "SET manifest = jsonb_set(manifest, '{provenance,vacancies}', 'null') "
                    "WHERE id = :id"
                ),
                {"id": mid},
            )
            await s.commit()
            r = await rollback_migration(s, mid)
            assert r["status"] == "aborted" and "list[str]" in r["reason"]
            assert await _count(s, "vacancies") == 1  # datos INTACTOS
            assert await _manifest_status(s, mid) == "applied"  # NO marcado rolled_back
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_rollback_aborts_on_non_uuid_pk():
    """REGRESIÓN P1 ronda 6: una PK no-UUID en la procedencia (list[str] válida pero 'not-a-uuid')
    → abort fail-closed (validación UUID), NO rolled_back sin borrar."""
    import json

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, [_user("https://nu.example.ch/1")])
            mid = manifest["id"]
            await s.commit()
            await s.execute(
                sa.text(
                    "UPDATE portfolio_migration_manifest "
                    "SET manifest = jsonb_set(manifest, '{provenance,vacancies}', CAST(:v AS jsonb)) "
                    "WHERE id = :id"
                ),
                {"v": json.dumps(["not-a-uuid"]), "id": mid},
            )
            await s.commit()
            r = await rollback_migration(s, mid)
            assert r["status"] == "aborted" and "no-UUID" in r["reason"]
            assert await _count(s, "vacancies") == 1
            assert await _manifest_status(s, mid) == "applied"
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_rollback_aborts_on_unknown_table_in_provenance():
    """DEFENSE-IN-DEPTH (verificación ronda 6): una tabla DESCONOCIDA en la procedencia (que el
    rollback NO borra — deriva de esquema o tamper) → abort fail-closed, no un rolled_back con
    residuo. Productor y consumidor deben cubrir el MISMO conjunto de tablas."""
    import json

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, [_user("https://ut.example.ch/1")])
            mid = manifest["id"]
            await s.commit()
            await s.execute(
                sa.text(
                    "UPDATE portfolio_migration_manifest "
                    "SET manifest = jsonb_set(manifest, '{provenance,some_future_table}', CAST(:v AS jsonb)) "
                    "WHERE id = :id"
                ),
                {"v": json.dumps([]), "id": mid},
            )
            await s.commit()
            r = await rollback_migration(s, mid)
            assert r["status"] == "aborted" and "DESCONOCIDA" in r["reason"]
            assert await _count(s, "vacancies") == 1  # intacto
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_rollback_aborts_and_reverts_on_incomplete_delete():
    """REGRESIÓN P1 ronda 6: una PK VÁLIDA (UUID) pero INEXISTENTE → el borrado no la alcanza →
    el savepoint REVIERTE todo el bloque (incluidas las tablas que sí borraron) y aborta; NO
    rolled_back. Los datos quedan INTACTOS."""
    import json
    import uuid as _uuid

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, [_user("https://ic.example.ch/1")])
            mid = manifest["id"]
            await s.commit()
            # saved_searches (hoja, fuera del guard unsafe y sin bloquear vacancies) con un UUID
            # VÁLIDO pero INEXISTENTE → el borrado no lo alcanza → incompleto.
            await s.execute(
                sa.text(
                    "UPDATE portfolio_migration_manifest "
                    "SET manifest = jsonb_set(manifest, '{provenance,saved_searches}', CAST(:v AS jsonb)) "
                    "WHERE id = :id"
                ),
                {"v": json.dumps([str(_uuid.uuid4())]), "id": mid},
            )
            await s.commit()
            r = await rollback_migration(s, mid)
            assert r["status"] == "aborted" and "incompleto" in r["reason"]
            assert await _count(s, "vacancies") == 1  # el savepoint revirtió TODO
            assert await _count(s, "applications") == 1  # incluidas las que sí borraron
            assert await _manifest_status(s, mid) == "applied"
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_migrate_rollback_migrate_roundtrip_is_verified():
    """Round-trip: migrar → rollback (commit) → migrar de nuevo → 'verified'. Prueba que el
    rollback deja el estado LIMPIO para una re-migración (sin residuos que la ensucien)."""

    async def _run(factory):
        users = [_user("https://rt.example.ch/1")]
        async with factory() as s:
            m1 = await man.migrate_and_reconcile(s, users)
            assert m1["verification"]["verdict"] == "verified"
            r = await rollback_migration(s, m1["id"])
            assert r["status"] == "rolled_back"
            await s.commit()
            assert await _count(s, "vacancies") == 0
            # P1 ronda 5: manifest_id obligatorio → m1 queda marcado rolled_back (no 'applied'
            # obsoleto tras borrar los datos).
            assert await _manifest_status(s, m1["id"]) == "rolled_back"
        async with factory() as s:
            m2 = await man.migrate_and_reconcile(s, users)
            assert m2["verdict"] == "ok", m2["divergences"]
            assert m2["verification"]["verdict"] == "verified", m2["verification"]
            assert len(m2["provenance"]["vacancies"]) == 1  # re-creada limpiamente
            await s.commit()

    asyncio.run(_on_disposable_db(_run))
