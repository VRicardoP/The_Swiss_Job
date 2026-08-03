"""Integración del VERIFICADOR estructural independiente (§4, parte 3).

Verifica que `verify_migration` (a) da 'verified' en una migración limpia, (b) detecta un
LISTING PERDIDO (ledger created pero sin vacante — lo que reconcile NO distinguía de una
cuarentena), (c) detecta desacuerdo entre oráculos independientes (ledger vs procedencia), y
(d) acepta reused y cuarentenas legítimas. Postgres desechable; ejecutar vía core-migrate.
"""

import asyncio
import os
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from jobhunt_core import import_portfolio_manifest as man
from jobhunt_core.import_portfolio import PORTFOLIO_IMPORT_SOURCE
from jobhunt_core.import_portfolio_verify import verify_migration
from jobhunt_core.tests.test_integration_import_portfolio_ledger import _seed_other_source
from jobhunt_core.tests.test_integration_migration_rehearsal_portfolio import _on_disposable_db

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


def _user(apps: list[dict], ref: int = 1) -> dict:
    return {"external_ref": ref, "applications": apps, "saved_searches": []}


def _app(url: str, **extra) -> dict:
    base = {"url": url, "status": "applied", "title": "T", "company": "C",
            "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)}
    base.update(extra)
    return base


def test_verify_clean_migration_is_verified():
    """Migración normal → verification verdict 'verified', sin discrepancias."""

    async def _run(factory):
        async with factory() as s:
            users = [_user([_app("https://v.example.ch/1"), _app("https://v.example.ch/2")])]
            manifest = await man.migrate_and_reconcile(s, users)
            assert manifest["verification"]["verdict"] == "verified", manifest["verification"]
            assert manifest["verification"]["discrepancies"] == []
            assert manifest["verification"]["checked"]["created_vacancies"] == 2
            await s.commit()

    asyncio.run(_on_disposable_db(_run))


def test_verify_detects_lost_listing():
    """CLAVE: si una vacante-sombra `created` DESAPARECE (se archiva → irresoluble), el
    verificador lo marca como LISTING PERDIDO — no como cuarentena. reconcile (que lee la
    estructura final) lo confundiría con "nunca se sintetizó"."""

    async def _run(factory):
        async with factory() as s:
            url = "https://lost.example.ch/9"
            users = [_user([_app(url)])]
            manifest = await man.migrate_and_reconcile(s, users)
            assert manifest["verification"]["verdict"] == "verified"
            # Sabotaje: archiva la vacante creada → ya no es presentable/resoluble.
            vid = [e["vacancy_id"] for e in manifest["ledger"] if e["disposition"] == "created"][0]
            await s.execute(
                sa.text("UPDATE vacancies SET archived_at = now() WHERE id = :v"), {"v": vid}
            )
            # Re-verifica con el MISMO ledger/procedencia (el contrato de lo migrado).
            report = await verify_migration(
                s, users, manifest["ledger"], manifest["provenance"], PORTFOLIO_IMPORT_SOURCE
            )
            assert report["verdict"] == "discrepant"
            assert any("PERDIDO" in d for d in report["discrepancies"])
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_verify_detects_oracle_disagreement():
    """Si la procedencia de vacancies NO coincide con las created del ledger (dos oráculos
    independientes), el verificador lo marca — es la señal de un fallo de uno u otro."""

    async def _run(factory):
        async with factory() as s:
            users = [_user([_app("https://o.example.ch/1")])]
            manifest = await man.migrate_and_reconcile(s, users)
            # Procedencia manipulada: le quitamos la vacante creada.
            tampered = dict(manifest["provenance"])
            tampered["vacancies"] = []
            report = await verify_migration(
                s, users, manifest["ledger"], tampered, PORTFOLIO_IMPORT_SOURCE
            )
            assert report["verdict"] == "discrepant"
            assert any("oráculos" in d for d in report["discrepancies"])
            await s.commit()

    asyncio.run(_on_disposable_db(_run))


def test_verify_reused_and_collision_are_legitimate():
    """Una vacante REUTILIZADA de otra fuente + una COLISIÓN cross-source (cadena revertida)
    son AMBAS legítimas: el verificador da 'verified' (no las confunde con perdidas)."""

    async def _run(factory):
        async with factory() as s:
            # Otra fuente tiene #/A; el portfolio pedirá #/B (colisión cross-source) y una url
            # exacta reutilizada.
            await _seed_other_source(s, "https://coll.example.ch/#/A", "coll-A")
            await _seed_other_source(s, "https://reuse.example.ch/r", "reuse-r")
            users = [
                _user([
                    _app("https://reuse.example.ch/r"),        # reused (otra fuente exacta)
                    _app("https://coll.example.ch/#/B"),        # collision_cross_source
                    _app("https://fresh.example.ch/n"),         # created
                ])
            ]
            manifest = await man.migrate_and_reconcile(s, users)
            v = manifest["verification"]
            assert v["verdict"] == "verified", v["discrepancies"]
            dispositions = {e["url"]: e["disposition"] for e in manifest["ledger"]}
            assert dispositions["https://reuse.example.ch/r"] == "reused"
            assert dispositions["https://coll.example.ch/#/B"] == "quarantine"
            assert dispositions["https://fresh.example.ch/n"] == "created"
            await s.commit()

    asyncio.run(_on_disposable_db(_run))
