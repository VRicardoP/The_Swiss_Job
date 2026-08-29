"""Harness GATE-SOMBRA B-05 (shadow/gate) contra Postgres real.

DoD (CONTRATOS_FASE_B §6/§7): (a) contador de N=7 ciclos CONSECUTIVOS con
secuencias verde/rojo/reset CALCULABLES A MANO (siembra directa de
shadow_cycle_metrics): un ciclo en rojo o SIN COMPUTAR resetea, las
[alerta] no; informe legible; (b) alertas de slot con umbrales de §6 —
consumidor parado > 30 min (simulado con updated_at viejo) y retención WAL
(umbral forzado bajo), como logger.error persistente; (c) run_cycle
end-to-end sobre fixture pequeño: proyecta + computa + purga + evalúa,
idempotente/re-entrante y con single-flight propio; (d) las tareas Celery
registradas, enrutadas SOLO a colas core.* y con las cadencias de B-05 en
el beat; (e) el DoD del ticket: UN rollback/replay COMPLETO ejecutado
contra la BD desechable (staging truncado, fuentes legacy desactivadas y
vacantes archivadas, slot re-creado, re-backfill consistente == legacy, y
`public` INTACTO) + simulacro de consumidor caído 30 min con alerta y
recuperación sin pérdida (kill de la conexión + reanudación desde
last_applied_lsn — el patrón del test kill −9 de B-01).

Aislamiento (patrón B-02/B-04): BD DESECHABLE (jobhunt_gate_<hex>) migrada
a head con MINI-tablas legacy fixture en SU `public` (jobs, users,
user_profiles, match_results) y slots de test propios con DROP garantizado
en el finally — estos tests JAMÁS tocan el `public` compartido ni el slot
real `jobhunt_shadow`. Ejecutar vía core-migrate.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import embeddings, matching
from jobhunt_core import profiles as core_profiles
from jobhunt_core.config import settings
from jobhunt_core.shadow import gate, labels, metrics, projector
from jobhunt_core.shadow.capture import DEFAULT_TABLES, ShadowCapture
from jobhunt_core.tests.alembic_runner import run_alembic
from jobhunt_core.tests.test_integration_capture import (
    _drop_slot,
    _wait_slot_released,
    _wal_level,
)
from jobhunt_core.tests.test_integration_matching import DirectionalBackend

_ADMIN = os.getenv("CORE_ADMIN_DATABASE_URL")
S = settings.CORE_DB_SCHEMA
SHA = "b" * 40

pytestmark = [
    pytest.mark.skipif(
        not _ADMIN, reason="requiere BD (ejecutar vía core-migrate)"
    ),
    pytest.mark.skipif(
        bool(_ADMIN) and _wal_level() != "logical",
        reason="requiere wal_level=logical (imagen postgres-core, B-01)",
    ),
]


@pytest.fixture(scope="module")
def gate_db():
    """BD desechable: core migrado a head + mini-tablas legacy en SU public."""
    dbname = f"jobhunt_gate_{uuid.uuid4().hex[:12]}"
    parts = urlsplit(_ADMIN)
    admin_engine = sa.create_engine(
        _ADMIN, poolclass=sa.pool.NullPool, isolation_level="AUTOCOMMIT"
    )
    with admin_engine.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{dbname}"'))
    db_url = urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))
    async_url = db_url.replace("postgresql://", "postgresql+asyncpg://")
    capture_pw = os.getenv("CORE_CAPTURE_PASSWORD", "jobhunt_capture_dev")
    capture_dsn = urlunsplit(
        (
            parts.scheme,
            f"jobhunt_capture:{capture_pw}@{parts.hostname}:{parts.port}",
            f"/{dbname}",
            "",
            "",
        )
    )
    engine = sa.create_engine(db_url, poolclass=sa.pool.NullPool)
    try:
        with engine.begin() as c:
            c.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            c.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{S}"'))
            c.execute(
                sa.text(
                    "CREATE TABLE public.jobs ("
                    "hash varchar(32) PRIMARY KEY, title varchar(500), "
                    "company varchar(300), description text, "
                    "url varchar(2048), source varchar(50), "
                    "tags jsonb NOT NULL DEFAULT '[]'::jsonb, "
                    "is_active boolean NOT NULL DEFAULT true, "
                    "duplicate_of varchar(32), content_hash varchar(32), "
                    "first_seen_at timestamptz NOT NULL DEFAULT now())"
                )
            )
            c.execute(
                sa.text(
                    "CREATE TABLE public.users ("
                    "id uuid PRIMARY KEY, email varchar(320), "
                    "hashed_password varchar(128), "
                    "is_active boolean NOT NULL DEFAULT true)"
                )
            )
            c.execute(
                sa.text(
                    "CREATE TABLE public.user_profiles ("
                    "id uuid PRIMARY KEY, user_id uuid, title varchar(200), "
                    "cv_text text, skills jsonb NOT NULL DEFAULT '[]'::jsonb, "
                    "updated_at timestamptz NOT NULL DEFAULT now())"
                )
            )
            c.execute(
                sa.text(
                    "CREATE TABLE public.match_results ("
                    "user_id uuid NOT NULL, job_hash varchar(32) NOT NULL, "
                    "feedback varchar(20), score_final float NOT NULL DEFAULT 0)"
                )
            )
        run_alembic(async_url, "upgrade", "head")
        yield {
            "core_dsn": db_url,
            "async_url": async_url,
            "capture_dsn": capture_dsn,
        }
    finally:
        engine.dispose()
        with admin_engine.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
        admin_engine.dispose()


@pytest.fixture()
def db(gate_db, monkeypatch):
    """Factory async sobre la BD desechable + settings parcheados (el gate,
    el proyector y las tareas crean sus engines desde settings)."""
    monkeypatch.setattr(settings, "CORE_DATABASE_URL", gate_db["async_url"])
    engine = create_async_engine(
        gate_db["async_url"],
        poolclass=sa.pool.NullPool,
        connect_args={"server_settings": {"search_path": f"{S}, public"}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory

    async def cleanup():
        async with engine.begin() as c:
            # core0033 (G9 P3-A) prohíbe TRUNCATE de los pares y de los sellos
            # mientras haya alguna cohorte CONGELADA — y estos tests sellan cohortes.
            # Se desmonta la guarda con el mismo idioma que las de core0025/0026 en el
            # resto de fixtures (DDL del owner, con rastro) y se vuelve a montar.
            for tabla in ("labeled_dedup_pairs", "labeled_dedup_cohorts"):
                await c.execute(
                    sa.text(f"ALTER TABLE {tabla} DISABLE TRIGGER {tabla}_truncate_guard")
                )
            await c.execute(
                sa.text(
                    "TRUNCATE shadow_change_log, shadow_projection_batches, "
                    "shadow_cycle_metrics, labeled_dedup_pairs, "
                    "labeled_dedup_cohorts, shadow_capture_state"
                )
            )
            for tabla in ("labeled_dedup_pairs", "labeled_dedup_cohorts"):
                await c.execute(
                    sa.text(f"ALTER TABLE {tabla} ENABLE ALWAYS TRIGGER {tabla}_truncate_guard")
                )
            await c.execute(sa.text("TRUNCATE integration_outbox CASCADE"))
            await c.execute(
                sa.text(
                    "TRUNCATE consumers, sources, vacancies, scoring_policies, "
                    "embedding_models CASCADE"
                )
            )
            await c.execute(
                sa.text(
                    "TRUNCATE public.jobs, public.users, public.user_profiles, "
                    "public.match_results"
                )
            )
        await engine.dispose()

    asyncio.run(cleanup())


@pytest.fixture()
def capture(gate_db):
    """Factory de ShadowCapture con slot único por test y DROP garantizado
    (patrón B-01: un slot huérfano retiene WAL y bloquea el DROP DATABASE)."""
    slot = f"gate_fx_{uuid.uuid4().hex[:10]}"
    instances: list[ShadowCapture] = []

    def make(**kwargs) -> ShadowCapture:
        cap = ShadowCapture(
            gate_db["capture_dsn"],
            gate_db["core_dsn"],
            slot=slot,
            tables=kwargs.pop("tables", DEFAULT_TABLES),
            **kwargs,
        )
        instances.append(cap)
        return cap

    try:
        yield make, slot
    finally:
        for cap in instances:
            cap.close()
        _drop_slot(slot)


def _run(coro):
    return asyncio.run(coro)


def _rows(factory, sql, **params):
    async def go():
        async with factory() as s:
            return (await s.execute(sa.text(sql), params)).all()

    return asyncio.run(go())


def _scalar(factory, sql, **params):
    async def go():
        async with factory() as s:
            return (await s.execute(sa.text(sql), params)).scalar()

    return asyncio.run(go())


def _exec(factory, sql, params=None):
    async def go():
        async with factory() as s:
            await s.execute(sa.text(sql), params or {})
            await s.commit()

    asyncio.run(go())


def _health(factory, slot, **kwargs):
    async def go():
        async with factory() as s:
            return await gate.check_slot_health(s, slot=slot, **kwargs)

    return _run(go())


def _status(factory, now=None):
    async def go():
        async with factory() as s:
            return await gate.gate_status(s, now=now)

    return _run(go())


def _report(factory, now=None):
    async def go():
        async with factory() as s:
            return await gate.render_gate_report(s, now=now)

    return _run(go())


def _stream_until(cap: ShadowCapture, predicate, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        cap.stream(max_seconds=0.5)
    pytest.fail(f"timeout de {timeout}s esperando cambios del stream")


def _wait_slot_active(factory, slot, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _scalar(
            factory,
            "SELECT active FROM pg_replication_slots WHERE slot_name = :n",
            n=slot,
        ):
            return
        time.sleep(0.2)
    raise RuntimeError(f"slot {slot} no llegó a activarse en {timeout}s")


# ------------------------------------------------------------- constructores


def _seed_legacy_job(factory, h, source="legacyfx", active=True):
    # first_seen_at 2h atrás (G1 H-7): supera la gracia de alta de 1h del
    # minuendo de perdida — el job cuenta como vivo ingerible, como siempre.
    _exec(
        factory,
        "INSERT INTO public.jobs (hash, title, company, description, url, "
        "source, tags, is_active, content_hash, first_seen_at) VALUES "
        "(:h, 'Backend Dev', 'ACME AG', 'python backend', :u, :s, "
        "CAST('[\"py\"]' AS jsonb), :a, :ch, now() - interval '2 hours')",
        {"h": h, "u": f"https://fx/{h}", "s": source, "a": active, "ch": f"c-{h}"},
    )


def _seed_legacy_user(factory, uid, active=True):
    _exec(
        factory,
        "INSERT INTO public.users (id, email, hashed_password, is_active) "
        "VALUES (:i, :e, '$2b$12$secreto', :a)",
        {"i": uid, "e": f"{uid}@example.com", "a": active},
    )


def _seed_legacy_profile(factory, pid, uid):
    _exec(
        factory,
        "INSERT INTO public.user_profiles (id, user_id, title, cv_text, skills) "
        "VALUES (:i, :u, 'dev', 'mi cv con python', CAST('[\"python\"]' AS jsonb))",
        {"i": pid, "u": uid},
    )


def _seed_legacy_result(factory, user_id, job_hash, score, feedback=None):
    _exec(
        factory,
        "INSERT INTO public.match_results (user_id, job_hash, feedback, "
        "score_final) VALUES (:u, :j, :f, :s)",
        {"u": user_id, "j": job_hash, "f": feedback, "s": score},
    )


_LSN_SEQ = iter(range(50_000, 90_000))


def _seed_staging(factory, changes):
    """Staging SINTÉTICO (patrón B-02): [(src_table, op, pk, payload)]."""
    rows = [
        {"l": next(_LSN_SEQ), "t": t, "o": op, "p": pk, "j": json.dumps(payload)}
        for (t, op, pk, payload) in changes
    ]

    async def go():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO shadow_change_log "
                    "(lsn, seq_in_tx, src_table, op, pk, payload) "
                    "VALUES (:l, 0, :t, :o, :p, CAST(:j AS jsonb)) "
                    "ON CONFLICT (lsn, seq_in_tx) DO NOTHING"
                ),
                rows,
            )
            await s.commit()

    _run(go())


def _job_payload(pk, source):
    """Payload de jobs con TODAS las columnas de contenido (§3)."""
    return {
        "title": "Backend Dev", "company": "ACME AG",
        "description": f"python backend {pk}", "tags": ["py"],
        "location": "Zurich", "canton": "ZH", "language": "en",
        "seniority": "senior", "contract_type": "permanent", "remote": False,
        "salary_min_chf": 100000, "salary_max_chf": 130000,
        "salary_original": "100k-130k CHF", "salary_currency": "CHF",
        "salary_period": "year", "url": f"https://fx/{pk}", "source": source,
        "is_active": True, "duplicate_of": None, "content_hash": f"c-{pk}",
    }


def _mk_profile(factory, external_ref):
    async def go():
        async with factory() as s:
            cid = await core_profiles.ensure_consumer(s, projector.SHADOW_CONSUMER)
            pid = await core_profiles.upsert_profile(s, cid, external_ref)
            await s.commit()
            return pid

    return _run(go())


def _mk_frozen_set(factory, pid, judgments):
    async def go():
        async with factory() as s:
            sid = await labels.create_set(s, pid, "ronda-gate")
            for ref, rel in judgments.items():
                await labels.add_judgment(s, sid, ref, rel)
            await labels.freeze_set(s, sid)
            await s.commit()
            return sid

    return _run(go())


def _seed_model_policy(factory):
    async def go():
        async with factory() as s:
            mid = await embeddings.register_model(
                s, f"modelo-{uuid.uuid4().hex[:6]}", SHA
            )
            polid = await matching.ensure_policy(s, "cosine", "v1")
            await s.commit()
            return mid, polid

    return _run(go())


# ------------------------------------------------------ contador de N=7 (§6)

# Ciclos FIJOS e inyectados: last closed = 2026-07-19 con now = 20/07 12:00.
GNOW = datetime(2026, 7, 20, 12, 0, tzinfo=metrics.CYCLE_TZ)
G0 = date(2026, 7, 19)


def _seed_metric(factory, cycle, metric, scope, value, details=None, sealed=True):
    _exec(
        factory,
        "INSERT INTO shadow_cycle_metrics (cycle_id, metric, scope, value, "
        "details, finished_at) VALUES (:c, :m, :s, :v, CAST(:d AS jsonb), "
        "CASE WHEN :f THEN now() END) "
        "ON CONFLICT (cycle_id, metric, scope) DO UPDATE SET "
        "value = EXCLUDED.value, details = EXCLUDED.details, "
        "finished_at = EXCLUDED.finished_at",
        {"c": cycle, "m": metric, "s": scope, "v": value,
         "d": json.dumps(details or {}), "f": sealed},
    )


def _freeze_holdout(factory, when):
    """Congela la cohorte del gate con frozen_at=`when` (elegibilidad,
    auditoría Nº2 B-3). El arnés RE-congela con timestamps distintos, cosa
    que el sello de core0026 prohíbe a la aplicación: se puentea con DDL de
    OWNER (DISABLE TRIGGER), el límite declarado en la migración — esto es
    exactamente lo que el guard NO cubre y el test lo usa a sabiendas.
    Manifest no vacío: el getter fail-closed ignora sellos sin acta."""
    _exec(
        factory,
        "ALTER TABLE labeled_dedup_cohorts "
        "DISABLE TRIGGER labeled_dedup_cohorts_frozen_guard",
    )
    _exec(
        factory,
        "INSERT INTO labeled_dedup_cohorts (source, frozen_at, manifest) "
        "VALUES (:src, :ts, '{\"test\": true}'::jsonb) "
        "ON CONFLICT (source) DO UPDATE SET frozen_at = :ts",
        {"src": labels.DEDUP_EVAL_COHORT, "ts": when},
    )
    _exec(
        factory,
        "ALTER TABLE labeled_dedup_cohorts "
        "ENABLE ALWAYS TRIGGER labeled_dedup_cohorts_frozen_guard",
    )


def _seed_green_cycle(factory, cycle, scope="profile:aaaa"):
    """Ciclo TODO en verde con los umbrales RATIFICADOS de §6: ndcg 0.90 >=
    max(0.60, 0.70−0.05); fn 0 en modo estricto; dedup 1.0; perdida 0;
    outbox 10 <= 900; outbox_dead 0 (P2-6); latencia 20 <= 3600 (umbrales 2026-08-22); alertas en
    reposo."""
    rows = [
        ("ndcg@10", scope, 0.90, {}),
        ("ndcg@10_legacy", scope, 0.70, {}),
        ("falsos_negativos", scope, 0.0, {"modo": "estricto_0"}),
        ("overlap@10", scope, 0.4, {}),
        ("labels_ready", "global", 1, {}),  # precondición del oráculo (P1-2)
        ("dedup_precision", "global", 1.0, {}),
        ("dedup_recall", "global", 1.0, {}),
        ("perdida", "global", 0, {}),
        ("no_ingeribles", "global", 0, {}),
        ("outbox_lag_p99", "global", 10.0, {"samples_count": 5, "no_data": False}),
        ("outbox_dead", "global", 0, {"dead_actual": 0}),  # P2-6: sin dead
        ("latencia_p95", "global", 20.0, {"lotes": 3}),
        ("coste", "global", 5.0, {}),
        ("reenlace_pct", "global", 0.0, {}),
    ]
    for m, sc, v, d in rows:
        _seed_metric(factory, cycle, m, sc, v, d)


def test_gate_counter_sequences_green_red_and_reset(db):
    factory = db
    # BD virgen: contador a 0, sin ciclos que listar (y sin cohorte
    # congelada: holdout_frozen_at None).
    empty = _status(factory, now=GNOW)
    assert empty == {
        "paradas_declaradas": [],
        "max_paradas": 3,
        "span_dias": None,
        "max_span_dias": 14,
        "consecutive_ok": 0, "required": 7, "gate_passed": False,
        "last_cycle": G0.isoformat(), "holdout_frozen_at": None,
        "per_cycle": [],
    }
    # Cohorte congelada ANTES de la ventana más vieja: todos elegibles.
    _freeze_holdout(factory, datetime(2026, 7, 1, tzinfo=metrics.CYCLE_TZ))

    # 7 ciclos CONSECUTIVOS en verde (G0-6 .. G0) ⇒ GATE superado.
    for i in range(7):
        _seed_green_cycle(factory, G0 - timedelta(days=i))
    st = _status(factory, now=GNOW)
    assert st["consecutive_ok"] == 7 and st["gate_passed"] is True
    assert st["last_cycle"] == "2026-07-19"
    assert len(st["per_cycle"]) == 7
    assert all(e["ok"] and e["computado"] for e in st["per_cycle"])
    assert st["per_cycle"][0]["cycle"] == "2026-07-19"  # del último hacia atrás
    assert st["per_cycle"][6]["cycle"] == "2026-07-13"
    text = _report(factory, now=GNOW)
    assert "GATE-SOMBRA SUPERADO" in text and "7/7" in text

    # Un [gate] en ROJO (perdida=1 en G0-3) RESETEA: a mano quedan 3 verdes
    # consecutivos (G0, G0-1, G0-2).
    _seed_metric(factory, G0 - timedelta(days=3), "perdida", "global", 1)
    st = _status(factory, now=GNOW)
    assert st["consecutive_ok"] == 3 and st["gate_passed"] is False
    assert st["per_cycle"][3] == {
        "cycle": "2026-07-16", "computado": True, "recomputado": False,
        "ok": False, "gates_rojos": ["perdida"], "alertas": [],
        "elegible": True, "parada_declarada": False,
    }

    # Las [alerta] NO resetean (§6): no_ingeribles > 0 y reenlace > 5% en G0
    # disparan, y el contador SIGUE en 3.
    _seed_metric(factory, G0, "no_ingeribles", "global", 5)
    _seed_metric(factory, G0, "reenlace_pct", "global", 0.5)
    st = _status(factory, now=GNOW)
    assert st["consecutive_ok"] == 3
    assert st["per_cycle"][0]["ok"] is True
    assert st["per_cycle"][0]["alertas"] == ["no_ingeribles", "reenlace_pct"]

    # Un ciclo SIN COMPUTAR también resetea: G0-1 se vacía ⇒ solo G0 cuenta.
    _exec(
        factory,
        "DELETE FROM shadow_cycle_metrics WHERE cycle_id = :c",
        {"c": G0 - timedelta(days=1)},
    )
    st = _status(factory, now=GNOW)
    assert st["consecutive_ok"] == 1
    assert st["per_cycle"][1] == {
        "cycle": "2026-07-18", "computado": False, "recomputado": False,
        "ok": False, "gates_rojos": [], "alertas": [],
        "elegible": True, "parada_declarada": False,
    }

    # Una fila SOLO del muestreador (finished_at NULL, placeholder) NO es un
    # ciclo computado: el contador no cambia.
    _seed_metric(
        factory, G0 - timedelta(days=1), "outbox_lag_p99", "global",
        metrics.NO_DATA_VALUE, {"samples": []}, sealed=False,
    )
    st = _status(factory, now=GNOW)
    assert st["consecutive_ok"] == 1
    assert st["per_cycle"][1]["computado"] is False

    text = _report(factory, now=GNOW)
    assert "consecutivos OK: 1/7" in text
    assert "EN CURSO (faltan 6)" in text
    assert "SIN COMPUTAR" in text
    assert "gates: perdida" in text
    assert "alertas: no_ingeribles, reenlace_pct" in text


def test_gate_counter_ignores_recomputed_cycle(db):
    """Regresión P1-4 (rev. externa): un ciclo sellado y RECOMPUTADO con
    force (details.recomputed_at — p.ej. un rojo reescrito a verde con
    estado posterior) NO cuenta para la racha aunque TODOS sus gates estén
    en verde: resetea igual que un rojo, y el informe lo señala."""
    factory = db
    _freeze_holdout(factory, datetime(2026, 7, 1, tzinfo=metrics.CYCLE_TZ))
    for i in range(3):
        _seed_green_cycle(factory, G0 - timedelta(days=i))
    assert _status(factory, now=GNOW)["consecutive_ok"] == 3

    # G0-1: TODO en verde pero con el sello que deja compute_cycle(force=True)
    # sobre un ciclo sellado — exactamente la reescritura del revisor.
    _exec(
        factory,
        "UPDATE shadow_cycle_metrics SET details = details || "
        "jsonb_build_object('recomputed_at', '2026-07-20T10:00:00+00:00') "
        "WHERE cycle_id = :c",
        {"c": G0 - timedelta(days=1)},
    )
    st = _status(factory, now=GNOW)
    assert st["consecutive_ok"] == 1  # G0 verde; G0-1 recomputado CORTA
    entry = st["per_cycle"][1]
    assert entry["recomputado"] is True
    assert entry["computado"] is True
    assert entry["ok"] is False
    assert entry["gates_rojos"] == []  # en verde… y aun así no computable
    text = _report(factory, now=GNOW)
    assert "RECOMPUTADO" in text and "no computable para la racha" in text


def test_gate_counter_ciclo_anterior_al_congelado_es_inelegible(db):
    """Regresión auditoría Nº2 (2026-08-23, BLOQUEANTE 3): un ciclo VERDE y
    sellado cuya ventana empezó ANTES del congelado del holdout no puede
    sumar — el corte es un dato PERSISTIDO (labeled_dedup_cohorts.frozen_at)
    aplicado por gate_status, no una nota de documentación. Sin cohorte
    congelada, NINGÚN ciclo es elegible (exactamente el estado que anuló el
    ciclo mixto del 2026-08-23)."""
    factory = db
    for i in range(3):
        _seed_green_cycle(factory, G0 - timedelta(days=i))

    # Sin congelado: 3 verdes sellados y aun así 0/7.
    st = _status(factory, now=GNOW)
    assert st["holdout_frozen_at"] is None
    assert st["consecutive_ok"] == 0
    assert all(e["elegible"] is False for e in st["per_cycle"])

    # Congelado DENTRO de la ventana de G0-1 (después de su inicio 06:00):
    # solo G0 (empieza el 19 a las 06:00) es posterior ⇒ cuenta 1.
    _freeze_holdout(
        factory, datetime(2026, 7, 18, 12, 0, tzinfo=metrics.CYCLE_TZ)
    )
    st = _status(factory, now=GNOW)
    assert st["consecutive_ok"] == 1
    assert st["per_cycle"][0]["elegible"] is True   # G0
    assert st["per_cycle"][1]["elegible"] is False  # G0-1: ventana mixta
    assert st["per_cycle"][1]["ok"] is True         # verde… pero no computa

    # Congelado anterior a todas las ventanas: las 3 cuentan.
    _freeze_holdout(factory, datetime(2026, 7, 1, tzinfo=metrics.CYCLE_TZ))
    assert _status(factory, now=GNOW)["consecutive_ok"] == 3


# ------------------------------------------------------ alertas de slot (§6)


def test_slot_health_consumer_stopped_over_30min_alerts(capture, db, caplog):
    """Umbral de §6: slot INACTIVO con updated_at viejo (> 30 min) ⇒ ALERTA
    persistente (logger.error). Activo o parado reciente: sin alerta."""
    make, slot = capture
    factory = db
    _seed_legacy_job(factory, "sl-1")
    cap = make()
    cap.start()
    _wait_slot_active(factory, slot)

    healthy = _health(factory, slot)
    assert healthy["ok"] is True and healthy["active"] is True
    assert healthy["alertas"] == []
    assert healthy["umbrales"] == {
        "wal_retention_max_bytes": 2 * 1024**3, "stalled_max_s": 30 * 60,
    }

    cap.close()  # consumidor caído: slot presente pero inactivo
    _wait_slot_released(slot)
    fresh = _health(factory, slot)  # parada RECIENTE: gracia de 30 min
    assert fresh["ok"] is True and fresh["active"] is False

    # 31 min sin LATIDO (heartbeat_at viejo) con updated_at FRESCO: prueba que la salud usa
    # heartbeat_at, no updated_at (P2 rev. externa integral) — el código viejo no habría alertado.
    _exec(
        factory,
        "UPDATE shadow_capture_state "
        "SET heartbeat_at = now() - interval '31 minutes' WHERE id = 1",
    )
    with caplog.at_level(logging.ERROR, logger="jobhunt_core.shadow.gate"):
        res = _health(factory, slot)
    assert res["ok"] is False
    assert [a["code"] for a in res["alertas"]] == ["consumidor_parado"]
    assert res["stalled_s"] > 30 * 60
    assert "ALERTA slot" in caplog.text and "consumidor parado" in caplog.text
    assert "RUNBOOK" in res["alertas"][0]["msg"]  # apunta al runbook RTO


def test_slot_health_wal_retention_with_forced_low_threshold(capture, db, caplog):
    """Retención WAL: con el consumidor parado y tráfico legacy, el slot
    retiene WAL — el umbral FORZADO bajo dispara la alerta; con el REAL de
    §6 (2 GiB) el mismo estado no la dispara."""
    make, slot = capture
    factory = db
    cap = make()
    cap.start()  # bootstrap (frontera fresca: sin alerta de parada)
    cap.close()
    _wait_slot_released(slot)
    for i in range(5):  # WAL que el slot retiene sin consumidor
        _seed_legacy_job(factory, f"wal-{i}")

    with caplog.at_level(logging.ERROR, logger="jobhunt_core.shadow.gate"):
        res = _health(factory, slot, wal_retention_max_bytes=1)
    assert res["ok"] is False
    assert [a["code"] for a in res["alertas"]] == ["retencion_wal"]
    assert res["retained_bytes"] > 1
    assert "retiene" in caplog.text and "COMPARTIDA" in caplog.text

    real = _health(factory, slot)  # umbrales RATIFICADOS: en verde
    assert real["ok"] is True and real["retained_bytes"] < 2 * 1024**3


def test_slot_health_missing_slot_with_state_alerts(db, caplog):
    """Slot AUSENTE con estado registrado = continuidad WAL perdida ⇒ alerta
    (la condición que exige el rollback/replay del runbook); sin slot NI
    estado = sombra sin bootstrap: nada retiene WAL, sin alerta."""
    factory = db
    virgin = _health(factory, "gate_slot_inexistente")
    assert virgin["ok"] is True
    assert virgin["slot_exists"] is False and virgin["state_exists"] is False

    _exec(
        factory,
        "INSERT INTO shadow_capture_state (id, slot_name, snapshot_lsn, "
        "snapshot_exported_at, last_applied_lsn) "
        "VALUES (1, 'gate_slot_inexistente', 100, now(), 100)",
    )
    with caplog.at_level(logging.ERROR, logger="jobhunt_core.shadow.gate"):
        res = _health(factory, "gate_slot_inexistente")
    assert res["ok"] is False
    assert [a["code"] for a in res["alertas"]] == ["slot_ausente"]
    assert "rollback/replay" in res["alertas"][0]["msg"]


# ------------------------------------------- cadencias/beat y tareas Celery


def test_tasks_registered_beat_cadences_and_core_queues(db):
    from jobhunt_core.celery_app import celery_app
    from jobhunt_core.tasks.shadow import check_slot_health_task

    assert "jobhunt.shadow.run_cycle" in celery_app.tasks
    assert "jobhunt.shadow.check_slot_health" in celery_app.tasks
    # run_cycle es ingesta (drena el staging): core.harvest, serializa con
    # el proyector; la vigilancia del slot es ligera: core.default.
    assert celery_app.conf.task_routes["jobhunt.shadow.run_cycle"] == {
        "queue": "core.harvest"
    }
    assert celery_app.conf.task_routes["jobhunt.shadow.check_slot_health"] == {
        "queue": "core.default"
    }

    # Cadencias B-05 + P1-1 en el beat (ajustables por settings): muestreador,
    # salud del slot, PROYECTOR y despacho del outbox cada 5 min (P1-1: la
    # proyección/entrega solo al cierre del ciclo hacía imposible
    # latencia_p95<=600s); run_cycle diario 06:05 Europe/Zurich.
    by_task = {
        e["task"]: e for e in celery_app.conf.beat_schedule.values()
    }
    assert by_task["jobhunt.shadow.sample_outbox_lag"]["schedule"] == 300.0
    assert by_task["jobhunt.shadow.check_slot_health"]["schedule"] == 300.0
    assert by_task["jobhunt.shadow.project"]["schedule"] == 300.0
    assert by_task["jobhunt.delivery.dispatch_outbox"]["schedule"] == 300.0
    cron = by_task["jobhunt.shadow.run_cycle"]["schedule"]
    assert cron.hour == {6} and cron.minute == {5}
    assert celery_app.conf.timezone == "Europe/Zurich"
    # SOLO colas core.*: TODO lo que dispara el beat rutea a core.*.
    for name in by_task:
        assert celery_app.conf.task_routes[name]["queue"].startswith("core.")

    # La tarea corre: BD desechable sin slot ni estado = sin bootstrap, ok.
    result = check_slot_health_task.apply(kwargs={"slot": "gate_no_slot"})
    assert result.successful()
    assert result.result["ok"] is True
    assert result.result["slot_exists"] is False


# ------------------------------------------------- run_cycle end-to-end (§7)


def test_run_cycle_ciclo_mixto_inelegible_no_es_apto(db, gate_db):
    """Regresión revisión solo-código Nº2 (IMPORTANTE 2): con todos los
    gates verdes pero la ventana INICIADA ANTES del congelado del holdout,
    _run_cycle_locked decía cycle_ok=true y logueaba APTO mientras el
    contador quedaba 0/7 — dos veredictos contradictorios para el operador.
    Ahora cycle_ok exige elegibilidad; el mismo ciclo con el freeze
    anterior a su ventana sí es APTO."""
    factory = db
    _seed_green_cycle(factory, G0)  # sellado, todo verde

    # Freeze a MITAD de la ventana de G0: ciclo mixto.
    _freeze_holdout(
        factory, metrics.cycle_bounds(G0)[0] + timedelta(hours=6)
    )
    result = _run(gate.run_cycle(legacy_schema="public", now=GNOW))
    assert result["status"] == "ok"
    assert result["gates_failed"] == []       # verde en todos los gates…
    assert result["cycle_eligible"] is False  # …pero la ventana es mixta
    assert result["cycle_ok"] is False        # UN solo veredicto: no apto
    assert result["consecutive_ok"] == 0

    # Freeze ANTERIOR a la ventana: el mismo ciclo pasa a APTO y computa.
    _freeze_holdout(
        factory, metrics.cycle_bounds(G0)[0] - timedelta(days=1)
    )
    result2 = _run(gate.run_cycle(legacy_schema="public", now=GNOW))
    assert result2["cycle_eligible"] is True
    assert result2["cycle_ok"] is True
    assert result2["consecutive_ok"] == 1


def test_run_cycle_end_to_end_projects_computes_purges_evaluates(db, gate_db):
    factory = db
    _seed_model_policy(factory)
    embeddings.set_backend_factory(lambda name, version: DirectionalBackend())
    try:
        src = f"gfx{uuid.uuid4().hex[:6]}"
        user = uuid.uuid4()
        j1, j2 = f"{src}-1", f"{src}-2"
        # Perfil sombra con set CONGELADO (el proyector upserta el mismo
        # external_ref: idempotente) + espejo legacy de los 2 jobs activos.
        pid = _mk_profile(factory, str(user))
        _mk_frozen_set(factory, pid, {j1: 3, j2: 2})
        for h in (j1, j2):
            _seed_legacy_job(factory, h, source=src)
        _seed_legacy_result(factory, user, j1, 90)
        _seed_staging(factory, [
            ("jobs", "I", j1, _job_payload(j1, src)),
            ("jobs", "I", j2, _job_payload(j2, src)),
            ("users", "I", str(user), {"id": str(user), "is_active": True}),
            ("user_profiles", "I", str(uuid.uuid4()), {
                "user_id": str(user), "title": "dev",
                "cv_text": "cv con python y fastapi", "skills": ["python"],
                "updated_at": "2026-07-25T10:00:00+00:00",
            }),
        ])

        moment = datetime.now(timezone.utc)  # fijo: ambas pasadas, mismo ciclo
        result = _run(gate.run_cycle(legacy_schema="public", now=moment))

        # Orquestación completa: proyecta → computa → purga → evalúa.
        assert result["status"] == "ok"
        assert result["cycle_id"] == metrics.latest_closed_cycle_id(moment).isoformat()
        assert result["project"]["status"] == "ok"
        assert result["project"]["changes"] == 4
        assert result["metrics"]["profiles_measured"] == 1
        assert result["purge"]["staging_deleted"] == 0  # nada fuera de retención
        cid = date.fromisoformat(result["cycle_id"])

        # Corpus espejo del legacy activo ⇒ perdida = 0 (2 vivos − 2 slots).
        perdida = _rows(
            factory,
            "SELECT value, details FROM shadow_cycle_metrics "
            "WHERE cycle_id = :c AND metric = 'perdida'", c=cid,
        )[0]
        assert float(perdida.value) == 0
        assert perdida.details["legacy_activos_ingeribles"] == 2
        assert perdida.details["slots_legacy_activos"] == 2
        ndcg = _rows(
            factory,
            "SELECT value, details FROM shadow_cycle_metrics "
            "WHERE cycle_id = :c AND metric = 'ndcg@10' AND scope = :s",
            c=cid, s=f"profile:{pid}",
        )[0]
        assert 0.0 <= float(ndcg.value) <= 1.0  # computado sobre el feed real
        # Gates evaluados: perdida en verde; los sin datos del ciclo cerrado
        # (outbox sin samples, sin lotes en SU ventana) en rojo, y también
        # labels_ready/dedup (P1-2: oráculo sin pares dedup ⇒ no
        # demostrable) — el contador queda a 0 (conservador, §6).
        assert "perdida" not in result["gates_failed"]
        assert "outbox_lag_p99" in result["gates_failed"]
        assert "latencia_p95" in result["gates_failed"]
        assert "labels_ready" in result["gates_failed"]
        assert "dedup_precision" in result["gates_failed"]
        assert result["cycle_ok"] is False
        assert (result["consecutive_ok"], result["required"]) == (0, 7)

        # IDEMPOTENTE y RE-ENTRANTE: segunda pasada sin pendientes; el ciclo
        # quedó SELLADO ⇒ compute_cycle se SALTA (inmutabilidad P1-4) y las
        # filas de métricas quedan idénticas (sin duplicar ni reescribir).
        n1 = _scalar(
            factory,
            "SELECT count(*) FROM shadow_cycle_metrics WHERE cycle_id = :c",
            c=cid,
        )
        result2 = _run(gate.run_cycle(legacy_schema="public", now=moment))
        assert result2["status"] == "ok"
        assert result2["project"]["changes"] == 0
        assert result2["metrics"]["skipped_sealed"] is True
        assert _scalar(
            factory,
            "SELECT count(*) FROM shadow_cycle_metrics WHERE cycle_id = :c",
            c=cid,
        ) == n1

        # SINGLE-FLIGHT propio: con el lock tomado por otra sesión, la
        # invocación concurrente sale limpia sin orquestar nada.
        lock_engine = sa.create_engine(
            gate_db["core_dsn"], poolclass=sa.pool.NullPool,
            isolation_level="AUTOCOMMIT",
        )
        try:
            with lock_engine.connect() as lock_conn:
                lock_conn.execute(
                    sa.text("SELECT pg_advisory_lock(hashtextextended(:k, 0))"),
                    {"k": "jobhunt:shadow-run-cycle"},
                )
                busy = _run(gate.run_cycle(legacy_schema="public", now=moment))
                assert busy == {"status": "already_running"}
                lock_conn.execute(
                    sa.text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"),
                    {"k": "jobhunt:shadow-run-cycle"},
                )
        finally:
            lock_engine.dispose()
    finally:
        embeddings.set_backend_factory(None)


def test_run_cycle_aborts_without_metrics_when_projector_busy(
    db, gate_db, monkeypatch
):
    """Regresión P1-3 (rev. externa): con el lock del PROYECTOR tomado por
    otra conexión, run_cycle YA NO "computa igual" — reintenta acotado
    (backoff corto) y sale con status='project_busy' SIN sellar métricas,
    SIN purgar y SIN tocar el contador."""
    factory = db
    monkeypatch.setattr(gate, "PROJECT_DRAIN_RETRIES", 2)
    monkeypatch.setattr(gate, "PROJECT_DRAIN_BACKOFF_S", 0.05)
    src = f"busy{uuid.uuid4().hex[:6]}"
    _seed_staging(factory, [("jobs", "I", f"{src}-1", _job_payload(f"{src}-1", src))])
    moment = datetime.now(timezone.utc)
    cid = metrics.latest_closed_cycle_id(moment)

    lock_engine = sa.create_engine(
        gate_db["core_dsn"], poolclass=sa.pool.NullPool,
        isolation_level="AUTOCOMMIT",
    )
    try:
        with lock_engine.connect() as lock_conn:
            lock_conn.execute(
                sa.text("SELECT pg_advisory_lock(hashtextextended(:k, 0))"),
                {"k": "jobhunt:shadow-projector"},
            )
            res = _run(gate.run_cycle(legacy_schema="public", now=moment))
            lock_conn.execute(
                sa.text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"),
                {"k": "jobhunt:shadow-projector"},
            )
    finally:
        lock_engine.dispose()

    assert res["status"] == "project_busy"
    assert res["project"]["status"] == "already_running"
    assert res["project_attempts"] == 2  # reintento ACOTADO, no infinito
    assert "metrics" not in res and "purge" not in res  # nada sellado
    assert _scalar(
        factory,
        "SELECT count(*) FROM shadow_cycle_metrics WHERE cycle_id = :c",
        c=cid,
    ) == 0  # el revisor encontraba aquí un ciclo computado sin drenar
    assert _scalar(
        factory,
        "SELECT count(*) FROM shadow_change_log WHERE applied_at IS NULL",
    ) == 1  # el staging sigue pendiente, intacto


def test_run_cycle_staging_pending_blocks_seal(db):
    """Regresión P1-3 (verificación PRE-SELLADO): si tras el drenado quedan
    filas de shadow_change_log sin applied_at con lsn <= watermark del
    cierre del ciclo, run_cycle NO computa (status='staging_pending') —
    se fuerza con max_batches=0 (drenado sin lotes) y una fila recibida
    DENTRO de la ventana del ciclo cerrado."""
    factory = db
    moment = datetime.now(timezone.utc)
    cid = metrics.latest_closed_cycle_id(moment)
    cycle_end = metrics.cycle_bounds(cid)[1]
    _exec(
        factory,
        "INSERT INTO shadow_change_log (lsn, seq_in_tx, src_table, op, pk, "
        "payload, received_at) VALUES (:l, 0, 'jobs', 'I', :p, "
        "CAST('{}' AS jsonb), :r)",
        {"l": next(_LSN_SEQ), "p": f"pend-{uuid.uuid4().hex[:6]}",
         "r": cycle_end - timedelta(hours=2)},
    )
    res = _run(
        gate.run_cycle(legacy_schema="public", now=moment, max_batches=0)
    )
    assert res["status"] == "staging_pending"
    assert res["staging_pending"] == 1
    assert res["project"]["status"] == "ok"  # el drenado no falló: no drenó
    assert "metrics" not in res and "purge" not in res
    assert _scalar(
        factory,
        "SELECT count(*) FROM shadow_cycle_metrics WHERE cycle_id = :c",
        c=cid,
    ) == 0


# ------------------------------------- rollback/replay COMPLETO (DoD B-05)


def test_rollback_replay_full_executed_against_disposable_db(capture, db, gate_db):
    """DoD §7 B-05: UN rollback/replay COMPLETO ejecutado — parar consumidor
    → DROP slot → desactivar fuentes legacy:* y archivar sus vacantes →
    truncar staging → re-crear slot + re-backfill por snapshot CONSISTENTE
    (staging == legacy) — con `public` INTACTO y replay funcional después."""
    make, slot = capture
    factory = db
    for i in range(3):
        _seed_legacy_job(factory, f"rb-{i}")
    uid = uuid.uuid4()
    _seed_legacy_user(factory, uid)
    _seed_legacy_profile(factory, uuid.uuid4(), uid)

    cap = make()
    cap.start()  # bootstrap: slot + snapshot + backfill (5 filas) + frontera
    old_state = _rows(factory, "SELECT * FROM shadow_capture_state")[0]
    cap.close()  # paso 1 del runbook: consumidor PARADO
    _wait_slot_released(slot)

    totals = _run(projector.project_pending())  # sombra con corpus real
    assert totals["upserts"] == 3
    active_before = _scalar(
        factory,
        "SELECT count(*) FROM source_listing_incarnations i "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "JOIN sources s ON s.id = l.source_id AND s.name LIKE 'legacy:%' "
        "WHERE i.ended_at IS NULL",
    )
    assert active_before == 3
    legacy_before = _rows(
        factory, "SELECT hash, title, is_active FROM public.jobs ORDER BY hash"
    )

    # GUARDA: sin confirm=True no se toca NADA.
    with pytest.raises(RuntimeError, match="confirm=True"):
        gate.rollback_replay(
            gate_db["capture_dsn"], gate_db["core_dsn"], slot=slot
        )
    assert _scalar(
        factory,
        "SELECT count(*) FROM pg_replication_slots WHERE slot_name = :n", n=slot,
    ) == 1
    assert _scalar(
        factory, "SELECT snapshot_lsn FROM shadow_capture_state"
    ) == old_state.snapshot_lsn
    # GUARDA: el esquema de escritura JAMÁS es public.
    with pytest.raises(RuntimeError, match="public"):
        gate.rollback_replay(
            gate_db["capture_dsn"], gate_db["core_dsn"], slot=slot,
            schema="public", confirm=True,
        )

    summary = gate.rollback_replay(
        gate_db["capture_dsn"], gate_db["core_dsn"], slot=slot, confirm=True
    )
    # Secuencia completa reportada, calculable a mano.
    assert summary["slot_dropped"] is True
    assert summary["scopes_disabled"] == 0  # el scope sombra ya nace disabled
    assert summary["incarnations_closed"] == 3
    assert summary["vacancies_archived"] == 3
    assert summary["staging_rows_deleted"] == 5
    assert summary["state_rows_deleted"] == 1
    assert summary["slot_recreated"] is True
    assert summary["backfill_rows"] == 5  # == legacy (3 jobs + 1 user + 1 perfil)
    assert summary["snapshot_lsn"] > old_state.snapshot_lsn

    # Fuentes desactivadas y vacantes archivadas (cero encarnaciones vivas).
    assert _scalar(
        factory,
        "SELECT count(*) FROM source_listing_incarnations WHERE ended_at IS NULL",
    ) == 0
    assert _scalar(
        factory, "SELECT count(*) FROM vacancies WHERE archived_at IS NULL"
    ) == 0
    assert _scalar(
        factory,
        "SELECT count(*) FROM harvest_scopes WHERE enabled",
    ) == 0
    # Re-backfill CONSISTENTE: staging nuevo = op 'I' en el snapshot nuevo,
    # sin aplicar, con conteos == legacy por tabla.
    staged = dict(
        _rows(
            factory,
            "SELECT src_table, count(*) FROM shadow_change_log "
            "WHERE op = 'I' AND lsn = :l AND applied_at IS NULL "
            "GROUP BY src_table",
            l=summary["snapshot_lsn"],
        )
    )
    assert staged == {"jobs": 3, "users": 1, "user_profiles": 1}
    assert _scalar(factory, "SELECT count(*) FROM shadow_change_log") == 5
    state = _rows(factory, "SELECT * FROM shadow_capture_state")[0]
    assert state.slot_name == slot
    assert state.snapshot_lsn == state.last_applied_lsn == summary["snapshot_lsn"]
    slot_row = _rows(
        factory,
        "SELECT active FROM pg_replication_slots WHERE slot_name = :n", n=slot,
    )
    assert len(slot_row) == 1 and not slot_row[0].active  # re-creado, a la espera

    # `public` INTACTO: el rollback JAMÁS escribe en el esquema legacy.
    assert _rows(
        factory, "SELECT hash, title, is_active FROM public.jobs ORDER BY hash"
    ) == legacy_before
    assert _scalar(factory, "SELECT count(*) FROM public.users") == 1
    assert _scalar(factory, "SELECT count(*) FROM public.user_profiles") == 1

    # REPLAY funcional tras el rollback: el consumidor reanuda desde la
    # frontera nueva y el streaming vuelve a fluir.
    cap2 = make()
    cap2.start()
    _seed_legacy_job(factory, "rb-post")
    _stream_until(
        cap2,
        lambda: _scalar(
            factory,
            "SELECT count(*) FROM shadow_change_log WHERE pk = 'rb-post'",
        ) >= 1,
    )
    assert _scalar(
        factory, "SELECT count(*) FROM shadow_change_log WHERE pk = 'rb-post'"
    ) == 1
    cap2.close()


# ------------------------- simulacro: consumidor caído 30 min (DoD B-05)


def test_consumer_down_30min_alert_and_lossless_recovery(capture, db, caplog):
    """DoD §7 B-05: consumidor caído 30 min ⇒ ALERTA de §6, y recuperación
    SIN pérdida — kill de la conexión (patrón kill −9 de B-01) + cambios
    legacy durante la caída + reanudación desde last_applied_lsn con cada
    cambio stageado EXACTAMENTE una vez."""
    make, slot = capture
    factory = db
    _seed_legacy_job(factory, "dw-0")
    cap1 = make()
    cap1.start()
    snap = _scalar(factory, "SELECT snapshot_lsn FROM shadow_capture_state")

    _seed_legacy_job(factory, "dw-1")  # tx aplicada y ACKeada antes del fallo
    _stream_until(
        cap1,
        lambda: _scalar(
            factory,
            "SELECT count(*) FROM shadow_change_log WHERE pk = 'dw-1'",
        ) >= 1,
    )
    cap1.close()  # kill de la conexión: consumidor CAÍDO, slot retiene WAL
    _wait_slot_released(slot)

    # El legacy sigue escribiendo durante la caída.
    _seed_legacy_job(factory, "dw-2")
    _exec(factory, "UPDATE public.jobs SET is_active = false WHERE hash = 'dw-0'")

    # 30+ min sin LATIDO (heartbeat_at viejo) ⇒ ALERTA persistente.
    _exec(
        factory,
        "UPDATE shadow_capture_state "
        "SET heartbeat_at = now() - interval '31 minutes' WHERE id = 1",
    )
    with caplog.at_level(logging.ERROR, logger="jobhunt_core.shadow.gate"):
        res = _health(factory, slot)
    assert res["ok"] is False
    assert [a["code"] for a in res["alertas"]] == ["consumidor_parado"]
    assert "gate: ALERTA slot" in caplog.text

    # RECUPERACIÓN (runbook §2, RTO <= 1h): reanudación desde
    # last_applied_lsn — los cambios de la caída llegan, sin pérdida ni
    # duplicado (la re-entrega la absorbe la PK del staging).
    cap2 = make()
    cap2.start()
    _stream_until(
        cap2,
        lambda: _scalar(
            factory,
            "SELECT count(*) FROM shadow_change_log "
            "WHERE pk IN ('dw-2', 'dw-0') AND lsn > :s", s=snap,
        ) >= 2,
    )
    assert _scalar(
        factory, "SELECT count(*) FROM shadow_change_log WHERE pk = 'dw-2'"
    ) == 1
    dw0_updates = _rows(
        factory,
        "SELECT payload FROM shadow_change_log "
        "WHERE pk = 'dw-0' AND op = 'U' AND lsn > :s", s=snap,
    )
    assert len(dw0_updates) == 1
    assert dw0_updates[0].payload["is_active"] is False
    assert _scalar(
        factory, "SELECT count(*) FROM shadow_change_log WHERE pk = 'dw-1'"
    ) == 1  # lo ya ACKeado no se duplica

    # Con el consumidor recuperado la salud vuelve a verde (updated_at
    # avanzó con las transacciones aplicadas y el walsender está conectado).
    _wait_slot_active(factory, slot)
    ok = _health(factory, slot)
    assert ok["ok"] is True and ok["active"] is True
    cap2.close()


# --------------------------------------------------------------------------
# core0036 — paradas DECLARADAS del anfitrión
#
# El contador metía en el mismo saco un ciclo ROJO (hay evidencia de que algo
# falló) y uno AUSENTE (no hay evidencia de nada). Apagar el equipo una noche
# reiniciaba la racha, así que «siete consecutivos» solo era medible en una
# máquina que no se apaga nunca. Ahora la ausencia se SALTA si alguien la
# declara — y la declaración sale en el informe, con tope y con ventana.
# --------------------------------------------------------------------------
def _declara_parada(factory, cycle_id, motivo="apagado del anfitrión"):
    _exec(
        factory,
        "INSERT INTO shadow_declared_downtime (cycle_id, reason) VALUES (:c, :r) "
        "ON CONFLICT (cycle_id) DO UPDATE SET reason = :r",
        {"c": cycle_id, "r": motivo},
    )


def test_una_parada_declarada_no_rompe_la_racha(db):
    """El caso que motivó el cambio: el anfitrión se apaga una noche. Sin
    declararlo la racha vuelve a cero; declarándolo, el hueco se salta."""
    factory = db
    _freeze_holdout(factory, datetime(2026, 7, 1, tzinfo=metrics.CYCLE_TZ))
    for i in (0, 1, 3, 4):                      # falta G0-2
        _seed_green_cycle(factory, G0 - timedelta(days=i))

    sin = _status(factory, now=GNOW)
    assert sin["consecutive_ok"] == 2, (
        "sin declarar, el hueco tiene que cortar la racha: " + repr(sin["consecutive_ok"])
    )

    _declara_parada(factory, G0 - timedelta(days=2))
    con = _status(factory, now=GNOW)
    assert con["consecutive_ok"] == 4, repr(con["consecutive_ok"])
    # Y la parada se PUBLICA: nadie puede leer «4 verdes» sin ver el hueco.
    assert len(con["paradas_declaradas"]) == 1, con["paradas_declaradas"]
    assert con["paradas_declaradas"][0]["motivo"] == "apagado del anfitrión"
    hueco = next(
        e for e in con["per_cycle"]
        if e["cycle"] == (G0 - timedelta(days=2)).isoformat()
    )
    assert hueco["parada_declarada"] is True and hueco["computado"] is False


def test_una_declaracion_no_puede_tapar_un_ciclo_rojo(db):
    """LA propiedad de seguridad del cambio. Si el ciclo se computó, mandan sus
    métricas: la declaración solo cubre la ausencia TOTAL. Si no, declarar sería
    una forma de borrar un día malo a posteriori."""
    factory = db
    _freeze_holdout(factory, datetime(2026, 7, 1, tzinfo=metrics.CYCLE_TZ))
    _seed_green_cycle(factory, G0)
    _seed_green_cycle(factory, G0 - timedelta(days=1))
    _seed_metric(factory, G0 - timedelta(days=1), "perdida", "global", 1, {})
    _seed_green_cycle(factory, G0 - timedelta(days=2))
    _declara_parada(factory, G0 - timedelta(days=1), "intento de tapar un rojo")

    r = _status(factory, now=GNOW)
    assert r["consecutive_ok"] == 1, (
        "una declaración tapó un ciclo ROJO computado: " + repr(r["consecutive_ok"])
    )
    rojo = next(
        e for e in r["per_cycle"]
        if e["cycle"] == (G0 - timedelta(days=1)).isoformat()
    )
    assert rojo["parada_declarada"] is False, rojo
    assert rojo["computado"] is True


def test_demasiadas_paradas_declaradas_cortan_igual(db):
    """El tope existe para que «consecutivos» siga significando algo: con
    ausencias ilimitadas, siete verdes podrían repartirse a lo largo de meses."""
    from jobhunt_core.shadow.gate import GATE_MAX_DECLARED_GAPS

    factory = db
    _freeze_holdout(factory, datetime(2026, 6, 1, tzinfo=metrics.CYCLE_TZ))
    verdes = [0, 2, 4, 6, 8]
    for i in verdes:
        _seed_green_cycle(factory, G0 - timedelta(days=i))
    for i in (1, 3, 5, 7):                       # CUATRO paradas: una de más
        _declara_parada(factory, G0 - timedelta(days=i))

    r = _status(factory, now=GNOW)
    assert len(r["paradas_declaradas"]) <= GATE_MAX_DECLARED_GAPS + 1
    assert r["consecutive_ok"] < len(verdes), (
        "las paradas se toleraron sin tope: " + repr(r)
    )
    assert r["gate_passed"] is False
