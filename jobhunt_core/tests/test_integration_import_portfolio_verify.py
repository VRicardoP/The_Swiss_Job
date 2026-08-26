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
from jobhunt_core.tests.test_integration_import_portfolio_ledger import (
    _attach_extra_incarnation,
    _other_source_vacancy,
    _seed_other_source,
)
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


def test_verify_lost_listing_in_ledger_reports_not_crashes():
    """REGRESIÓN análisis 1 (P2): un `created` con vacancy_id None en el ledger (anomalía del
    sink: listing PERDIDO) debía crashear el cross-check (sorted mezclando None y str). Ahora
    se reporta como discrepant/PERDIDO limpiamente, sin TypeError."""

    async def _run(factory):
        async with factory() as s:
            url = "https://lost2.example.ch/z"
            users = [_user([_app(url)])]
            ledger = [
                {"url": url, "url_normalized": url, "external_id": "x",
                 "disposition": "created", "reason": None, "vacancy_id": None},
            ]
            report = await verify_migration(
                s, users, ledger, {"vacancies": []}, PORTFOLIO_IMPORT_SOURCE
            )
            assert report["verdict"] == "discrepant"  # no crashea
            assert any("PERDIDO" in d for d in report["discrepancies"])
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_verify_flags_vacancy_without_canonical():
    """REGRESIÓN P1 rev. externa: una vacante `created` SIN cadena canónica
    (current_offer_revision NULL → impresentable en el catálogo) da discrepant, no verified."""

    async def _run(factory):
        async with factory() as s:
            url = "https://nocanon.example.ch/1"
            users = [_user([_app(url)])]
            manifest = await man.migrate_and_reconcile(s, users)
            assert manifest["verification"]["verdict"] == "verified"
            vid = [e["vacancy_id"] for e in manifest["ledger"] if e["disposition"] == "created"][0]
            # Simular canónica perdida (un fallo del sink que dejara la vacante impresentable).
            await s.execute(
                sa.text("UPDATE vacancies SET current_offer_revision_id = NULL WHERE id = :v"),
                {"v": vid},
            )
            report = await verify_migration(
                s, users, manifest["ledger"], manifest["provenance"], PORTFOLIO_IMPORT_SOURCE
            )
            assert report["verdict"] == "discrepant"
            assert any("IMPRESENTABLE" in d for d in report["discrepancies"])
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_no_title_durable_staged_even_if_sibling_shares_url():
    """REGRESIÓN P1 ronda 2: un durable SIN título cuya url SÍ sintetiza (por un hermano válido
    con la misma url) se STAGEA por-durable (snapshot vacío = impresentable), NO se migra. El
    ledger de la url es created (el hermano); NO hay doble entrada created+no_title para la url."""
    url = "https://shared.example.ch/j"
    users = [
        {
            "external_ref": 1,
            "applications": [
                {"url": url, "status": "saved", "title": "Good title",
                 "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
                {"url": url, "status": "applied", "title": "   ",  # sin título → staging
                 "created_at": datetime(2026, 6, 2, tzinfo=timezone.utc)},
            ],
            "saved_searches": [],
        }
    ]

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, users)
            assert manifest["verdict"] == "ok", manifest["divergences"]
            assert manifest["verification"]["verdict"] == "verified", manifest["verification"]
            # La url se sintetizó (por el hermano) → created; SIN doble entrada no_title para la url.
            url_entries = [e for e in manifest["ledger"] if e["url"] == url]
            assert len(url_entries) == 1 and url_entries[0]["disposition"] == "created"
            # El durable sin título está en staging, NO en applications con snapshot vacío.
            assert any(
                r["reason"] == "no_title" for r in manifest["staged"] if r["external_ref"] == "1"
            )
            n_apps = (await s.execute(sa.text("SELECT count(*) FROM applications"))).scalar_one()
            assert n_apps == 0  # el 'saved' es bookmark; el 'applied' sin título → staging
            await s.commit()

    asyncio.run(_on_disposable_db(_run))


def test_surrogate_title_quarantined_not_false_lost():
    """REGRESIÓN P2 ronda 2: un título con surrogate (el sink lo cuarentena por UnicodeEncode)
    pasaba el precheck y quedaba created sin vacante → falso PERDIDO. Ahora _synthesizable
    reutiliza la frontera del sink (_preprocess) → cuarentena malformed, verified."""

    async def _run(factory):
        async with factory() as s:
            url = "https://sur.example.ch/1"
            users = [_user([_app(url, title="T\ud800bad")])]  # título no codificable
            manifest = await man.migrate_and_reconcile(s, users)
            assert manifest["verdict"] == "ok", manifest["divergences"]
            assert manifest["verification"]["verdict"] == "verified", manifest["verification"]
            e = [x for x in manifest["ledger"] if x["url"] == url][0]
            assert e["disposition"] == "quarantine" and e["reason"] == "malformed"
            await s.commit()

    asyncio.run(_on_disposable_db(_run))


def test_toxic_titled_winner_staged_no_crash():
    """REGRESIÓN P1 ronda 3: un durable titulado-TÓXICO (surrogate) que GANARÍA la consolidación
    por recencia se STAGEA por-durable (misma frontera que la síntesis), NO se migra — su
    snapshot reventaría el INSERT CAST(:snap AS jsonb). La migración COMPLETA y el hermano limpio
    (más antiguo) migra con su título."""
    url = "https://toxwin.example.ch/1"
    users = [
        {
            "external_ref": 1,
            "applications": [
                {"url": url, "status": "applied", "title": "Engineer", "company": "A",
                 "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},  # limpio, antiguo
                {"url": url, "status": "applied", "title": "Engineer\ud800", "company": "A",
                 "created_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},  # tóxico, RECIENTE
            ],
            "saved_searches": [],
        }
    ]

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, users)  # NO debe reventar
            assert manifest["verdict"] == "ok", manifest["divergences"]
            assert manifest["verification"]["verdict"] == "verified", manifest["verification"]
            title = (
                await s.execute(sa.text("SELECT snapshot->>'title' FROM applications"))
            ).scalar_one()
            assert title == "Engineer"  # el LIMPIO, no el tóxico ganador por recencia
            assert any(r["external_ref"] == "1" for r in manifest["staged"])  # tóxico staged
            await s.commit()

    asyncio.run(_on_disposable_db(_run))


def test_toxic_titled_sibling_no_false_divergent():
    """REGRESIÓN P1 (verificación ronda 2): dos durables de la MISMA url — D_a titulado pero con
    payload no codificable (surrogate → el sink lo cuarentena) y D_b limpio + más reciente. El
    sink sintetiza la canónica de D_b; el oráculo de oferta debe elegir TAMBIÉN a D_b
    (is_synthesizable), no al primer grouped D_a → sin falso divergent."""
    url = "https://toxic-sib.example.ch/1"
    users = [
        {
            "external_ref": 1,
            "applications": [
                {"url": url, "status": "applied", "title": "Engineer\ud800", "company": "ACME",
                 "description": "Great job",
                 "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
                {"url": url, "status": "applied", "title": "Engineer", "company": "ACME",
                 "description": "Great job",
                 "created_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},  # más reciente, limpio
            ],
            "saved_searches": [],
        }
    ]

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, users)
            assert manifest["verdict"] == "ok", manifest["divergences"]
            assert manifest["verification"]["verdict"] == "verified", manifest["verification"]
            await s.commit()

    asyncio.run(_on_disposable_db(_run))


def test_verify_reused_without_canonical_is_not_flagged():
    """REGRESIÓN P1 (verificación de fixes): una vacante REUTILIZADA de otra fuente con canónica
    NULL (contenido ACTUAL ajeno no normalizable — condición preexistente que C-4 ni causó ni
    puede arreglar) NO debe dar discrepant. El check de canónica se GATEA a created."""

    async def _run(factory):
        async with factory() as s:
            url = "https://reuse-nc.example.ch/1"
            await _seed_other_source(s, url, "nc-1")
            users = [_user([_app(url)])]
            manifest = await man.migrate_and_reconcile(s, users)
            reused = [e["vacancy_id"] for e in manifest["ledger"] if e["disposition"] == "reused"]
            assert reused, "el durable debía resolver como reused"
            # Nulificar la canónica de la vacante reutilizada (estado ajeno de la otra fuente).
            await s.execute(
                sa.text("UPDATE vacancies SET current_offer_revision_id = NULL WHERE id = :v"),
                {"v": reused[0]},
            )
            report = await verify_migration(
                s, users, manifest["ledger"], manifest["provenance"], PORTFOLIO_IMPORT_SOURCE
            )
            assert report["verdict"] == "verified", report["discrepancies"]  # no bloquea por lo ajeno
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_verify_no_url_ledger_completeness():
    """REGRESIÓN P2 rev. externa: un durable SIN url tiene su entrada quarantine:no_url en el
    ledger (contrato por-entrada), y la verificación de completitud no_url pasa."""

    async def _run(factory):
        async with factory() as s:
            users = [_user([_app("https://u.example.ch/1"), _app(None, status="saved")])]
            manifest = await man.migrate_and_reconcile(s, users)
            assert manifest["verification"]["verdict"] == "verified", manifest["verification"]
            no_url = [
                e for e in manifest["ledger"]
                if e["disposition"] == "quarantine" and e["reason"] == "no_url"
            ]
            assert len(no_url) == 1
            await s.commit()

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
            # Otra fuente tiene una vacante que abarca DOS claves normalizadas
            # (colisión cross-source REAL, G1-P3-6) y una url exacta reutilizada.
            coll_url = "https://coll.example.ch/jobs/a"
            await _seed_other_source(s, coll_url, "coll-A")
            vid = await _other_source_vacancy(s, coll_url)
            await _attach_extra_incarnation(
                s, vid, "https://coll.example.ch/jobs/b", "coll-B"
            )
            await _seed_other_source(s, "https://reuse.example.ch/r", "reuse-r")
            users = [
                _user([
                    _app("https://reuse.example.ch/r"),        # reused (otra fuente exacta)
                    _app(coll_url),                             # collision_cross_source
                    _app("https://fresh.example.ch/n"),         # created
                ])
            ]
            manifest = await man.migrate_and_reconcile(s, users)
            v = manifest["verification"]
            assert v["verdict"] == "verified", v["discrepancies"]
            dispositions = {e["url"]: e["disposition"] for e in manifest["ledger"]}
            assert dispositions["https://reuse.example.ch/r"] == "reused"
            assert dispositions[coll_url] == "quarantine"
            assert dispositions["https://fresh.example.ch/n"] == "created"
            await s.commit()

    asyncio.run(_on_disposable_db(_run))


def test_entrypoint_downgrades_verdict_on_structural_discrepancy(monkeypatch):
    """REGRESIÓN P1 ronda 8: si la verificación estructural es 'discrepant' (listing PERDIDO por
    un fallo del sink) pero `reconcile` —que solo compara VALORES materiales contra el origen—
    dice 'ok', el ENTRYPOINT debe degradar el verdict SUPERIOR (el que se PERSISTE y gobierna la
    confirmación del llamador: confirma SOLO si 'ok') a 'divergent' y anexar sus discrepancias.
    Sin esto, el falso verde que el 4º artefacto debe impedir. La DETECCIÓN ya la cubre
    test_verify_detects_lost_listing; aquí se prueba la PROPAGACIÓN forzando el verificador."""

    async def _fake_verify(session, users, ledger, provenance, source_name):
        return {
            "verdict": "discrepant",
            "discrepancies": ["LISTING PERDIDO: url=https://x.example.ch/1 sin vacante"],
            "checked": {},
        }

    monkeypatch.setattr(man, "verify_migration", _fake_verify)

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, [_user([_app("https://prop.example.ch/1")])])
            # reconcile por sí solo daría 'ok' (destino material coherente); la verificación
            # estructural degrada el verdict superior y aporta la discrepancia.
            assert manifest["verification"]["verdict"] == "discrepant"
            assert manifest["verdict"] == "divergent", manifest["verdict"]
            assert any(
                "verificación estructural" in d and "PERDIDO" in d
                for d in manifest["divergences"]
            ), manifest["divergences"]
            # La COLUMNA persistida (la que un GATE-C ingenuo leería) también es 'divergent'.
            persisted = (
                await s.execute(
                    sa.text("SELECT verdict FROM portfolio_migration_manifest WHERE id = :i"),
                    {"i": manifest["id"]},
                )
            ).scalar_one()
            assert persisted == "divergent"
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_verify_detects_extra_url_not_in_origin(monkeypatch):
    """REGRESIÓN P1 ronda 9: la completitud era UNIDIRECCIONAL (solo urls de origen SIN entrada en
    el ledger). Una url ADICIONAL en el ledger, AUSENTE del origen, se colaba — el caso peligroso
    es una `reused` inyectada: NO crea vacante (el cross-check created==procedencia cuadra), se
    excluye del oráculo material, RESUELVE a una vacante válida... y quedaba 'verified', pese a
    añadir al corpus un enlace portfolio-import que no procede del origen. La completitud
    BIDIRECCIONAL (Counter) la marca discrepant y la propagación de la ronda 8 degrada el verdict
    superior a 'divergent'."""
    import jobhunt_core.import_portfolio_migrate as mig

    extra_url = "https://extra.example.ch/injected"
    orig_synth = mig.synthesize_vacancies

    async def _inject(session, scope_id, items, *args, **kwargs):
        # El pipeline procesa una url EXTRA que NO está en `users` (reutiliza la vacante sembrada
        # de otra fuente) → entra al ledger como 'reused' sin crear vacante.
        items = list(items) + [
            {"url": extra_url, "title": "Inj", "company": "X", "description": "d"}
        ]
        return await orig_synth(session, scope_id, items, *args, **kwargs)

    monkeypatch.setattr(mig, "synthesize_vacancies", _inject)

    async def _run(factory):
        async with factory() as s:
            await _seed_other_source(s, extra_url, "inj-x")  # otra fuente → la extra será reused
            manifest = await man.migrate_and_reconcile(
                s, [_user([_app("https://normal.example.ch/1")])]
            )
            # La url extra entró al ledger como reused (no está en users → ajena al origen).
            extra = [e for e in manifest["ledger"] if e["url"] == extra_url]
            assert len(extra) == 1 and extra[0]["disposition"] == "reused", manifest["ledger"]
            # El verificador la caza como AUSENTE del origen; el verdict superior se degrada.
            assert manifest["verification"]["verdict"] == "discrepant", manifest["verification"]
            assert any(
                "AUSENTES del origen" in d for d in manifest["verification"]["discrepancies"]
            ), manifest["verification"]["discrepancies"]
            assert manifest["verdict"] == "divergent", manifest["verdict"]
            await s.rollback()

    asyncio.run(_on_disposable_db(_run))


def test_company_no_str_no_es_falso_divergent():
    """Regresión G1 H-9: company/description/url no-str en un durable (int del
    feed) hacían divergir esperado (tipo Python) vs real (`->>` siempre text) →
    falso 'divergent' y cutover abortado (el título ya estaba protegido). El
    lado esperado aplica ahora la coerción textual jsonb-equivalente."""

    async def _run(factory):
        async with factory() as s:
            users = [{
                "external_ref": 1,
                "applications": [{
                    "url": "https://h9fix.example.ch/1", "status": "applied",
                    "title": "T", "company": 123, "description": 4.5,
                    "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
                }],
                "saved_searches": [],
            }]
            manifest = await man.migrate_and_reconcile(s, users)
            assert manifest["verdict"] == "ok", manifest["divergences"]
            snap = (
                await s.execute(
                    sa.text(
                        "SELECT snapshot->>'company' AS c, "
                        "snapshot->>'description' AS d FROM applications"
                    )
                )
            ).one()
            assert (snap.c, snap.d) == ("123", "4.5")
            await s.commit()

    asyncio.run(_on_disposable_db(_run))


def test_company_float_exponencial_no_es_falso_divergent():
    """Regresión G2-P3-1: `_pg_text` decía serializar los escalares «como los
    serializa jsonb», pero jsonb NORMALIZA los números a `numeric` y numeric_out
    jamás usa exponente: json.dumps(1.5e300) daba '1.5e+300' contra los 301
    dígitos que devuelve `->>` ⇒ falso 'divergent' y cutover abortado — la clase
    que H-9 cerró para int, reabierta para el repr exponencial de Python
    (|x| >= 1e16 o < 1e-4)."""

    async def _run(factory):
        async with factory() as s:
            users = [{
                "external_ref": 1,
                "applications": [{
                    "url": "https://g2p31.example.ch/1", "status": "applied",
                    "title": "T", "company": 1.5e300, "description": 1e-5,
                    "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
                }],
                "saved_searches": [],
            }]
            manifest = await man.migrate_and_reconcile(s, users)
            assert manifest["verdict"] == "ok", manifest["divergences"]
            snap = (
                await s.execute(
                    sa.text(
                        "SELECT snapshot->>'company' AS c, "
                        "snapshot->>'description' AS d FROM applications"
                    )
                )
            ).one()
            # Lo que jsonb devuelve DE VERDAD: numeric expandido, sin exponente.
            assert snap.c == man._pg_text(1.5e300) and len(snap.c) == 301
            assert snap.d == man._pg_text(1e-5) == "0.00001"
            await s.commit()

    asyncio.run(_on_disposable_db(_run))
