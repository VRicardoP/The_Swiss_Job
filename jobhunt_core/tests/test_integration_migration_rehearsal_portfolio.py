"""Ensayo LOCAL de la migración de durables del portfolio (C-4, parte 3).

Arnés de reconciliación del cutover del piloto: orquesta la migración completa
(partes 1+2 vía import_portfolio_migrate) sobre una BD DESECHABLE poblada con un
dataset REPRESENTATIVO y verifica las propiedades que el ensayo REAL (sobre copia
del NAS, gated) exigirá:

- ORDEN y encadenado por usuario (provision→síntesis→mapeo).
- VACANTE COMPARTIDA entre usuarios: una URL común resuelve a UNA vacante-sombra,
  con una application por perfil (UNIQUE(profile_id, vacancy_id) intacto).
- DRY-RUN REVERSIBLE: migrar, ver checksums NO vacíos, y ROLLBACK ⇒ nada persiste
  en NINGUNA tabla que escribe la migración (tracking + corpus global: vacancies,
  sources, profiles…) — el origen jamás se muta; todo-o-nada del llamador.
- RECONCILIACIÓN: conteos agregados + count/checksum por objetivo (4 tablas de
  tracking + la canónica sintetizada); los checksums son != md5('') (DoD:
  "checksums de cero = confianza falsa").
- ENUMERACIÓN de irrecuperables: los durables sin vacante (unresolved) / status
  inválido / sin name se LISTAN en report['staged'] (identidad + razón), no solo
  se cuentan — identidad contable: nada se pierde sin auditar.
- IDEMPOTENCIA a nivel de DATOS: re-migrar el mismo origen NO cambia los checksums.
- PORTABILIDAD cross-BD: el mismo origen migrado en DOS BDs distintas produce
  checksums IDÉNTICOS (claves de negocio portables, no PK uuid4).

El ensayo REAL sobre los datos del NAS queda GATED (mismo criterio que las demás
validaciones NAS). Ejecutar vía core-migrate.
"""

import asyncio
import os
import uuid
from collections import Counter
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

# Todas las tablas que la migración ESCRIBE (tracking + cadena de identidad del
# corpus completa que toca el sink + tenant): el dry-run debe revertirlas TODAS
# (fidelidad del rollback). Incluye source_listing_revisions/offer_revision_sources
# (el sink las escribe por cada vacante-sombra) y dedup_candidates/link_evidence
# (cross-source, 0 en este dataset pero parte del contrato del sink).
FULL_WRITE_SET = (
    "applications", "application_status_events", "profile_vacancy_state",
    "saved_searches", "vacancies", "offer_revisions", "offer_revision_sources",
    "source_listings", "source_listing_incarnations", "source_listing_revisions",
    "dedup_candidates", "link_evidence", "sources", "harvest_scopes",
    "consumers", "profiles",
)


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
                # Sin url → unresolved (no persiste, se ENUMERA en staged).
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
    "consolidated": 1, "invalid_status": 0, "collision": 0,
}
EXPECTED_SS = {"migrated": 5, "existing": 0, "invalid_filters": 1, "no_name": 0}
# Filas resultantes por objetivo de reconciliación (4 tracking + canónica
# sintetizada: 6 URLs distintas → 6 vacantes-sombra portfolio-import).
EXPECTED_ROWS = {
    "applications": 5, "application_status_events": 5,
    "profile_vacancy_state": 4, "saved_searches": 5,
    "portfolio_vacancies": 6,
}


async def _on_disposable_db(async_fn):
    """Crea una BD DESECHABLE (extensión+esquema+alembic head), ejecuta
    async_fn(factory) y la elimina en finally. Devuelve lo que async_fn retorne.
    Jamás toca la BD compartida."""
    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    dbname = f"jobhunt_rehearsal_pf_{uuid.uuid4().hex[:12]}"
    parts = urlsplit(admin_url)
    temp_url = urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))
    admin_engine = create_async_engine(
        admin_url, poolclass=sa.pool.NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin_engine.connect() as c:
            await c.execute(sa.text(f'CREATE DATABASE "{dbname}"'))
        temp_engine = create_async_engine(
            temp_url, poolclass=sa.pool.NullPool,
            connect_args={
                "server_settings": {
                    "search_path": f"{settings.CORE_DB_SCHEMA}, public"
                }
            },
        )
        try:
            async with temp_engine.begin() as c:
                await c.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
                await c.execute(
                    sa.text(f'CREATE SCHEMA IF NOT EXISTS "{settings.CORE_DB_SCHEMA}"')
                )
            run_alembic(temp_url, "upgrade", "head")  # sync (subprocess)
            factory = async_sessionmaker(temp_engine, expire_on_commit=False)
            return await async_fn(factory)
        finally:
            await temp_engine.dispose()
    finally:
        async with admin_engine.connect() as c:
            await c.execute(
                sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
            )
        await admin_engine.dispose()


def test_migration_rehearsal_portfolio_local():
    asyncio.run(_on_disposable_db(_scenario))


def test_checksums_portable_across_databases():
    """PORTABILIDAD cross-BD (reproducción del hallazgo P2 análisis 1): el MISMO
    origen migrado en dos BDs frescas independientes produce checksums IDÉNTICOS
    — los PK uuid4 (profile_id/vacancy_id) difieren entre BDs pero la proyección
    usa claves de negocio portables (external_ref, url_normalized)."""
    from jobhunt_core import import_portfolio_migrate as ipm

    users = _representative_users()

    async def _migrate_and_checksum(factory):
        async with factory() as s:
            await ipm.migrate_portfolio(s, users)
            await s.commit()
            return await ipm.table_checksums(s)

    chk_a = asyncio.run(_on_disposable_db(_migrate_and_checksum))
    chk_b = asyncio.run(_on_disposable_db(_migrate_and_checksum))
    assert chk_a == chk_b  # claves de negocio portables → coincidencia cross-BD
    assert all(t["checksum"] != ipm.EMPTY_CHECKSUM for t in chk_a.values())


def test_collision_routed_to_staging():
    """P1 rev. externa: dos URLs SPA DISTINTAS que normalizan a la MISMA clave (el
    id vive en el fragmento que normalize_url descarta) NO deben fundirse ni elegir
    ganador por orden: ambigüedad no resoluble ⇒ NO se sintetiza NINGUNA vacante y
    AMBOS durables se enrutan a staging (razón 'collision'). Caso CROSS-USUARIO (la
    síntesis GLOBAL detecta la colisión entre usuarios)."""
    from jobhunt_core import import_portfolio_migrate as ipm

    users = [
        {"external_ref": 1, "applications": [
            {"url": "https://spa.ch/jobs#/detail/111", "status": "applied",
             "title": "Job 111", "company": "A",
             "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
        ], "saved_searches": []},
        {"external_ref": 2, "applications": [
            {"url": "https://spa.ch/jobs#/detail/222", "status": "applied",
             "title": "Job 222", "company": "B",
             "created_at": datetime(2026, 6, 2, tzinfo=timezone.utc)},
        ], "saved_searches": []},
    ]

    async def _run(factory):
        async with factory() as s:
            report = await ipm.migrate_portfolio(s, users)
            await s.commit()
            # STAGE-ALL: ni vacante ni application; ambos durables a staging.
            assert await _count(s, "vacancies") == 0
            assert await _count(s, "applications") == 0
            assert report["applications"]["collision"] == 2
            assert report["applications"]["applications"] == 0
            staged = report["staged"]
            assert len(staged) == 2
            assert {r["reason"] for r in staged} == {"collision"}
            assert {r["durable"]["title"] for r in staged} == {"Job 111", "Job 222"}
            assert {r["external_ref"] for r in staged} == {"1", "2"}

    asyncio.run(_on_disposable_db(_run))


def test_consolidated_real_enumerated():
    """P3 análisis 2: dos candidaturas REALES del mismo perfil a la MISMA url
    (UNIQUE(profile_id, vacancy_id) ⇒ una application) — gana la más reciente y la
    PERDEDORA se ENUMERA en staged (razón 'consolidated_real'), no solo un log
    (distinguible de un fold benigno saved+follow_up)."""
    from jobhunt_core import import_portfolio_migrate as ipm

    users = [{"external_ref": 1, "applications": [
        {"url": "https://x.ch/dup", "status": "applied", "title": "Dup",
         "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
        {"url": "https://x.ch/dup", "status": "rejected", "title": "Dup",
         "created_at": datetime(2026, 6, 2, tzinfo=timezone.utc)},  # más reciente
    ], "saved_searches": []}]

    async def _run(factory):
        async with factory() as s:
            report = await ipm.migrate_portfolio(s, users)
            await s.commit()
            assert await _count(s, "applications") == 1
            assert report["applications"]["applications"] == 1
            assert report["applications"]["consolidated"] == 1
            staged = report["staged"]
            assert len(staged) == 1
            assert staged[0]["reason"] == "consolidated_real"
            assert staged[0]["durable"]["status"] == "applied"  # la perdedora
            st = (
                await s.execute(sa.text("SELECT status FROM applications LIMIT 1"))
            ).scalar_one()
            assert st == "rejected"  # la application lleva el status más reciente

    asyncio.run(_on_disposable_db(_run))


def test_bookmark_value_loss_staged():
    """P1 #2 rev. externa: dos bookmarks (saved) de la MISMA vacante con notas
    DISTINTAS — una sola columna profile_vacancy_state.notes ⇒ gana la MÁS RECIENTE
    (determinista, no por orden del lote) y la otra se ENUMERA (razón
    'consolidated_saved'), no se pierde en silencio."""
    from jobhunt_core import import_portfolio_migrate as ipm

    users = [{"external_ref": 1, "applications": [
        {"url": "https://x.ch/b", "status": "saved", "notes": "vieja",
         "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
        {"url": "https://x.ch/b", "status": "saved", "notes": "nueva",
         "created_at": datetime(2026, 6, 2, tzinfo=timezone.utc)},
    ], "saved_searches": []}]

    async def _run(factory):
        async with factory() as s:
            report = await ipm.migrate_portfolio(s, users)
            await s.commit()
            lost = [r for r in report["staged"] if r["reason"] == "consolidated_saved"]
            assert len(lost) == 1
            assert lost[0]["durable"]["notes"] == "vieja"  # la más antigua, no elegida
            note = (
                await s.execute(sa.text("SELECT notes FROM profile_vacancy_state LIMIT 1"))
            ).scalar_one()
            assert note == "nueva"  # la más reciente gana

    asyncio.run(_on_disposable_db(_run))


def test_coalescence_deterministic():
    """P2 rev. externa 2: la nota/follow_up del bookmark elegido NO depende del orden
    del lote (invertirlo produce el MISMO destino y checksum)."""
    from jobhunt_core import import_portfolio_migrate as ipm

    saved = [
        {"url": "https://x.ch/c", "status": "saved", "notes": "vieja",
         "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
        {"url": "https://x.ch/c", "status": "saved", "notes": "nueva",
         "created_at": datetime(2026, 6, 2, tzinfo=timezone.utc)},
    ]

    def _run_order(order):
        users = [{"external_ref": 1, "applications": order, "saved_searches": []}]

        async def _run(factory):
            async with factory() as s:
                await ipm.migrate_portfolio(s, users)
                await s.commit()
                note = (
                    await s.execute(sa.text("SELECT notes FROM profile_vacancy_state LIMIT 1"))
                ).scalar_one()
                return note, await ipm.table_checksums(s)

        return asyncio.run(_on_disposable_db(_run))

    assert _run_order(saved) == _run_order(list(reversed(saved)))


def test_invalid_filter_disabled_and_staged():
    """P1 #3 rev. externa: un filtro inválido NO se convierte en búsqueda ACTIVA con
    {} (alertaría de todo): se importa DESACTIVADA + se ENUMERA el original."""
    from jobhunt_core import import_portfolio_migrate as ipm

    users = [{"external_ref": 1, "applications": [], "saved_searches": [
        {"name": "alertas", "filters": "{broken", "is_active": True},
    ]}]

    async def _run(factory):
        async with factory() as s:
            report = await ipm.migrate_portfolio(s, users)
            await s.commit()
            assert report["saved_searches"]["invalid_filters"] == 1
            assert report["saved_searches"]["migrated"] == 1
            assert [r for r in report["staged"] if r["reason"] == "invalid_filters"]
            row = (
                await s.execute(sa.text(
                    "SELECT is_active, filters FROM saved_searches WHERE name = 'alertas'"))
            ).one()
            assert row.is_active is False and row.filters == {}  # inerte, no alerta

    asyncio.run(_on_disposable_db(_run))


def test_saved_search_material_dedup():
    """P1 #4 rev. externa: mismo name+filters pero min_score DISTINTO = DOS búsquedas
    materiales distintas → AMBAS migran (no se colapsa una config en silencio)."""
    from jobhunt_core import import_portfolio_migrate as ipm

    users = [{"external_ref": 1, "applications": [], "saved_searches": [
        {"name": "s", "filters": '{"q": "x"}', "min_score": 20, "is_active": True},
        {"name": "s", "filters": '{"q": "x"}', "min_score": 80, "is_active": True},
    ]}]

    async def _run(factory):
        async with factory() as s:
            report = await ipm.migrate_portfolio(s, users)
            await s.commit()
            assert report["saved_searches"]["migrated"] == 2
            assert await _count(s, "saved_searches") == 2

    asyncio.run(_on_disposable_db(_run))


def test_recency_tie_deterministic():
    """P2 #7 rev. externa: dos candidaturas reales con IGUAL recencia y status
    distinto ganan lo MISMO al invertir el orden del lote (desempate por contenido,
    no por posición)."""
    from jobhunt_core import import_portfolio_migrate as ipm

    same = datetime(2026, 6, 1, tzinfo=timezone.utc)
    apps = [
        {"url": "https://x.ch/t", "status": "applied", "title": "T", "created_at": same},
        {"url": "https://x.ch/t", "status": "rejected", "title": "T", "created_at": same},
    ]

    def _winner(order):
        users = [{"external_ref": 1, "applications": order, "saved_searches": []}]

        async def _run(factory):
            async with factory() as s:
                await ipm.migrate_portfolio(s, users)
                await s.commit()
                return (
                    await s.execute(sa.text("SELECT status FROM applications LIMIT 1"))
                ).scalar_one()

        return asyncio.run(_on_disposable_db(_run))

    assert _winner(apps) == _winner(list(reversed(apps)))  # orden-independiente


def test_checksums_scoped_to_consumer():
    """P2 #5 rev. externa: los conteos/checksums SOLO cuentan el consumer `portfolio`
    — una fila de otro tenant en el core NO altera el manifiesto."""
    from jobhunt_core import import_portfolio as ip
    from jobhunt_core import import_portfolio_migrate as ipm
    from jobhunt_core import profiles

    users = [{"external_ref": 1, "applications": [
        {"url": "https://x.ch/a", "status": "applied", "title": "A",
         "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
    ], "saved_searches": []}]

    async def _run(factory):
        async with factory() as s:
            await ipm.migrate_portfolio(s, users)
            await s.commit()
            chk_before = await ipm.table_checksums(s)
            # Fila de OTRO tenant contra la misma vacante (UNIQUE profile+vacancy ok).
            vid = await ip.resolve_vacancy_by_url(s, "https://x.ch/a")
            oc = await profiles.ensure_consumer(s, "other-tenant")
            op = await profiles.upsert_profile(s, oc, "z")
            await s.execute(
                sa.text("INSERT INTO applications (id, profile_id, vacancy_id, snapshot) "
                        "VALUES (:i, :p, :v, '{}'::jsonb)"),
                {"i": uuid.uuid4(), "p": op, "v": vid},
            )
            await s.commit()
            assert await ipm.table_checksums(s) == chk_before  # ignora el ajeno

    asyncio.run(_on_disposable_db(_run))


def test_checksums_portable_across_timezones():
    """P2 #6 rev. externa: el mismo origen migrado con TimeZone de sesión DISTINTO
    produce checksums IDÉNTICOS (last_run_at→epoch, ORDER BY COLLATE "C")."""
    from jobhunt_core import import_portfolio_migrate as ipm

    users = [{"external_ref": 1, "applications": [], "saved_searches": [
        {"name": "s", "filters": '{"q": "x"}', "min_score": 0, "is_active": True,
         "last_notified_at": datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)},
    ]}]

    def _with_tz(tz):
        async def _run(factory):
            async with factory() as s:
                await s.execute(sa.text(f"SET TIME ZONE '{tz}'"))
                await ipm.migrate_portfolio(s, users)
                await s.commit()
                await s.execute(sa.text(f"SET TIME ZONE '{tz}'"))  # NullPool renovó conexión
                return await ipm.table_checksums(s)

        return asyncio.run(_on_disposable_db(_run))

    assert _with_tz("UTC") == _with_tz("America/New_York")


def test_migrate_and_reconcile_ok_and_persists():
    """P1 rev. externa 3: el ENTRYPOINT transaccional migra + reconcilia las 4 tablas
    + staging + persiste el manifiesto con las IDENTIDADES de rollback, todo antes del
    commit. Veredicto 'ok' para el dataset representativo."""
    from jobhunt_core import import_portfolio_manifest as man

    users = _representative_users()

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, users)
            assert manifest["verdict"] == "ok", manifest["divergences"]
            # Identidades de rollback (scopeadas): PK del tracking + vacantes nuevas.
            assert len(manifest["identities"]["applications"]) == 5
            assert len(manifest["identities"]["new_vacancies"]) == 6
            await s.commit()
            row = (
                await s.execute(sa.text(
                    "SELECT verdict, manifest FROM portfolio_migration_manifest "
                    "WHERE id = :i"), {"i": manifest["id"]})
            ).one()
            assert row.verdict == "ok"
            assert row.manifest["identities"]["applications"]

    asyncio.run(_on_disposable_db(_run))


def test_manifest_detects_deterministic_bug():
    """P2 #8 rev. externa: un error DETERMINISTA (min_score 60→0) —que pasaría rerun y
    comparación cross-BD porque ambos destinos fallan igual— lo DETECTA la
    reconciliación contra el ESPERADO del origen."""
    from jobhunt_core import import_portfolio_manifest as man
    from jobhunt_core import import_portfolio_migrate as ipm

    users = [{"external_ref": 1, "applications": [], "saved_searches": [
        {"name": "s", "filters": '{"q": "x"}', "min_score": 60, "is_active": True},
    ]}]

    async def _run(factory):
        async with factory() as s:
            report = await ipm.migrate_portfolio(s, users)
            await s.execute(sa.text("UPDATE saved_searches SET min_score = 0"))
            manifest = await man.reconcile(s, users, report)
            assert manifest["verdict"] == "divergent"
            assert any("saved_searches" in d for d in manifest["divergences"])

    asyncio.run(_on_disposable_db(_run))


def test_offer_oracle_strips_whitespace():
    """Verificación adversarial: una descripción con salto de línea final (el sink la
    normaliza con strip) NO debe dar falso divergent — el oráculo de oferta normaliza
    igual (P1)."""
    from jobhunt_core import import_portfolio_manifest as man

    users = [{"external_ref": 1, "applications": [
        {"url": "https://x.ch/a", "status": "applied", "title": "Engineer",
         "company": "Acme", "description": "Great role.\n",
         "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
    ], "saved_searches": []}]

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, users)
            assert manifest["verdict"] == "ok", manifest["divergences"]

    asyncio.run(_on_disposable_db(_run))


def test_staging_identity_robust():
    """Verificación adversarial: name vacío ('') y name NO-str (['x']) en saved_searches
    → verdict ok (identidad de staging robusta None↔'' + coerción str, sin crash)."""
    from jobhunt_core import import_portfolio_manifest as man

    users = [{"external_ref": 1, "applications": [], "saved_searches": [
        {"name": "", "filters": "{}"},
        {"name": ["x"], "filters": "{}"},
    ]}]

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, users)
            assert manifest["verdict"] == "ok", manifest["divergences"]

    asyncio.run(_on_disposable_db(_run))


def test_utf8_quarantine_modeled():
    """Verificación adversarial: un surrogate suelto en title (el sink cuarentena por
    UnicodeEncodeError) → el durable queda unresolved; el oráculo lo lee del estado
    final (sin incarnación) → verdict ok (P2)."""
    from jobhunt_core import import_portfolio_manifest as man

    users = [{"external_ref": 1, "applications": [
        {"url": "https://ok.ch/j1", "status": "applied", "title": "T\ud800",
         "company": "X", "description": "d",
         "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
    ], "saved_searches": []}]

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, users)
            assert manifest["verdict"] == "ok", manifest["divergences"]

    asyncio.run(_on_disposable_db(_run))


def test_rollback_new_reused_by_creation_not_refs():
    """Verificación adversarial: new/reused distingue CREAR vs REUTILIZAR (incarnación de
    otra fuente), NO por referencias de terceros — una vacante que C-4 SINTETIZÓ es new
    aunque otro consumer la referencie (esa FK la trata el script gated §4 con
    abort-on-RESTRICT); una que C-4 REUTILIZÓ es reused (solo se borra el enlace)."""
    from jobhunt_core import import_portfolio as ip
    from jobhunt_core import import_portfolio_manifest as man
    from jobhunt_core import import_portfolio_migrate as ipm
    from jobhunt_core import profiles

    users = [{"external_ref": 1, "applications": [
        {"url": "https://x.ch/a", "status": "applied", "title": "A",
         "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
    ], "saved_searches": []}]

    async def _run(factory):
        async with factory() as s:
            await ipm.migrate_portfolio(s, users)
            await s.commit()
            vid = await ip.resolve_vacancy_by_url(s, "https://x.ch/a")
            oc = await profiles.ensure_consumer(s, "other-tenant")
            op = await profiles.upsert_profile(s, oc, "z")
            await s.execute(sa.text(
                "INSERT INTO applications (id, profile_id, vacancy_id, snapshot) "
                "VALUES (:i, :p, :v, '{}'::jsonb)"),
                {"i": uuid.uuid4(), "p": op, "v": vid})
            await s.commit()
            ident = await man._captured_identities(s)
            # C-4 la sintetizó (sin incarnación de otra fuente) → NEW, aunque la
            # referencie otro consumer (la FK-safety es del script gated §4).
            assert str(vid) in ident["new_vacancies"]
            assert str(vid) not in ident["reused_vacancies"]

    asyncio.run(_on_disposable_db(_run))


def test_manifest_catches_material_corruption():
    """Verificación adversarial 2: corromper columnas MATERIALES (notes, snapshot,
    nota de bookmark, título de la oferta canónica) tras migrar → divergent — el
    oráculo compara material completo, no solo (external_ref, url_normalized, status)."""
    from jobhunt_core import import_portfolio_manifest as man
    from jobhunt_core import import_portfolio_migrate as ipm

    users = [{"external_ref": 1, "applications": [
        {"url": "https://x.ch/a", "status": "applied", "title": "A", "company": "Acme",
         "notes": "n", "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
        {"url": "https://x.ch/b", "status": "saved", "notes": "bm",
         "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
    ], "saved_searches": []}]

    def _corrupt(sql):
        async def _run(factory):
            async with factory() as s:
                report = await ipm.migrate_portfolio(s, users)
                if sql:
                    await s.execute(sa.text(sql))
                return (await man.reconcile(s, users, report))["verdict"]

        return asyncio.run(_on_disposable_db(_run))

    assert _corrupt(None) == "ok"  # baseline
    assert _corrupt("UPDATE applications SET notes = 'X'") == "divergent"
    assert _corrupt(
        "UPDATE applications SET snapshot = jsonb_set(snapshot, '{title}', '\"Z\"')"
    ) == "divergent"
    assert _corrupt(
        "UPDATE profile_vacancy_state SET notes = 'X' WHERE notes IS NOT NULL"
    ) == "divergent"
    assert _corrupt(
        "UPDATE offer_revisions SET content = jsonb_set(content, '{title}', '\"Z\"')"
    ) == "divergent"


def test_manifest_catches_missing_tracking():
    """P1 rev. externa 2: borrar applications+events tras migrar NO puede dar 'ok' —
    la reconciliación cubre las 4 tablas de tracking, no solo búsquedas/vacantes."""
    from jobhunt_core import import_portfolio_manifest as man
    from jobhunt_core import import_portfolio_migrate as ipm

    users = [{"external_ref": 1, "applications": [
        {"url": "https://x.ch/a", "status": "applied", "title": "A",
         "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
    ], "saved_searches": []}]

    async def _run(factory):
        async with factory() as s:
            report = await ipm.migrate_portfolio(s, users)
            await s.execute(sa.text("DELETE FROM application_status_events"))
            await s.execute(sa.text("DELETE FROM applications"))
            manifest = await man.reconcile(s, users, report)
            assert manifest["verdict"] == "divergent"
            assert any("applications" in d for d in manifest["divergences"])
            assert any("events" in d for d in manifest["divergences"])

    asyncio.run(_on_disposable_db(_run))


def test_cross_source_collision_staged():
    """P1 rev. externa 1: una URL de OTRA fuente comparte url_normalized con una del
    portfolio (distinto fragmento) → el sink las fundiría (attach cross-source). Debe
    detectarse como colisión: NO se sintetiza, el durable va a staging, sin vínculo
    a la vacante de la otra fuente."""
    import jobhunt_core.harvest.providers  # noqa: F401 — registra arbeitnow
    from jobhunt_core.harvest.sink import RawListingSink
    from jobhunt_core.harvest.types import RawListing
    from jobhunt_core import import_portfolio as ip
    from jobhunt_core import import_portfolio_migrate as ipm

    users = [{"external_ref": 1, "applications": [
        {"url": "https://spa-other.ch/#/B", "status": "applied", "title": "B",
         "created_at": datetime(2026, 6, 2, tzinfo=timezone.utc)},
    ], "saved_searches": []}]

    async def _run(factory):
        async with factory() as s:
            # Otra fuente importa #/A (misma clave normalizada que #/B).
            src, scope = uuid.uuid4(), uuid.uuid4()
            await s.execute(sa.text(
                "INSERT INTO sources (id, name, tier) VALUES (:i, 'arbeitnow', 0)"),
                {"i": src})
            await s.execute(sa.text(
                "INSERT INTO harvest_scopes (id, source_id, params, tier) "
                "VALUES (:i, :s, '{}'::jsonb, 0)"), {"i": scope, "s": src})
            await s.commit()
            await RawListingSink().handle(s, str(scope), (RawListing(
                external_id="other-A", url="https://spa-other.ch/#/A",
                payload={"title": "Other", "company_name": "X",
                         "description": "d", "tags": []}),))
            await s.commit()
            other_vac = await _count(s, "vacancies")

            report = await ipm.migrate_portfolio(s, users)
            await s.commit()
            # Detectada en la REVALIDACIÓN post-attach (la vacante resultó tener la url
            # de la otra fuente además de la del portfolio): la cadena se REVIRTIÓ.
            assert report["applications"]["collision"] == 1
            assert report["applications"]["applications"] == 0
            assert await _count(s, "vacancies") == other_vac  # ninguna nueva
            assert [r for r in report["staged"] if r["reason"] == "collision"]
            # NINGÚN dato de usuario (application/bookmark) vincula el durable.
            assert await _count(s, "applications") == 0
            assert await _count(s, "profile_vacancy_state") == 0
            # Y NINGÚN artefacto de corpus portfolio-import para #/B (cadena revertida):
            # resolve→None y cero source_listings portfolio-import con esa clave.
            assert await ip.resolve_vacancy_by_url(s, "https://spa-other.ch/#/B") is None
            n_pi = (await s.execute(sa.text(
                "SELECT count(*) FROM source_listings sl JOIN sources s "
                "ON s.id = sl.source_id AND s.name = 'portfolio-import' "
                "WHERE sl.url_normalized = :u"),
                {"u": "https://spa-other.ch/"})).scalar_one()
            assert n_pi == 0

    asyncio.run(_on_disposable_db(_run))


def test_inventory_reused_vacancy():
    """P2 rev. externa 2: si el portfolio importa una URL que YA existe en el corpus
    (misma exacta) bajo otra fuente, el inventario registra el vacancy_id REUTILIZADO
    (elegibilidad de attach: incarnación activa + vacante presentable)."""
    import jobhunt_core.harvest.providers  # noqa: F401
    from jobhunt_core.harvest.sink import RawListingSink
    from jobhunt_core.harvest.types import RawListing
    from jobhunt_core import import_portfolio_manifest as man

    url = "https://reuse.ch/job-1"
    users = [{"external_ref": 1, "applications": [
        {"url": url, "status": "applied", "title": "R",
         "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
    ], "saved_searches": []}]

    async def _run(factory):
        async with factory() as s:
            src, scope = uuid.uuid4(), uuid.uuid4()
            await s.execute(sa.text(
                "INSERT INTO sources (id, name, tier) VALUES (:i, 'arbeitnow', 0)"),
                {"i": src})
            await s.execute(sa.text(
                "INSERT INTO harvest_scopes (id, source_id, params, tier) "
                "VALUES (:i, :s, '{}'::jsonb, 0)"), {"i": scope, "s": src})
            await s.commit()
            await RawListingSink().handle(s, str(scope), (RawListing(
                external_id="reuse-1", url=url,
                payload={"title": "R", "company_name": "X",
                         "description": "d", "tags": []}),))
            await s.commit()
            existing_vid = (
                await s.execute(sa.text("SELECT id FROM vacancies LIMIT 1"))
            ).scalar_one()

            manifest = await man.migrate_and_reconcile(s, users)
            # La misma URL exacta → el sink adjunta la incarnación portfolio a la
            # vacante existente (reutilizada), no crea una nueva.
            assert str(existing_vid) in manifest["identities"]["reused_vacancies"]

    asyncio.run(_on_disposable_db(_run))


def test_manifest_subsecond_last_run_ok():
    """Verificación adversarial: last_notified_at con MICROSEGUNDOS no debe dar falso
    divergent (esperado y destino canonizan el timestamp igual, sin trunc↔round)."""
    from jobhunt_core import import_portfolio_manifest as man

    users = [{"external_ref": 1, "applications": [], "saved_searches": [
        {"name": "s", "filters": '{"q": "x"}', "min_score": 0, "is_active": True,
         "last_notified_at": datetime(2026, 7, 1, 12, 0, 0, 750000, tzinfo=timezone.utc)},
    ]}]

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, users)
            assert manifest["verdict"] == "ok", manifest["divergences"]

    asyncio.run(_on_disposable_db(_run))


def test_manifest_models_sink_quarantine():
    """Verificación adversarial: un durable con url > MAX_URL_LEN (que el sink
    cuarentena → unresolved) NO debe dar falso divergent — el clasificador lo modela."""
    from jobhunt_core import import_portfolio_manifest as man

    long_url = "https://x.ch/" + "a" * 1100  # > 1000
    users = [{"external_ref": 1, "applications": [
        {"url": long_url, "status": "applied", "title": "T",
         "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
    ], "saved_searches": []}]

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, users)
            assert manifest["verdict"] == "ok", manifest["divergences"]
            # El durable no-persistible quedó AUDITADO en staging (expected trae razón).
            assert any("unresolved" in k for k in manifest["staging"]["expected"])

    asyncio.run(_on_disposable_db(_run))


def test_rollback_manifest_exact_pks():
    """Verificación adversarial 3: el manifiesto de rollback (RUNBOOK §3) emite los PKs
    EXACTOS insertados por tabla —incl. las hijas del sink (source_listing_revisions,
    offer_revisions, dedup_candidates, link_evidence)— + new/reused vacancies. La
    FK-safety del borrado es del script gated §4."""
    from jobhunt_core import import_portfolio_manifest as man

    users = _representative_users()

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, users)
            ident = manifest["identities"]
            assert len(ident["source"]) == 1 and len(ident["consumer"]) == 1
            assert len(ident["new_vacancies"]) == 6
            assert len(ident["reused_vacancies"]) == 0
            assert len(ident["applications"]) == 5
            assert len(ident["application_status_events"]) == 5
            assert len(ident["source_listing_incarnations"]) == 6
            assert len(ident["source_listing_revisions"]) == 6
            assert len(ident["offer_revisions"]) == 6
            # dedup_candidates/link_evidence presentes (posiblemente vacías sin drift).
            assert "dedup_candidates" in ident and "link_evidence" in ident

    asyncio.run(_on_disposable_db(_run))


def test_identities_exclude_concurrent_foreign():
    """Verificación adversarial 3 (bis): una vacante de OTRA fuente confirmada en medio
    NO se atribuye a C-4 (la captura es scopeada a portfolio-import, no un diff global
    bajo READ COMMITTED)."""
    import jobhunt_core.harvest.providers  # noqa: F401
    from jobhunt_core.harvest.sink import RawListingSink
    from jobhunt_core.harvest.types import RawListing
    from jobhunt_core import import_portfolio_manifest as man

    users = [{"external_ref": 1, "applications": [
        {"url": "https://x.ch/a", "status": "applied", "title": "A",
         "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
    ], "saved_searches": []}]

    async def _run(factory):
        async with factory() as s:
            # Escritura AJENA (otra fuente, otra URL) confirmada antes de reconciliar.
            src, scope = uuid.uuid4(), uuid.uuid4()
            await s.execute(sa.text(
                "INSERT INTO sources (id, name, tier) VALUES (:i, 'arbeitnow', 0)"),
                {"i": src})
            await s.execute(sa.text(
                "INSERT INTO harvest_scopes (id, source_id, params, tier) "
                "VALUES (:i, :s, '{}'::jsonb, 0)"), {"i": scope, "s": src})
            await s.commit()
            await RawListingSink().handle(s, str(scope), (RawListing(
                external_id="foreign", url="https://foreign.ch/x",
                payload={"title": "F", "company_name": "Y",
                         "description": "d", "tags": []}),))
            await s.commit()
            foreign_vid = str((
                await s.execute(sa.text("SELECT id FROM vacancies LIMIT 1"))
            ).scalar_one())

            manifest = await man.migrate_and_reconcile(s, users)
            ident = manifest["identities"]
            assert foreign_vid not in ident["new_vacancies"]  # no atribuida a C-4
            assert foreign_vid not in ident["reused_vacancies"]
            assert len(ident["new_vacancies"]) == 1  # solo la de x.ch/a

    asyncio.run(_on_disposable_db(_run))


def test_winner_tiebreak_company_deterministic():
    """Verificación adversarial: dos candidaturas reales de la MISMA vacante que
    empatan en fecha/status/url/title/notes pero difieren en company → el ganador (y
    el snapshot) NO depende del orden del lote (tiebreak incluye company)."""
    from jobhunt_core import import_portfolio_migrate as ipm

    same = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
    apps = [
        {"url": "https://x.ch/d", "status": "applied", "title": "T",
         "company": "Acme", "created_at": same},
        {"url": "https://x.ch/d", "status": "applied", "title": "T",
         "company": "Globex", "created_at": same},
    ]

    def _company(order):
        users = [{"external_ref": 1, "applications": order, "saved_searches": []}]

        async def _run(factory):
            async with factory() as s:
                await ipm.migrate_portfolio(s, users)
                await s.commit()
                return (
                    await s.execute(sa.text(
                        "SELECT snapshot->>'company' AS c FROM applications LIMIT 1"))
                ).scalar_one()

        return asyncio.run(_on_disposable_db(_run))

    assert _company(apps) == _company(list(reversed(apps))) == "Globex"


async def _scenario(factory):
    from jobhunt_core import import_portfolio as ip
    from jobhunt_core import import_portfolio_migrate as ipm

    users = _representative_users()

    # --- DRY-RUN REVERSIBLE: migrar en una sesión, verificar checksums NO vacíos
    # (la migración corrió contra datos reales), y ROLLBACK ⇒ nada persiste en
    # NINGUNA tabla que escribe la migración (tracking + corpus global + tenant).
    async with factory() as s:
        await ipm.migrate_portfolio(s, users)
        chk_dry = await ipm.table_checksums(s)
        assert all(t["count"] > 0 for t in chk_dry.values()), chk_dry
        assert all(
            t["checksum"] != ipm.EMPTY_CHECKSUM for t in chk_dry.values()
        ), chk_dry
        await s.rollback()
    async with factory() as s:
        for table in FULL_WRITE_SET:
            assert await _count(s, table) == 0, table  # rollback ⇒ todo descartado

    # --- CUTOVER real: migrar + commit + reconciliar.
    async with factory() as s:
        report = await ipm.migrate_portfolio(s, users)
        await s.commit()
        assert report["users"] == 2
        assert report["applications"] == EXPECTED_APPS, report["applications"]
        assert report["saved_searches"] == EXPECTED_SS, report["saved_searches"]

        # ENUMERACIÓN por razón: identidad contable contra los conteos (nada sin
        # auditar). El dataset deja fuera 1 oferta sin url (unresolved) y 1 búsqueda
        # con filtros inválidos (importada desactivada + enumerada).
        reasons = Counter(r["reason"] for r in report["staged"])
        assert reasons["unresolved"] == report["applications"]["unresolved"]
        assert reasons["invalid_status"] == report["applications"]["invalid_status"]
        assert reasons["collision"] == report["applications"]["collision"]
        assert reasons["no_name"] == report["saved_searches"]["no_name"]
        assert reasons["invalid_filters"] == report["saved_searches"]["invalid_filters"]
        assert reasons == Counter({"unresolved": 1, "invalid_filters": 1})
        unresolved_rec = next(r for r in report["staged"] if r["reason"] == "unresolved")
        assert unresolved_rec["external_ref"] == "1"
        assert unresolved_rec["kind"] == "application"
        assert unresolved_rec["durable"]["title"] == "Sin URL"

        chk1 = await ipm.table_checksums(s)
        for target, rows in EXPECTED_ROWS.items():
            assert chk1[target]["count"] == rows, (target, chk1[target])
            assert chk1[target]["checksum"] != ipm.EMPTY_CHECKSUM, target

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
