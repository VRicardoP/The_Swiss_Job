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


# ----------------------------------------- fronteras de fecha/hora (G1-P2-2/P3-7)


def test_recency_key_mixed_naive_aware_no_typeerror():
    """Regresión G1-P2-2: un grupo (misma vacante) con una candidatura de fecha
    ISO SIN offset (naive) y otra SIN fechas (fallback AWARE datetime.min utc)
    hacía que max()/sorted() con _recency_key lanzara `TypeError: can't compare
    offset-naive and offset-aware datetimes` → transacción del cutover abortada
    (y el mismo crash en _classify_expected, antes de veredicto). _as_datetime
    ancla ahora los naive a UTC (coherente con _ts_key)."""
    from jobhunt_core.import_portfolio_durables import _as_datetime, _recency_key

    rows = [
        {"created_at": datetime(2026, 6, 1, 12, 0), "status": "applied"},  # naive
        {"status": "saved"},  # sin fechas → fallback aware
        {"created_at": "2026-05-01T10:00:00", "status": "applied"},  # ISO sin offset
    ]
    winner = max(rows, key=_recency_key)  # antes: TypeError
    assert winner is rows[0]
    assert sorted(rows, key=_recency_key, reverse=True)[0] is rows[0]
    # El ancla es UTC, la MISMA semántica que _ts_key para naive.
    anchored = _as_datetime(datetime(2026, 6, 1, 12, 0))
    assert anchored is not None and anchored.tzinfo is not None
    assert anchored == datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def test_follow_up_aware_takes_product_tz_date_not_utc():
    """Regresión G1-P3-7: follow_up_date llega como timestamptz normalizado a
    UTC (asyncpg); `.date()` sobre el wall-clock UTC desplazaba un día los
    seguimientos de madrugada — 2026-01-02 00:30 hora suiza (=2026-01-01T23:30Z)
    migraba como 2026-01-01, y reconcile no lo veía (mismo _as_date en ambos
    lados). Ahora los datetimes AWARE se convierten a Europe/Zurich antes de
    tomar la fecha."""
    from jobhunt_core.import_portfolio_durables import _as_date

    madrugada = datetime(2026, 1, 1, 23, 30, tzinfo=timezone.utc)  # 00:30 CH
    assert _as_date(madrugada) == date(2026, 1, 2)  # lo que el usuario ve
    mediodia = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert _as_date(mediodia) == date(2026, 1, 1)
    # date/naive/ISO-date: sin cambio de semántica.
    assert _as_date(date(2026, 1, 1)) == date(2026, 1, 1)
    assert _as_date(datetime(2026, 1, 1, 23, 30)) == date(2026, 1, 1)
    assert _as_date("2026-01-01") == date(2026, 1, 1)


def test_follow_up_iso_datetime_string_no_se_pierde():
    """G2-H-2 (hipótesis CONFIRMADA): un follow_up_date que llegue como
    ISO-string de DATETIME (no de date) lo rechazaba date.fromisoformat y
    `_as_date` devolvía None — el seguimiento se perdía en SILENCIO, y el
    reconciliador tampoco lo veía (mismo helper en ambos lados). Se reencamina
    por _as_datetime, con la MISMA regla de zona del producto (G1-P3-7)."""
    from jobhunt_core.import_portfolio_durables import _as_date

    # 00:30 hora suiza = 2026-01-01T23:30Z → la fecha que el usuario ve.
    assert _as_date("2026-01-01T23:30:00+00:00") == date(2026, 1, 2)
    assert _as_date("2026-01-01T12:00:00+00:00") == date(2026, 1, 1)
    # NAIVE: wall-clock YA RESUELTO, igual que la rama de objeto (G3-P3-1).
    assert _as_date("2026-01-01T00:30:00") == date(2026, 1, 1)
    assert _as_date("ni-fecha-ni-hora") is None


def test_follow_up_naive_da_la_misma_fecha_como_objeto_y_como_cadena():
    """Regresión G3-P3-1: el docstring de `_as_date` fija que un datetime NAIVE
    es wall-clock local YA RESUELTO, y la rama de objeto lo cumple. La rama de
    cadena (G2-H-2) lo reencaminaba por `_as_datetime`, que ancla el naive a
    UTC (regla de _recency_key, para comparar instantes) y luego lo convertía
    a Europe/Zurich: el MISMO instante daba un día MÁS a partir de las 23:00
    según llegara como objeto o como ISO-string — en silencio, y con reconcile
    concordando porque usa el mismo helper en ambos lados."""
    from jobhunt_core.import_portfolio_durables import _as_date

    obj = datetime(2026, 1, 1, 23, 30)
    assert _as_date(obj) == date(2026, 1, 1)
    assert _as_date("2026-01-01T23:30:00") == date(2026, 1, 1)  # antes: 01-02
    assert _as_date("2026-01-01T23:30:00") == _as_date(obj)
    # Los AWARE siguen resolviéndose en la zona del PRODUCTO (G1-P3-7).
    assert _as_date("2026-01-01T23:30:00+00:00") == date(2026, 1, 2)


def test_min_score_decimal_textual_no_degrada_al_valor_menos_restrictivo():
    """Regresión G3-P3-5: la degradación era incoherente (`40.9` → 40 pero
    `'40.0'`/`Decimal('40.0')` → 0) y 0 es el valor MENOS restrictivo: la
    búsqueda pasa a alertar de TODAS las ofertas. Además no dejaba rastro
    alguno, al contrario que su hermano `invalid_filters` del mismo bucle."""
    from decimal import Decimal

    from jobhunt_core.import_portfolio_durables import _as_min_score

    assert _as_min_score(Decimal("40.0")) == (40, False)  # antes: 0 mudo
    assert _as_min_score("40.0") == (40, False)  # antes: 0 mudo
    assert _as_min_score(60) == (60, False)
    assert _as_min_score("60") == (60, False)
    assert _as_min_score(40.9) == (40, False)  # truncado, como el float
    assert _as_min_score(None) == (0, False)  # AUSENTE no es degradación
    # Lo que de verdad no coerciona sigue degradando, pero ahora SE SABE.
    assert _as_min_score(Decimal("NaN")) == (0, True)
    assert _as_min_score(float("nan")) == (0, True)
    assert _as_min_score("sesenta") == (0, True)


def test_g4_min_score_fuera_de_cota_degrada_en_vez_de_matar_el_cutover():
    """Regresión G4-P3-1: el helper blindaba lo NO FINITO pero no la COTA del
    destino (int4), la única que la BD impone. Un valor numéricamente válido y
    fuera de rango se coercionaba SIN marcar degradación, se importaba ACTIVO,
    no iba a staging —el reconciliador tampoco lo veía— y el INSERT abortaba la
    transacción del cutover con `DataError: value out of int32 range`. El
    segundo intento de coerción que añadió G3 AMPLIABA el vector: `1e10`
    degradaba antes a 0 y pasó a producir el int que revienta."""
    from decimal import Decimal

    from jobhunt_core.import_portfolio_durables import _as_min_score

    for valor in (3_000_000_000, "3000000000", Decimal("3e9"), 3e9):
        assert _as_min_score(valor) == (0, True), valor  # antes: (3000000000, False)
    # Segundo grado, sin crash: dentro de int4 pero fuera del contrato de la
    # API (ge=0, le=100) — se importaba ACTIVA y muda.
    assert _as_min_score(101) == (0, True)
    assert _as_min_score(-1) == (0, True)
    # Las cotas siguen siendo válidas y NO degradan.
    assert _as_min_score(0) == (0, False)
    assert _as_min_score(100) == (100, False)


def test_min_score_invalido_migra_desactivada_y_enumerada_en_staging():
    """G3-P3-5 (2ª mitad): un min_score irrecuperable ya no se cuela como 0
    ACTIVO (alertar de TODO) sin rastro — se importa DESACTIVADA y el durable
    ORIGINAL se enumera en staging, el mismo patrón que invalid_filters. El
    lado ESPERADO del reconciliador lo espeja, o el cutover divergiría."""
    import asyncio as _asyncio

    from jobhunt_core.import_portfolio_durables import migrate_saved_searches
    from jobhunt_core.import_portfolio_manifest import _classify_expected
    from jobhunt_core.profiles import ensure_consumer, upsert_profile
    from jobhunt_core.tests.test_integration_migration_rehearsal_portfolio import (
        _on_disposable_db,
    )

    durable = {
        "name": "umbral roto", "filters": {"q": "python"},
        "min_score": "sesenta", "is_active": True,
    }

    async def _run(factory):
        async with factory() as s:
            cid = await ensure_consumer(s, "portfolio")
            pid = await upsert_profile(s, cid, "g3-minscore")
            staging: list = []
            counts = await migrate_saved_searches(s, pid, [durable], staging=staging)
            await s.commit()
            assert counts["migrated"] == 1
            assert staging == [
                {"kind": "saved_search", "reason": "invalid_min_score",
                 "durable": durable}
            ]  # antes: [] — degradación MUDA
            row = (
                await s.execute(
                    sa.text(
                        "SELECT min_score, is_active FROM saved_searches "
                        "WHERE profile_id = :p"
                    ),
                    {"p": pid},
                )
            ).one()
            assert row.min_score == 0
            assert row.is_active is False  # antes: True (alertaba de TODO)

            # El lado ESPERADO del reconciliador coincide, entrada a entrada.
            expected = await _classify_expected(
                s, [{"external_ref": "g3-minscore", "saved_searches": [durable]}]
            )
            assert expected["staged"][
                ("invalid_min_score", "g3-minscore", "umbral roto")
            ] == 1
            assert [t[3:5] for t in expected["saved_searches"]] == [(0, False)]

    _asyncio.run(_on_disposable_db(_run))


def test_escalar_no_json_del_durable_no_mata_la_transaccion_del_cutover():
    """Regresión G3-P3-2: la frontera que decide si un durable es sintetizable
    serializa el payload con `canonical_payload` (que lleva `default=str`), así
    que ACEPTA un Decimal —lo que asyncpg entrega SIEMPRE para una columna
    numeric— o un `date` en company/description; el ESCRITOR del durable no lo
    llevaba y reventaba con TypeError en el INSERT de `applications`, matando
    la transacción ENTERA del cutover, y el reconciliador cometía el mismo
    error en el lado esperado (`_pg_text`), así que ni siquiera había
    veredicto. Las cuatro serializaciones a jsonb quedan alineadas."""
    import asyncio as _asyncio
    from decimal import Decimal

    from jobhunt_core import import_portfolio as ip
    from jobhunt_core.import_portfolio_manifest import (
        _canon, _pg_text, migrate_and_reconcile,
    )
    from jobhunt_core.tests.test_integration_migration_rehearsal_portfolio import (
        _on_disposable_db,
    )

    durables = [
        {"url": "https://g3dec.example.ch/1", "title": "T1",
         "company": Decimal("1.5"), "description": None, "status": "applied",
         "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
        {"url": "https://g3dt.example.ch/1", "title": "T2", "company": "A",
         "description": date(2026, 1, 1), "status": "applied",
         "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
    ]
    # La frontera los acepta: si el escritor no los acepta, el cutover muere.
    for d in durables:
        assert ip.durable_synthesizable(d) == (True, None)
    # El lado ESPERADO lee lo que el destino GUARDA (jsonb ->> del str).
    assert _pg_text(Decimal("1.5")) == "1.5"  # antes: TypeError
    assert _pg_text(date(2026, 1, 1)) == "2026-01-01"  # antes: TypeError
    assert _canon({"x": Decimal("1.5")}) == '{"x": "1.5"}'  # antes: TypeError

    async def _run(factory):
        async with factory() as s:
            users = [{
                "external_ref": 1, "applications": durables,
                "saved_searches": [
                    {"name": "f", "filters": {"min": Decimal("2.5")},
                     "min_score": 60, "is_active": True},
                ],
            }]
            report = await migrate_and_reconcile(s, users)  # antes: TypeError
            assert report["verdict"] == "ok", report["divergences"]

    _asyncio.run(_on_disposable_db(_run))


def test_json_safe_no_pierde_entradas_aunque_la_clave_desambiguada_colisione():
    """Regresión G3-P3-3: el desambiguador de G2-H-1 no iteraba, así que si la
    clave YA sufijada existía en la salida la entrada se pisaba igual que antes
    del fix (3 claves → 2). Y no degrada «solo la auditoría»: `_json_safe` sanea
    también `filters` y el `snapshot`, que son DATOS DE PRODUCTO."""
    from jobhunt_core.import_portfolio_durables import _json_safe

    # 'a\ufffd' y 'a\x00' COLAPSAN a la misma clave tras el saneo, y la clave
    # desambiguada ('a\ufffd#2') YA existe: el sufijo colisiona a su vez.
    entrada = {"a\ufffd": 1, "a\ufffd#2": 2, "a\x00": 3}
    salida = _json_safe(entrada)
    assert len(salida) == 3  # antes: 2 — el valor 2 se perdía
    assert sorted(salida.values()) == [1, 2, 3]
    assert salida["a\ufffd"] == 1 and salida["a\ufffd#2"] == 2
    assert salida["a\ufffd#3"] == 3  # el desambiguador ITERA hasta hueco


def test_saved_search_filters_dict_y_last_run_at_del_extractor():
    """Regresión G1 H-3 (contrato del extractor): la columna origen `filters` es
    JSONB real — el driver entrega un DICT, y json.loads(dict) → TypeError hacía
    que TODAS las búsquedas migraran vacías y DESACTIVADAS; y la columna real se
    llama `last_run_at` (no `last_notified_at`) → NULL para todas. El
    reconciliador no lo cazaba: cometía el mismo error en el lado esperado.
    Ahora se aceptan ambas formas y ambas claves."""
    import asyncio as _asyncio

    from jobhunt_core.import_portfolio_durables import migrate_saved_searches
    from jobhunt_core.profiles import ensure_consumer, upsert_profile
    from jobhunt_core.tests.test_integration_migration_rehearsal_portfolio import (
        _on_disposable_db,
    )

    async def _run(factory):
        async with factory() as s:
            cid = await ensure_consumer(s, "portfolio")
            pid = await upsert_profile(s, cid, "h3-user")
            staging: list = []
            counts = await migrate_saved_searches(
                s, pid,
                [{
                    "name": "b1", "filters": {"q": "python"}, "min_score": 60,
                    "is_active": True,
                    "last_run_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
                }],
                staging=staging,
            )
            await s.commit()
            assert counts["migrated"] == 1
            assert counts["invalid_filters"] == 0  # antes: 1 (dict → TypeError)
            assert staging == []
            row = (
                await s.execute(
                    sa.text(
                        "SELECT filters, is_active, last_run_at FROM saved_searches "
                        "WHERE profile_id = :p"
                    ),
                    {"p": pid},
                )
            ).one()
            assert row.filters == {"q": "python"}  # los filtros REALES
            assert row.is_active is True
            assert row.last_run_at == datetime(2026, 6, 1, tzinfo=timezone.utc)

    _asyncio.run(_on_disposable_db(_run))
