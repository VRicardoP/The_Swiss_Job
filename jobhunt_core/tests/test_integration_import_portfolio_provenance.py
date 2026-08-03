"""Integración de la PROCEDENCIA EXACTA de la importación del portfolio (§4, parte 2).

Verifica que `migrate_and_reconcile` produce `manifest["provenance"]` = filas insertadas por
ESTE run (snapshot después−antes), y que —a diferencia del inventario scopeado— (a) una
vacante REUTILIZADA de otra fuente NO aparece (preexistía), y (b) un re-run idempotente da
procedencia VACÍA. Postgres desechable; ejecutar vía core-migrate.
"""

import asyncio
import os
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from jobhunt_core import import_portfolio_manifest as man
from jobhunt_core import import_portfolio_provenance as prov
from jobhunt_core.import_portfolio import PORTFOLIO_IMPORT_SOURCE
from jobhunt_core.import_portfolio_durables import PORTFOLIO_CONSUMER
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


def test_provenance_created_covers_vacancy_and_durables():
    """Una migración fresca: la procedencia incluye la vacante-sombra NUEVA, su
    offer_revision, el corpus portfolio-import y los durables (application + evento)."""

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, [_user("https://p.example.ch/1")])
            assert manifest["verdict"] == "ok", manifest["divergences"]
            pv = manifest["provenance"]
            assert len(pv["vacancies"]) == 1
            assert len(pv["offer_revisions"]) == 1
            assert len(pv["source_listings"]) == 1
            assert len(pv["source_listing_incarnations"]) == 1
            assert len(pv["applications"]) == 1
            assert len(pv["application_status_events"]) == 1
            assert len(pv["sources"]) == 1  # portfolio-import se creó este run
            assert len(pv["harvest_scopes"]) == 1
            # La vacante en procedencia == la del ledger created.
            created = [e for e in manifest["ledger"] if e["disposition"] == "created"]
            assert {e["vacancy_id"] for e in created} == set(pv["vacancies"])
            await s.commit()

    asyncio.run(_on_disposable_db(_run))


def test_provenance_excludes_reused_other_source_vacancy():
    """CLAVE (lo que el inventario scopeado NO podía): una vacante reutilizada de OTRA
    fuente PREEXISTÍA → NO está en la procedencia de `vacancies` (el snapshot full-id la
    tenía en `antes`). El enlace portfolio-import (incarnación) SÍ es nuevo → sí aparece."""

    async def _run(factory):
        async with factory() as s:
            url = "https://reuse.example.ch/x"
            await _seed_other_source(s, url, "other-x")
            other_vid = (
                await s.execute(
                    sa.text(
                        "SELECT i.vacancy_id::text FROM source_listing_incarnations i "
                        "JOIN source_listings sl ON sl.id = i.source_listing_id "
                        "JOIN sources s ON s.id = sl.source_id AND s.name = 'arbeitnow' "
                        "WHERE i.ended_at IS NULL"
                    )
                )
            ).scalar_one()

            manifest = await man.migrate_and_reconcile(s, [_user(url)])
            assert manifest["verdict"] == "ok", manifest["divergences"]
            pv = manifest["provenance"]
            # La vacante reutilizada NO se creó este run → ausente de la procedencia.
            assert other_vid not in pv["vacancies"]
            assert pv["vacancies"] == []  # C-4 no creó ninguna vacante nueva
            # Pero el enlace portfolio-import (incarnación) SÍ es de este run.
            assert len(pv["source_listing_incarnations"]) == 1
            assert len(pv["applications"]) == 1
            # Y el ledger lo marca reused, coherente con la procedencia vacía de vacancies.
            assert any(e["disposition"] == "reused" for e in manifest["ledger"])
            await s.commit()

    asyncio.run(_on_disposable_db(_run))


def test_provenance_empty_on_idempotent_rerun():
    """Re-run idempotente: la 2ª ejecución no inserta NADA nuevo → procedencia vacía en
    todas las tablas (lo que el inventario scopeado NO distinguía: reaparecían las filas)."""

    async def _run(factory):
        users = [_user("https://p.example.ch/2")]
        async with factory() as s:
            m1 = await man.migrate_and_reconcile(s, users)
            assert m1["verdict"] == "ok", m1["divergences"]
            assert sum(len(v) for v in m1["provenance"].values()) > 0  # el 1er run insertó
            await s.commit()
        async with factory() as s:
            m2 = await man.migrate_and_reconcile(s, users)
            assert m2["verdict"] == "ok", m2["divergences"]
            # Nada nuevo en el 2º run: procedencia vacía en TODAS las tablas.
            assert sum(len(v) for v in m2["provenance"].values()) == 0
            await s.commit()

    asyncio.run(_on_disposable_db(_run))


def test_provenance_excludes_preexisting_dedup_candidate_on_reused_vacancy():
    """REGRESIÓN del fix scope-change: un dedup_candidate PREEXISTENTE que referencia una
    vacante que C-4 luego REUTILIZA (gana incarnación portfolio-import este run) NO debe
    entrar en procedencia. Con la query scopeada entraba (falso-nuevo → Part 4 lo borraría);
    con full-id se excluye (estaba en `antes`)."""
    import uuid

    async def _run(factory):
        async with factory() as s:
            url = "https://reuse-dc.example.ch/y"
            await _seed_other_source(s, url, "other-dc")
            reused_vid = (
                await s.execute(
                    sa.text(
                        "SELECT i.vacancy_id::text FROM source_listing_incarnations i "
                        "JOIN source_listings sl ON sl.id = i.source_listing_id "
                        "JOIN sources s ON s.id = sl.source_id AND s.name = 'arbeitnow' "
                        "WHERE i.ended_at IS NULL"
                    )
                )
            ).scalar_one()
            # Un dedup_candidate PREEXISTENTE que referencia la vacante que se reutilizará.
            other_v = uuid.uuid4()
            dc_id = uuid.uuid4()
            await s.execute(sa.text("INSERT INTO vacancies (id) VALUES (:v)"), {"v": other_v})
            await s.execute(
                sa.text(
                    "INSERT INTO dedup_candidates (id, vacancy_a, vacancy_b) "
                    "VALUES (:id, :a, :b)"
                ),
                {"id": dc_id, "a": reused_vid, "b": other_v},
            )
            await s.commit()

            manifest = await man.migrate_and_reconcile(s, [_user(url)])
            assert manifest["verdict"] == "ok", manifest["divergences"]
            # El dc preexistente NO es procedencia de este run (estaba en `antes`).
            assert str(dc_id) not in manifest["provenance"]["dedup_candidates"]
            assert manifest["provenance"]["dedup_candidates"] == []
            await s.commit()

    asyncio.run(_on_disposable_db(_run))


def test_snapshot_diff_is_pure_insert_delta():
    """exact_provenance = después − antes, por tabla: solo lo NUEVO, ordenado."""
    before = {"vacancies": {"a", "b"}, "applications": set()}
    after = {"vacancies": {"a", "b", "c"}, "applications": {"x"}}
    pv = prov.exact_provenance(before, after)
    assert pv["vacancies"] == ["c"]
    assert pv["applications"] == ["x"]
