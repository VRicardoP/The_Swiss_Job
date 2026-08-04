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
            result = await rollback_migration(s, manifest["provenance"])
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
            result = await rollback_migration(s, manifest["provenance"])
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


def test_rollback_empty_provenance_is_noop():
    """Procedencia vacía (re-run idempotente): no borra nada."""

    async def _run(factory):
        async with factory() as s:
            empty = {
                t: []
                for t in (
                    "application_status_events", "applications", "saved_searches",
                    "link_evidence", "dedup_candidates", "source_listing_revisions",
                    "source_listing_incarnations", "offer_revisions", "source_listings",
                    "vacancies", "harvest_scopes", "sources", "profile_vacancy_state",
                    "offer_revision_sources",
                )
            }
            result = await rollback_migration(s, empty)
            assert result["status"] == "rolled_back"
            assert all(v == 0 for v in result["deleted"].values())
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_rollback_aborts_if_nonprovenance_vacancy_points_to_provenance_offrev():
    """SEGURIDAD: si una vacante NO listada en la procedencia apunta (current_offer_revision)
    a un offer_revision de la procedencia, borrarlo nulificaría su canónica → ABORTA sin
    borrar. Se simula omitiendo la vacante creada de la procedencia (como si fuera reutilizada)."""

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, [_user("https://ab.example.ch/1")])
            vid = manifest["provenance"]["vacancies"][0]
            # La vacante `vid` apunta a su offer_revision (en la procedencia). Si la tratamos
            # como NO-procedencia (reutilizada), el guard debe detectar el puntero → abort.
            prov = dict(manifest["provenance"])
            prov["vacancies"] = []
            result = await rollback_migration(s, prov)
            assert result["status"] == "aborted"
            assert vid in result["unsafe_vacancies"]
            # No borró nada (la vacante y la application siguen).
            assert await _count(s, "vacancies") == 1
            assert await _count(s, "applications") == 1
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
            r = await rollback_migration(s, manifest["provenance"], manifest_id=mid)
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
            prov = dict(manifest["provenance"])
            prov["vacancies"] = []  # fuerza el abort (la vacante apunta a un offrev de procedencia)
            r = await rollback_migration(s, prov, manifest_id=mid)
            assert r["status"] == "aborted"
            await s.commit()
            assert await _manifest_status(s, mid) == "rollback_aborted"

    asyncio.run(_on_disposable_db(_run))


def test_rollback_invalid_manifest_id_aborts_without_deleting():
    """REGRESIÓN P1 ronda 2: un manifest_id INEXISTENTE aborta ANTES de borrar (fail-closed);
    los datos siguen intactos (antes borraba todo y el manifiesto real quedaba 'applied')."""
    import uuid

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, [_user("https://iv.example.ch/1")])
            await s.commit()
            r = await rollback_migration(
                s, manifest["provenance"], manifest_id=str(uuid.uuid4())
            )
            assert r["status"] == "aborted" and "no existe" in r["reason"]
            assert await _count(s, "vacancies") == 1  # NADA borrado
            assert await _count(s, "applications") == 1
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
            r = await rollback_migration(s, m1["provenance"], manifest_id=m1["id"])
            assert r["status"] == "aborted" and "LIFO" in r["reason"]
            assert await _count(s, "vacancies") == 1  # NADA borrado
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
            r = await rollback_migration(s, m1["provenance"])
            assert r["status"] == "rolled_back"
            await s.commit()
            assert await _count(s, "vacancies") == 0
        async with factory() as s:
            m2 = await man.migrate_and_reconcile(s, users)
            assert m2["verdict"] == "ok", m2["divergences"]
            assert m2["verification"]["verdict"] == "verified", m2["verification"]
            assert len(m2["provenance"]["vacancies"]) == 1  # re-creada limpiamente
            await s.commit()

    asyncio.run(_on_disposable_db(_run))
