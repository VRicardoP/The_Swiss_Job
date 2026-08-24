"""Integración del mapeo de durables del portfolio (C-4, parte 2).

BD DESECHABLE (patrón test_integration_import_portfolio): CREATE DATABASE +
extensión vector + esquema + alembic head, DROP en finally. Cubre: application
con evento inicial, bookmark (con y sin follow_up_date), consolidación por
UNIQUE(profile_id, vacancy_id), idempotencia de re-ejecución, saved_searches
(filters str→JSONB, last_notified_at→last_run_at, dedup por name+filters,
filters inválido → {} con log) y durables sin url/irresolubles. Regresiones
análisis 2 (pérdida silenciosa auditable): coalesce de notes de bookmark,
consolidación por recencia orden-independiente + log de la descartada, dedup
de saved_searches por (name, filters) y duplicado preexistente que no envenena
el re-run. Ejecutar vía core-migrate.
"""

import asyncio
import logging
import os
import uuid
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

URL_APPLIED = "https://jobs.example.ch/applied-1"
URL_SAVED = "https://jobs.example.ch/saved-2"
URL_SAVED_FU = "https://jobs.example.ch/saved-fu-3"
URL_BOTH = "https://jobs.example.ch/both-4"
URL_MULTI = "https://jobs.example.ch/multi-note-5"
URL_TWO_REAL = "https://jobs.example.ch/two-real-6"


def test_migrate_portfolio_durables(caplog):
    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    dbname = f"jobhunt_durables_{uuid.uuid4().hex[:12]}"
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
                await c.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
                await c.execute(
                    sa.text(f'CREATE SCHEMA IF NOT EXISTS "{settings.CORE_DB_SCHEMA}"')
                )

        asyncio.run(bootstrap())
        run_alembic(temp_url, "upgrade", "head")
        asyncio.run(_scenario(factory, caplog))
        asyncio.run(temp_engine.dispose())
    finally:

        async def drop_db():
            async with admin_engine.connect() as c:
                await c.execute(
                    sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
                )
            await admin_engine.dispose()

        asyncio.run(drop_db())


async def _scenario(factory, caplog):
    from jobhunt_core import import_portfolio as ip
    from jobhunt_core import import_portfolio_durables as ipd

    apps = [
        {
            "user_id": 7, "url": URL_APPLIED, "title": "Backend Developer",
            "company": "ACME AG", "description": "Python backend",
            "status": "applied", "notes": "CV enviado", "follow_up_date": None,
            "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
        },
        {
            "user_id": 7, "url": URL_SAVED, "title": "Data Engineer",
            "company": "Beta", "description": None,
            "status": "saved", "notes": "interesante", "follow_up_date": None,
            "created_at": datetime(2026, 6, 2, tzinfo=timezone.utc),
        },
        {
            "user_id": 7, "url": URL_SAVED_FU, "title": "ML Engineer",
            "company": "Gamma", "description": None,
            "status": "saved", "notes": None,
            "follow_up_date": date(2026, 8, 15),
            "created_at": datetime(2026, 6, 3, tzinfo=timezone.utc),
        },
        # CONSOLIDACIÓN: bookmark con follow_up_date + candidatura REAL con la
        # MISMA url → deben fundirse en UNA application (UNIQUE del esquema).
        {
            "user_id": 7, "url": URL_BOTH, "title": "DevOps Engineer",
            "company": "Delta", "description": None,
            "status": "saved", "notes": "recordar ping",
            "follow_up_date": date(2026, 9, 1),
            "created_at": datetime(2026, 6, 4, tzinfo=timezone.utc),
        },
        {
            "user_id": 7, "url": URL_BOTH, "title": "DevOps Engineer",
            "company": "Delta", "description": "IaC",
            "status": "applied", "notes": None, "follow_up_date": None,
            "created_at": datetime(2026, 6, 5, tzinfo=timezone.utc),
        },
        # Sin url → unresolved (staging futuro), no inserta nada.
        {
            "user_id": 7, "url": None, "title": "Sin URL", "company": "X",
            "description": None, "status": "offer", "notes": None,
            "follow_up_date": None,
            "created_at": datetime(2026, 6, 6, tzinfo=timezone.utc),
        },
        # URL jamás sintetizada (p.ej. cuarentena del sink) → unresolved.
        {
            "user_id": 7, "url": "https://nunca.vista/x", "title": "Fantasma",
            "company": "Y", "description": None, "status": "rejected",
            "notes": None, "follow_up_date": None,
            "created_at": datetime(2026, 6, 7, tzinfo=timezone.utc),
        },
    ]
    expected_counts = {
        "applications": 3, "bookmarks": 3, "unresolved": 2,
        "consolidated": 1, "invalid_status": 0, "collision": 0, "no_title": 0,
    }

    async with factory() as s:
        scope_id = await ip.ensure_import_scope(s)
        profile_id = await ipd.provision_profile(s, "7")
        await s.commit()

        # provision_profile IDEMPOTENTE: mismo external_ref → mismo perfil.
        assert await ipd.provision_profile(s, "7") == profile_id

        # --- Parte 1 primero: sintetizar las vacantes de las URLs resolubles
        # (la URL "nunca vista" NO se sintetiza a propósito: simula un durable
        # cuya vacante quedó en cuarentena del sink → resolve devuelve None).
        await ip.synthesize_vacancies(
            s,
            scope_id,
            [
                {k: r[k] for k in ("url", "title", "company", "description")}
                for r in apps[:5]
            ],
        )
        await s.commit()

        counts = await ipd.migrate_applications(s, profile_id, apps)
        await s.commit()
        assert counts == expected_counts
        assert await _count(s, "applications") == 3
        assert await _count(s, "application_status_events") == 3

        vid_applied = await ip.resolve_vacancy_by_url(s, URL_APPLIED)
        vid_saved = await ip.resolve_vacancy_by_url(s, URL_SAVED)
        vid_saved_fu = await ip.resolve_vacancy_by_url(s, URL_SAVED_FU)
        vid_both = await ip.resolve_vacancy_by_url(s, URL_BOTH)
        assert None not in (vid_applied, vid_saved, vid_saved_fu, vid_both)

        # --- applied con url: 1 application + evento inicial 'applied'.
        app = await _application(s, profile_id, vid_applied)
        assert app.status == "applied"
        assert app.notes == "CV enviado"
        assert app.follow_up_date is None
        assert app.snapshot == {
            "title": "Backend Developer", "company": "ACME AG",
            "url": URL_APPLIED, "description": "Python backend",
        }
        assert await _event_statuses(s, app.id) == ["applied"]
        # Sin bookmark: la application no toca profile_vacancy_state.
        assert await _pvs(s, profile_id, vid_applied) is None

        # --- saved SIN follow_up_date: bookmark, NO application.
        pvs = await _pvs(s, profile_id, vid_saved)
        assert pvs is not None and pvs.saved_at is not None
        assert pvs.notes == "interesante"
        assert await _application(s, profile_id, vid_saved) is None

        # --- saved CON follow_up_date: bookmark + application status 'saved'
        # con el follow_up_date (regla C-4: el dato no se pierde).
        pvs = await _pvs(s, profile_id, vid_saved_fu)
        assert pvs is not None and pvs.saved_at is not None
        app = await _application(s, profile_id, vid_saved_fu)
        assert app.status == "saved"
        assert app.follow_up_date == date(2026, 8, 15)
        assert await _event_statuses(s, app.id) == ["saved"]

        # --- CONSOLIDACIÓN: saved+follow_up y 'applied' con la MISMA url →
        # UNA sola application: gana el status REAL, conserva follow_up_date
        # y notes del bookmark; el bookmark en sí también queda.
        app = await _application(s, profile_id, vid_both)
        assert app.status == "applied"
        assert app.follow_up_date == date(2026, 9, 1)
        assert app.notes == "recordar ping"
        assert app.snapshot["description"] == "IaC"  # snapshot del ganador
        assert await _event_statuses(s, app.id) == ["applied"]
        pvs = await _pvs(s, profile_id, vid_both)
        assert pvs is not None and pvs.saved_at is not None

        # --- IDEMPOTENCIA: mismas rows → MISMOS conteos, sin duplicar.
        counts2 = await ipd.migrate_applications(s, profile_id, apps)
        await s.commit()
        assert counts2 == expected_counts
        assert await _count(s, "applications") == 3
        assert await _count(s, "application_status_events") == 3
        assert await _count(s, "profile_vacancy_state") == 3

        # --- saved_searches: filters str→JSONB, last_notified_at→last_run_at,
        # defaults de core0011; filters inválido → {} con log.
        searches = [
            {
                "user_id": 7, "name": "python zurich",
                "filters": '{"q": "python", "location": "Zurich"}',
                "min_score": 60, "is_active": True,
                "last_notified_at": datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
                "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
            },
            {
                "user_id": 7, "name": "rota", "filters": "{no es json",
                "min_score": 0, "is_active": False, "last_notified_at": None,
                "created_at": datetime(2026, 5, 2, tzinfo=timezone.utc),
            },
        ]
        with caplog.at_level(logging.WARNING):
            sc = await ipd.migrate_saved_searches(s, profile_id, searches)
        await s.commit()
        assert sc == {
            "migrated": 2, "existing": 0, "invalid_filters": 1, "no_name": 0,
        }
        assert "filters INVÁLIDO" in caplog.text
        row = (
            await s.execute(
                sa.text(
                    "SELECT filters, min_score, is_active, last_run_at, "
                    "notify_frequency, notify_push, total_matches "
                    "FROM saved_searches WHERE profile_id = :pid AND name = :n"
                ),
                {"pid": profile_id, "n": "python zurich"},
            )
        ).one()
        assert row.filters == {"q": "python", "location": "Zurich"}
        assert row.min_score == 60
        assert row.is_active is True
        assert row.last_run_at == datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        assert row.notify_frequency == "daily"
        assert row.notify_push is True
        assert row.total_matches == 0
        broken = (
            await s.execute(
                sa.text(
                    "SELECT filters, last_run_at FROM saved_searches "
                    "WHERE profile_id = :pid AND name = :n"
                ),
                {"pid": profile_id, "n": "rota"},
            )
        ).one()
        assert broken.filters == {}
        assert broken.last_run_at is None

        # --- IDEMPOTENCIA saved_searches: re-ejecutar no duplica (dedup por
        # (profile_id, name, filters)).
        sc2 = await ipd.migrate_saved_searches(s, profile_id, searches)
        await s.commit()
        assert sc2 == {
            "migrated": 0, "existing": 2, "invalid_filters": 1, "no_name": 0,
        }
        assert await _count(s, "saved_searches") == 2

        # =============================================================
        # REGRESIONES análisis 2 (parte 2): pérdida silenciosa auditable.
        # =============================================================

        # --- P2 notes de bookmark COALESCEN DETERMINISTA por RECENCIA (no por orden
        # del lote — P2 rev. externa): tres saved de la MISMA vacante → gana la nota
        # MÁS RECIENTE no vacía ('nota tardía'), en un solo UPDATE.
        await ip.synthesize_vacancies(
            s, scope_id,
            [{"url": URL_MULTI, "title": "Multi Note", "company": "M",
              "description": None}],
        )
        await s.commit()
        multi = [
            {"user_id": 7, "url": URL_MULTI, "status": "saved", "title": "Multi Note",
             "notes": None, "follow_up_date": None,
             "created_at": datetime(2026, 6, 10, tzinfo=timezone.utc)},
            {"user_id": 7, "url": URL_MULTI, "status": "saved", "title": "Multi Note",
             "notes": "nota buena", "follow_up_date": None,
             "created_at": datetime(2026, 6, 11, tzinfo=timezone.utc)},
            {"user_id": 7, "url": URL_MULTI, "status": "saved", "title": "Multi Note",
             "notes": "nota tardía", "follow_up_date": None,
             "created_at": datetime(2026, 6, 12, tzinfo=timezone.utc)},
        ]
        cm = await ipd.migrate_applications(s, profile_id, multi)
        await s.commit()
        # 3 saved de una vacante → 3 bookmarks, 0 applications (sin follow_up ni real).
        assert cm["bookmarks"] == 3 and cm["applications"] == 0
        vid_multi = await ip.resolve_vacancy_by_url(s, URL_MULTI)
        pvs_multi = await _pvs(s, profile_id, vid_multi)
        assert pvs_multi.notes == "nota tardía"  # la más reciente, determinista

        # --- P3 consolidación por RECENCIA (no por orden del lote): dos candidaturas
        # reales de la MISMA vacante con status distinto; la MÁS RECIENTE va PRIMERA
        # (el orden batch-last elegiría la equivocada). Gana la reciente, y la
        # descartada se LOGUEA (auditable — no pérdida silenciosa).
        await ip.synthesize_vacancies(
            s, scope_id,
            [{"url": URL_TWO_REAL, "title": "Two Real", "company": "T",
              "description": None}],
        )
        await s.commit()
        two_real = [
            {"user_id": 7, "url": URL_TWO_REAL, "status": "rejected", "title": "Two Real",
             "notes": None, "follow_up_date": None,
             "created_at": datetime(2026, 7, 20, tzinfo=timezone.utc)},  # reciente, 1ª
            {"user_id": 7, "url": URL_TWO_REAL, "status": "applied", "title": "Two Real",
             "notes": None, "follow_up_date": None,
             "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},  # antigua, última
        ]
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            cr = await ipd.migrate_applications(s, profile_id, two_real)
        await s.commit()
        assert cr["applications"] == 1 and cr["consolidated"] == 1
        vid_tr = await ip.resolve_vacancy_by_url(s, URL_TWO_REAL)
        app_tr = await _application(s, profile_id, vid_tr)
        assert app_tr.status == "rejected"  # la MÁS RECIENTE, no batch-last ('applied')
        assert await _event_statuses(s, app_tr.id) == ["rejected"]
        assert "DESCARTADA por consolidación" in caplog.text  # 'applied' auditada

        # --- P2 saved_searches: mismo name, filters DISTINTOS = DOS búsquedas
        # legítimas (el origen no impone UNIQUE por name) → AMBAS migran (dedup por
        # name+filters, no solo name — no se pierde una en silencio).
        homonym = [
            {"user_id": 7, "name": "zurich", "filters": '{"q": "python"}',
             "min_score": 0, "is_active": True, "last_notified_at": None},
            {"user_id": 7, "name": "zurich", "filters": '{"q": "rust"}',
             "min_score": 0, "is_active": True, "last_notified_at": None},
        ]
        sc3 = await ipd.migrate_saved_searches(s, profile_id, homonym)
        await s.commit()
        assert sc3["migrated"] == 2 and sc3["existing"] == 0
        assert (
            await s.execute(
                sa.text(
                    "SELECT count(*) FROM saved_searches "
                    "WHERE profile_id = :pid AND name = :n"
                ),
                {"pid": profile_id, "n": "zurich"},
            )
        ).scalar_one() == 2
        # Re-ejecutar: ambas ya existen (dedup name+filters), no duplica.
        sc4 = await ipd.migrate_saved_searches(s, profile_id, homonym)
        await s.commit()
        assert sc4["migrated"] == 0 and sc4["existing"] == 2

        # --- P3 saved_searches: un DUPLICADO PREEXISTENTE (mismo name+filters, p.ej.
        # de un run parcial previo) NO envenena el re-run — .first()/LIMIT 1 en vez
        # de scalar_one_or_none (que lanzaría MultipleResultsFound).
        for _ in range(2):
            await s.execute(
                sa.text(
                    "INSERT INTO saved_searches (id, profile_id, name, filters) "
                    "VALUES (:id, :pid, :n, CAST(:f AS jsonb))"
                ),
                {"id": uuid.uuid4(), "pid": profile_id, "n": "dup",
                 "f": '{"q": "x"}'},
            )
        await s.commit()
        dup_row = [{"user_id": 7, "name": "dup", "filters": '{"q": "x"}',
                    "min_score": 0, "is_active": True, "last_notified_at": None}]
        sc5 = await ipd.migrate_saved_searches(s, profile_id, dup_row)  # no lanza
        await s.commit()
        assert sc5["migrated"] == 0 and sc5["existing"] == 1


async def _application(session, profile_id, vacancy_id):
    return (
        await session.execute(
            sa.text(
                "SELECT id, status, notes, follow_up_date, snapshot "
                "FROM applications "
                "WHERE profile_id = :pid AND vacancy_id = :vid"
            ),
            {"pid": profile_id, "vid": vacancy_id},
        )
    ).one_or_none()


async def _event_statuses(session, application_id) -> list[str]:
    return list(
        (
            await session.execute(
                sa.text(
                    "SELECT status FROM application_status_events "
                    "WHERE application_id = :aid ORDER BY created_at"
                ),
                {"aid": application_id},
            )
        ).scalars()
    )


async def _pvs(session, profile_id, vacancy_id):
    return (
        await session.execute(
            sa.text(
                "SELECT saved_at, notes, dismissed_at FROM profile_vacancy_state "
                "WHERE profile_id = :pid AND vacancy_id = :vid"
            ),
            {"pid": profile_id, "vid": vacancy_id},
        )
    ).one_or_none()


async def _count(session, table: str) -> int:
    return (
        await session.execute(sa.text(f"SELECT count(*) FROM {table}"))
    ).scalar_one()
