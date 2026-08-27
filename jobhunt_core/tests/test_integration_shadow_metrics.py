"""Métricas por ciclo B-04 (shadow/metrics) contra Postgres real.

DoD (CONTRATOS_FASE_B §5/§6/§7): fórmulas EXACTAS verificadas sobre fixtures
CALCULABLES A MANO — (a) ciclo determinista [06:00, 06:00) con cycle_id y
ventana INYECTABLES; (b) ndcg@10 con relevancias conocidas → valor exacto,
nDCG del feed VISIBLE legacy contra el MISMO set/IDCG, y overlap parcial;
(c) dedup precision/recall con TP/FP/FN/TN construidos (attach, candidato
pending/rejected, par sin mapeo); (d) falsos_negativos en AMBOS modos (< 50
rel>=2 ⇒ 0 permitidos; >= 50 ⇒ <= 2%); (e) perdida = 0 en espejo sano y > 0
con hueco inyectado + backlog > 1h, con no_ingeribles APARTE; (f) muestreador
de outbox que appendea y p99 exacto (percentile_cont); (g) latencia_p95 con
lotes sembrados incl. uno sin sellar y uno fuera de ventana; (h) coste y
reenlace_pct con fuentes definidas; (i) purga que borra lo viejo aplicado y
PRESERVA la última fila users por pk (la exclusión del proyector sigue
funcionando) y lo no aplicado, podando samples viejos SOLO si su p99 quedó
SELLADO (los no sellados esperan a su compute) — idempotente;
(j) gates ok/failed con umbrales forzados; (k) informe legible generado;
(l) tareas Celery registradas y enrutadas a core.default.

Aislamiento (patrón B-02): BD DESECHABLE (jobhunt_met_<hex>) migrada a head
con un esquema legacy FIXTURE propio (mini jobs/match_results) — estos tests
JAMÁS tocan `public` ni el staging del slot real. Ejecutar vía core-migrate.
"""

import asyncio
import json
import logging
import math
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import embeddings, matching
from jobhunt_core import profiles as core_profiles
from jobhunt_core.config import settings
from jobhunt_core.shadow import gate, labels, metrics, projector, stratum
from jobhunt_core.tests.alembic_runner import run_alembic

_ADMIN = os.getenv("CORE_ADMIN_DATABASE_URL")
S = settings.CORE_DB_SCHEMA
LEG = "legmx"  # esquema legacy FIXTURE dentro de la BD desechable
SHA = "b" * 40

pytestmark = pytest.mark.skipif(
    not _ADMIN, reason="requiere BD (ejecutar vía core-migrate)"
)

# Ciclo FIJO e inyectado: nada depende de la hora real del test.
CYCLE = date(2026, 7, 20)
CSTART, CEND = metrics.cycle_bounds(CYCLE)
AFTER = CEND + timedelta(hours=1)  # "ahora" tras el cierre del ciclo


@pytest.fixture(scope="module")
def met_db():
    """BD desechable migrada a head + esquema legacy fixture (URL asyncpg)."""
    dbname = f"jobhunt_met_{uuid.uuid4().hex[:12]}"
    parts = urlsplit(_ADMIN)
    admin_engine = sa.create_engine(
        _ADMIN, poolclass=sa.pool.NullPool, isolation_level="AUTOCOMMIT"
    )
    with admin_engine.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{dbname}"'))
    db_url = urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))
    async_url = db_url.replace("postgresql://", "postgresql+asyncpg://")
    engine = sa.create_engine(db_url, poolclass=sa.pool.NullPool)
    try:
        with engine.begin() as c:
            c.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            c.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{S}"'))
        run_alembic(async_url, "upgrade", "head")
        with engine.begin() as c:
            # Mini-tablas legacy (columnas que leen las métricas, §1 RO).
            c.execute(sa.text(f'CREATE SCHEMA "{LEG}"'))
            c.execute(
                sa.text(
                    f"CREATE TABLE {LEG}.jobs ("
                    f"hash varchar(32) PRIMARY KEY, source varchar(50), "
                    f"url varchar(2048), is_active boolean NOT NULL DEFAULT true, "
                    f"duplicate_of varchar(32), "
                    # first_seen_at (G1 H-7): la gracia de alta del minuendo
                    f"first_seen_at timestamptz NOT NULL DEFAULT now())"
                )
            )
            c.execute(
                sa.text(
                    f"CREATE TABLE {LEG}.match_results ("
                    f"user_id uuid NOT NULL, job_hash varchar(32) NOT NULL, "
                    f"feedback varchar(20), "
                    f"score_final float NOT NULL DEFAULT 0)"
                )
            )
        yield async_url
    finally:
        engine.dispose()
        with admin_engine.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
        admin_engine.dispose()


@pytest.fixture()
def db(met_db, monkeypatch):
    """Factory async sobre la BD desechable + settings parcheados (las tareas
    Celery crean su engine desde settings). Limpieza: TRUNCATE — la BD es de
    usar y tirar."""
    monkeypatch.setattr(settings, "CORE_DATABASE_URL", met_db)
    engine = create_async_engine(
        met_db,
        poolclass=sa.pool.NullPool,
        connect_args={"server_settings": {"search_path": f"{S}, public"}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory

    async def cleanup():
        async with engine.begin() as c:
            # core0033 (G9 P3-A): con una cohorte CONGELADA el TRUNCATE de los pares y
            # de los sellos está prohibido — se desmonta la guarda como las de
            # core0025/0026 en el resto de fixtures (DDL del owner) y se vuelve a montar.
            for tabla in ("labeled_dedup_pairs", "labeled_dedup_cohorts"):
                await c.execute(
                    sa.text(f"ALTER TABLE {tabla} DISABLE TRIGGER {tabla}_truncate_guard")
                )
            await c.execute(
                sa.text(
                    "TRUNCATE shadow_change_log, shadow_projection_batches, "
                    "shadow_cycle_metrics, labeled_dedup_pairs, "
                    "labeled_dedup_cohorts"
                )
            )
            for tabla in ("labeled_dedup_pairs", "labeled_dedup_cohorts"):
                await c.execute(
                    sa.text(f"ALTER TABLE {tabla} ENABLE TRIGGER {tabla}_truncate_guard")
                )
            await c.execute(sa.text("TRUNCATE integration_outbox CASCADE"))
            await c.execute(
                sa.text(
                    "TRUNCATE consumers, sources, vacancies, scoring_policies, "
                    "embedding_models CASCADE"
                )
            )
            await c.execute(sa.text(f"TRUNCATE {LEG}.jobs, {LEG}.match_results"))
        await engine.dispose()

    asyncio.run(cleanup())


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


# ------------------------------------------------------------- constructores


def _mk_source(factory, name):
    sid = uuid.uuid4()
    _exec(
        factory,
        "INSERT INTO sources (id, name, tier) VALUES (:i, :n, 0)",
        {"i": sid, "n": name},
    )
    return sid


def _mk_slot(
    factory, source_id, ext, seq=1, active=True, archived=False,
    first_seen=None, last_seen=None, ended=None, vacancy_id=None,
    listing_id=None,
):
    """Vacante + slot + encarnación con timestamps INYECTABLES. Devuelve
    (vacancy_id, listing_id, incarnation_id)."""
    vid = vacancy_id or uuid.uuid4()
    lid = listing_id or uuid.uuid4()
    iid = uuid.uuid4()

    async def go():
        async with factory() as s:
            if vacancy_id is None:
                await s.execute(
                    sa.text(
                        "INSERT INTO vacancies (id, archived_at) "
                        "VALUES (:i, CASE WHEN :a THEN now() END)"
                    ),
                    {"i": vid, "a": archived},
                )
            if listing_id is None:
                await s.execute(
                    sa.text(
                        "INSERT INTO source_listings "
                        "(id, source_id, external_id, url_normalized) "
                        "VALUES (:i, :s, :e, :u)"
                    ),
                    {"i": lid, "s": source_id, "e": ext, "u": f"https://m/{ext}"},
                )
            await s.execute(
                sa.text(
                    "INSERT INTO source_listing_incarnations "
                    "(id, source_listing_id, vacancy_id, seq, url, "
                    " first_seen_at, last_seen_at, ended_at) "
                    "VALUES (:i, :l, :v, :q, :u, "
                    " COALESCE(:fs, now()), COALESCE(:ls, now()), "
                    " CASE WHEN :act THEN NULL ELSE COALESCE(:en, now()) END)"
                ),
                {
                    "i": iid, "l": lid, "v": vid, "q": seq,
                    "u": f"https://m/{ext}/{seq}", "fs": first_seen,
                    "ls": last_seen, "en": ended, "act": active and ended is None,
                },
            )
            await s.commit()

    _run(go())
    return vid, lid, iid


def _mk_profile(factory, external_ref):
    """Perfil bajo el consumer sombra (los medidos por compute_cycle)."""

    async def go():
        async with factory() as s:
            cid = await core_profiles.ensure_consumer(s, projector.SHADOW_CONSUMER)
            pid = await core_profiles.upsert_profile(s, cid, external_ref)
            await s.commit()
            return pid

    return _run(go())


def _mk_frozen_set(factory, pid, judgments, name="ronda-1", freeze=True):
    async def go():
        async with factory() as s:
            sid = await labels.create_set(s, pid, name)
            for ref, rel in judgments.items():
                await labels.add_judgment(s, sid, ref, rel)
            if freeze:
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


def _mk_eval(factory, mp, pid, vid, score, created_at=None):
    """Evaluación + estado vigente (lo que lee matching.feed). Las FKs
    compuestas de match_evaluations exigen revisiones REALES: una revisión
    de perfil (reutilizada) y una offer_revision por evaluación."""
    mid, polid = mp
    eid = uuid.uuid4()

    async def go():
        async with factory() as s:
            rid = (
                await s.execute(
                    sa.text(
                        "SELECT id FROM profile_revisions "
                        "WHERE profile_id = :p LIMIT 1"
                    ),
                    {"p": pid},
                )
            ).scalar()
            if rid is None:
                rid = await core_profiles.save_profile_revision(
                    s, pid,
                    {"title": "dev", "cv_text": "cv python", "skills": ["python"]},
                )
            orid = uuid.uuid4()
            await s.execute(
                sa.text(
                    "INSERT INTO offer_revisions "
                    "(id, vacancy_id, content_hash, text_hash, content) "
                    "VALUES (:i, :v, :ch, :th, CAST('{}' AS jsonb))"
                ),
                {"i": orid, "v": vid, "ch": uuid.uuid4().hex, "th": uuid.uuid4().hex},
            )
            await s.execute(
                sa.text(
                    "INSERT INTO match_evaluations "
                    "(id, profile_id, vacancy_id, offer_revision_id, "
                    " profile_revision_id, model_id, scoring_policy_id, "
                    " eval_key, score_final, scores, created_at) "
                    "VALUES (:i, :p, :v, :o, :r, :m, :sp, :k, :sc, "
                    " CAST('{}' AS jsonb), COALESCE(:ca, now()))"
                ),
                {
                    "i": eid, "p": pid, "v": vid, "o": orid, "r": rid,
                    "m": mid, "sp": polid, "k": uuid.uuid4().hex, "sc": score,
                    "ca": created_at,
                },
            )
            await s.execute(
                sa.text(
                    "INSERT INTO profile_vacancy_state "
                    "(profile_id, vacancy_id, current_eval_id) "
                    "VALUES (:p, :v, :e) ON CONFLICT (profile_id, vacancy_id) "
                    "DO UPDATE SET current_eval_id = EXCLUDED.current_eval_id"
                ),
                {"p": pid, "v": vid, "e": eid},
            )
            await s.commit()

    _run(go())
    return eid


_URL_DEFAULT = object()  # centinela: url=None debe poder INSERTAR NULL


def _legacy_job(factory, h, active=True, dup=None, url=_URL_DEFAULT,
                first_seen=None):
    # first_seen anterior al inicio del ciclo FIJO (G1 H-7): el job supera la
    # gracia de alta de 1h y cuenta en el minuendo, como antes del fix.
    _exec(
        factory,
        f"INSERT INTO {LEG}.jobs (hash, source, url, is_active, duplicate_of, "
        f"first_seen_at) VALUES (:h, 'metfx', :u, :a, :d, :f)",
        {"h": h, "u": f"https://leg/{h}" if url is _URL_DEFAULT else url,
         "a": active, "d": dup,
         "f": first_seen or (CSTART - timedelta(days=1))},
    )


def _legacy_result(factory, user_id, job_hash, score, feedback=None):
    _exec(
        factory,
        f"INSERT INTO {LEG}.match_results (user_id, job_hash, feedback, score_final) "
        f"VALUES (:u, :j, :f, :s)",
        {"u": user_id, "j": job_hash, "f": feedback, "s": score},
    )


def _stage_users(factory, rows):
    """rows = [(lsn, op, user_id, is_active)] → filas `users` del staging YA
    APLICADAS (la forma que lee inactive_user_refs, mecanismo compartido
    proyector/métricas)."""
    for lsn, op, uid, active in rows:
        _exec(
            factory,
            "INSERT INTO shadow_change_log (lsn, seq_in_tx, src_table, op, pk, "
            "payload, applied_at) VALUES (:l, 0, 'users', :o, :p, "
            "CAST(:j AS jsonb), now())",
            {"l": lsn, "o": op, "p": str(uid),
             "j": json.dumps({"id": str(uid), "is_active": active})},
        )


def _compute(factory, cycle_id=None, now=AFTER, force=False):
    async def go():
        async with factory() as s:
            r = await metrics.compute_cycle(
                s, cycle_id=cycle_id, legacy_schema=LEG, now=now, force=force
            )
            await s.commit()
            return r

    return _run(go())


def _metric_row(factory, metric, scope="global", cycle=CYCLE):
    rows = _rows(
        factory,
        "SELECT value, details, finished_at FROM shadow_cycle_metrics "
        "WHERE cycle_id = :c AND metric = :m AND scope = :s",
        c=cycle, m=metric, s=scope,
    )
    return rows[0] if rows else None


def _gates(factory, cycle=CYCLE):
    async def go():
        async with factory() as s:
            return await metrics.evaluate_gates(s, cycle)

    return _run(go())


# --------------------------------------------------- ciclo determinista (§5)


def test_cycle_bounds_and_ids_are_deterministic():
    start, end = metrics.cycle_bounds(CYCLE)
    # Ventana calendario [06:00, 06:00 +1d) hora suiza (CEST en julio).
    assert start.isoformat() == "2026-07-20T06:00:00+02:00"
    assert end.isoformat() == "2026-07-21T06:00:00+02:00"
    # 05:59 local pertenece al ciclo ANTERIOR; 06:00 abre el del día.
    assert metrics.current_cycle_id(start - timedelta(minutes=1)) == CYCLE - timedelta(days=1)
    assert metrics.current_cycle_id(start) == CYCLE
    assert metrics.current_cycle_id(start + timedelta(hours=23)) == CYCLE
    # Inyectable en UTC: 2026-07-21T03:59Z = 05:59 CEST → sigue en CYCLE.
    assert metrics.current_cycle_id(
        datetime(2026, 7, 21, 3, 59, tzinfo=timezone.utc)
    ) == CYCLE
    assert metrics.latest_closed_cycle_id(AFTER) == CYCLE


# ------------------------------------------- ndcg@10 / legacy / overlap (§5)


def test_ndcg_core_and_legacy_and_overlap_exact_values(db):
    factory = db
    mp = _seed_model_policy(factory)
    user = uuid.uuid4()
    pid = _mk_profile(factory, str(user))
    src = _mk_source(factory, "legacy:metfx")
    p = uuid.uuid4().hex[:6]
    A, B, C, D, E, X = (f"{p}-{n}" for n in ("a", "b", "c", "d", "e", "x"))

    # Set CONGELADO: A=3, B=2, C=1, D=0 (E y X sin juzgar).
    _mk_frozen_set(factory, pid, {A: 3, B: 2, C: 1, D: 0})
    vacs = {ref: _mk_slot(factory, src, ref)[0] for ref in (A, B, C, D, E)}
    # Feed core (score DESC): [A, C, E, B] → rels [3, 1, 0, 2].
    for ref, score in ((A, 90), (C, 80), (E, 70), (B, 60)):
        _mk_eval(factory, mp, pid, vacs[ref], score)

    # Legacy VISIBLE espejo de get_results: [B, A, X] → rels [2, 3, 0].
    for h in (A, B, C, D, E, X):
        _legacy_job(factory, h)
    _legacy_job(factory, f"{p}-inact", active=False)
    _legacy_job(factory, f"{p}-dup", dup=A)
    _legacy_result(factory, user, B, 99, feedback="thumbs_up")
    _legacy_result(factory, user, A, 98)
    _legacy_result(factory, user, X, 97)          # sin slot core: no mapea
    _legacy_result(factory, user, D, 96, feedback="dismissed")   # excluido
    _legacy_result(factory, user, f"{p}-inact", 95)              # excluido
    _legacy_result(factory, user, f"{p}-dup", 94)                # excluido

    # Perfil con set SIN congelar: NO se mide (frozen_at es el gate de §4).
    pid2 = _mk_profile(factory, str(uuid.uuid4()))
    _mk_frozen_set(factory, pid2, {A: 3}, freeze=False)

    # cycle_id=None + now inyectado ⇒ resuelve el ciclo CERRADO más reciente.
    result = _compute(factory, cycle_id=None, now=AFTER)
    assert result["cycle_id"] == CYCLE.isoformat()
    assert result["profiles_measured"] == 1

    scope = f"profile:{pid}"
    # A MANO — DCG core = 7/log2(2) + 1/log2(3) + 0/log2(4) + 3/log2(5)
    #                  = 7 + 0.630930 + 0 + 1.292030 = 8.922959
    # IDCG (labels [3,2,1,0]) = 7 + 3/log2(3) + 1/log2(4) = 9.392789
    # nDCG = 8.922959 / 9.392789 = 0.949980
    ndcg = _metric_row(factory, "ndcg@10", scope)
    assert float(ndcg.value) == pytest.approx(0.949980, abs=1e-6)
    assert [f["rel"] for f in ndcg.details["feed"]] == [3, 1, 0, 2]
    assert ndcg.details["idcg"] == pytest.approx(9.392789, abs=1e-6)

    # A MANO — DCG legacy [2,3,0] = 3 + 7/log2(3) + 0 = 7.416508
    # nDCG legacy = 7.416508 / 9.392789 = 0.789596 (MISMO set y MISMO IDCG).
    leg = _metric_row(factory, "ndcg@10_legacy", scope)
    assert float(leg.value) == pytest.approx(0.789596, abs=1e-6)
    assert [t["rel"] for t in leg.details["top"]] == [2, 3, 0]
    assert leg.details["idcg"] == ndcg.details["idcg"]

    # overlap@10 = |{A,C,E,B} ∩ {B,A}| / 10 = 0.2 (X sin slot no interseca;
    # el denominador es FIJO = 10 por fórmula).
    ov = _metric_row(factory, "overlap@10", scope)
    assert float(ov.value) == pytest.approx(0.2)
    assert ov.details["interseccion"] == 2
    assert ov.details["legacy_mapeados"] == 2

    # Gate de §6: 0.949980 >= max(0.60, 0.789596 - 0.05) = 0.739596 → OK.
    gates = _gates(factory)
    g = gates[f"ndcg@10::{scope}"]
    assert g["kind"] == "gate" and g["ok"] is True
    assert g["umbral"] == pytest.approx(0.739596, abs=1e-6)
    assert g["ndcg_legacy"] == pytest.approx(0.789596, abs=1e-6)
    assert gates[f"overlap@10::{scope}"]["kind"] == "alerta"
    assert gates[f"overlap@10::{scope}"]["ok"] is True

    # Recomputar (con force: el ciclo quedó SELLADO — P1-4) es IDEMPOTENTE
    # (upsert por PK): mismos valores, sin filas dup.
    n_before = _scalar(
        factory, "SELECT count(*) FROM shadow_cycle_metrics WHERE cycle_id = :c",
        c=CYCLE,
    )
    _compute(factory, force=True)
    assert _scalar(
        factory, "SELECT count(*) FROM shadow_cycle_metrics WHERE cycle_id = :c",
        c=CYCLE,
    ) == n_before
    assert float(_metric_row(factory, "ndcg@10", scope).value) == pytest.approx(
        0.949980, abs=1e-6
    )


def test_ndcg_no_penaliza_al_core_por_deduplicar_dos_refs_juzgados(db):
    """Regresión G3-A-P2-1: el IDCG se construía en espacio job_ref mientras el
    DCG del core se puntúa en espacio VACANTE. Con dos refs juzgados atachados
    a la MISMA vacante (el attach cross-source por url_normalized, justo lo que
    `reenlace_pct` existe para contar), el ideal presupuestaba DOS ranuras que
    el feed del core no puede llenar —solo tiene una vacante— mientras el
    legacy, que no dedujo, llenaba las dos y sacaba 1.0. Como el umbral se ata
    al legacy (legacy − 0.05), el core SUSPENDÍA el gate por deduplicar BIEN:
    0.613147 vs umbral 0.95, y la racha de 7 ciclos a cero. Ahora cada nDCG se
    normaliza con el ideal de SU espacio."""
    factory = db
    mp = _seed_model_policy(factory)
    user = uuid.uuid4()
    pid = _mk_profile(factory, str(user))
    src_a = _mk_source(factory, "legacy:ndcgdup-a")
    src_b = _mk_source(factory, "legacy:ndcgdup-b")
    p = uuid.uuid4().hex[:6]
    A, B = f"{p}-a", f"{p}-b"

    _mk_frozen_set(factory, pid, {A: 3, B: 3})
    # A y B, en DOS fuentes legacy distintas, atachados a la MISMA vacante.
    vac, _, _ = _mk_slot(factory, src_a, A)
    _mk_slot(factory, src_b, B, vacancy_id=vac)
    _mk_eval(factory, mp, pid, vac, 90)  # feed core = [V]
    for h in (A, B):
        _legacy_job(factory, h)
    _legacy_result(factory, user, A, 99)  # legacy visible = [A, B]
    _legacy_result(factory, user, B, 98)

    _compute(factory)
    scope = f"profile:{pid}"
    ndcg = _metric_row(factory, "ndcg@10", scope)
    leg = _metric_row(factory, "ndcg@10_legacy", scope)
    # A MANO — core: una ranura, rel 3 ⇒ dcg = idcg = 7 ⇒ 1.0 (antes:
    # 7 / 11.416508 = 0.613147 contra el ideal de DOS ranuras).
    assert float(ndcg.value) == pytest.approx(1.0)
    assert ndcg.details["idcg"] == pytest.approx(7.0, abs=1e-6)
    assert ndcg.details["espacio_idcg"] == "vacante"
    assert ndcg.details["refs_juzgados"] == 2
    assert ndcg.details["vacantes_juzgadas"] == 1
    # El legacy sigue midiéndose en espacio REF, con SU ideal (dos ranuras).
    assert ndcg.details["idcg_ref"] == pytest.approx(11.416508, abs=1e-6)
    assert leg.details["idcg"] == pytest.approx(11.416508, abs=1e-6)
    assert float(leg.value) == pytest.approx(1.0)
    g = _gates(factory)[f"ndcg@10::{scope}"]
    assert g["umbral"] == pytest.approx(0.95)
    assert g["ok"] is True  # antes: False — suspenso por deduplicar bien

    # El colapso queda VISIBLE en el informe legible (si no, un ndcg bajo
    # parecería un fallo de ranking y es deduplicación).
    async def report():
        async with factory() as s:
            return await metrics.render_report(s, CYCLE)

    rep = _run(report())
    assert "COLAPSAN" in rep
    # G4-P3-3: aquí el colapso es REAL y no hay refs ausentes.
    assert ndcg.details["refs_colapsados_por_attach"] == 1
    assert ndcg.details["refs_sin_vacante"] == 0
    assert "1 ref(s) juzgados COLAPSAN" in rep


def test_g4_el_informe_no_afirma_un_attach_que_no_ocurrio(db):
    """Regresión G4-P3-3: `colapso = refs_juzgados − vacantes_juzgadas` suma
    DOS poblaciones distintas —los refs colapsados por attach Y los que el
    core NO TIENE (sin slot legacy: `map_job_refs_to_vacancies` los deja fuera
    por contrato)— y la línea las etiquetaba todas como «COLAPSAN por attach».
    Es el ÚNICO rastro legible de por qué el ideal del core encogió, y mentía
    en la dirección tranquilizadora: afirmaba deduplicación donde hay AUSENCIA
    de corpus. Aquí no hay un solo attach en todo el corpus."""
    factory = db
    mp = _seed_model_policy(factory)
    user = uuid.uuid4()
    pid = _mk_profile(factory, str(user))
    src = _mk_source(factory, "legacy:g4inf")
    p = uuid.uuid4().hex[:6]
    A, X = f"{p}-a", f"{p}-x"

    # Set congelado con DOS juicios; el core solo tiene vacante para A. X no
    # se atacha con nadie: sencillamente NO está en el core.
    _mk_frozen_set(factory, pid, {A: 3, X: 3})
    vac_a = _mk_slot(factory, src, A)[0]
    _mk_eval(factory, mp, pid, vac_a, 90)
    for h in (A, X):
        _legacy_job(factory, h)
    _legacy_result(factory, user, A, 99)
    _legacy_result(factory, user, X, 98)

    _compute(factory)

    async def report():
        async with factory() as s:
            return await metrics.render_report(s, CYCLE)

    linea = [ln for ln in _run(report()).splitlines() if "COLAPSAN" in ln]
    assert linea, "el informe tiene que seguir explicando por qué encogió el ideal"
    # Antes del fix: «1 ref(s) juzgados COLAPSAN por attach…» — falso.
    assert "0 ref(s) juzgados COLAPSAN" in linea[0]
    assert "1 NO tienen vacante en el core" in linea[0]

    row = _metric_row(factory, "ndcg@10", scope=f"profile:{pid}")
    assert row.details["refs_juzgados"] == 2
    assert row.details["vacantes_juzgadas"] == 1
    assert row.details["refs_colapsados_por_attach"] == 0  # NINGÚN attach
    assert row.details["refs_sin_vacante"] == 1


def test_ndcg_unmeasurable_set_is_visible_not_green(db):
    """IDCG = 0 (set congelado sin etiquetas > 0): 0 con no_medible y gate
    ok=False — jamás un verde silencioso."""
    factory = db
    pid = _mk_profile(factory, str(uuid.uuid4()))
    _mk_frozen_set(factory, pid, {"zz-1": 0, "zz-2": 0})
    _compute(factory)
    row = _metric_row(factory, "ndcg@10", f"profile:{pid}")
    assert float(row.value) == 0.0
    assert row.details["no_medible"] is True
    g = _gates(factory)[f"ndcg@10::profile:{pid}"]
    assert g["ok"] is False and "no medible" in g["nota"]


# ------------------------------------------------- dedup precision/recall (§5)


def test_dedup_precision_recall_with_built_confusion_matrix(db):
    factory = db
    src = _mk_source(factory, "legacy:dedupfx")
    p = uuid.uuid4().hex[:6]

    def ref(n):
        return f"{p}-{n}"

    # TP attach: pa y pb comparten VACANTE (mapeo por encarnación CERRADA
    # incluida — los pares de duplicate_of apuntan a jobs muertos, §4).
    va, _, _ = _mk_slot(factory, src, ref("pa"))
    _mk_slot(factory, src, ref("pb"), active=False, vacancy_id=va)
    # TP por dedup_candidates state=pending.
    vc, _, _ = _mk_slot(factory, src, ref("pc"))
    vd, _, _ = _mk_slot(factory, src, ref("pd"))
    # FN: vacantes distintas sin candidato.
    ve, _, _ = _mk_slot(factory, src, ref("pe"))
    vf, _, _ = _mk_slot(factory, src, ref("pf"))
    # FP: verdict distinct pero candidato pending.
    vg, _, _ = _mk_slot(factory, src, ref("pg"))
    vh, _, _ = _mk_slot(factory, src, ref("ph"))
    # TN: verdict distinct con candidato RECHAZADO (state=rejected no cuenta).
    vi, _, _ = _mk_slot(factory, src, ref("pi"))
    vj, _, _ = _mk_slot(factory, src, ref("pj"))
    # Par no evaluable: pz sin slot core.
    _mk_slot(factory, src, ref("pk"))

    for a, b, st in ((vc, vd, "pending"), (vg, vh, "pending"), (vi, vj, "rejected")):
        _exec(
            factory,
            "INSERT INTO dedup_candidates (id, vacancy_a, vacancy_b, state) "
            "VALUES (:i, :a, :b, CAST(:st AS dedup_candidate_state))",
            {"i": uuid.uuid4(), "a": a, "b": b, "st": st},
        )
    pairs = [
        (ref("pa"), ref("pb"), "duplicate"),  # TP (misma vacante)
        (ref("pc"), ref("pd"), "duplicate"),  # TP (candidato pending)
        (ref("pe"), ref("pf"), "duplicate"),  # FN
        (ref("pg"), ref("ph"), "distinct"),   # FP
        (ref("pi"), ref("pj"), "distinct"),   # TN
        (ref("pk"), ref("pz"), "duplicate"),  # no evaluable (pz sin mapeo)
    ]
    for a, b, v in pairs:
        _exec(
            factory,
            # Cohorte del GATE (auditoría Nº2 BLOQUEANTE 1): _dedup_rows
            # SOLO puntúa DEDUP_EVAL_COHORT — con 'manual' este test no
            # vería ningún par.
            "INSERT INTO labeled_dedup_pairs (job_ref_a, job_ref_b, verdict, source) "
            "VALUES (:a, :b, :v, :src)",
            {"a": a, "b": b, "v": v, "src": labels.DEDUP_EVAL_COHORT},
        )

    _compute(factory)
    # A MANO: TP=2 FP=1 FN=1 TN=1 → precision = recall = 2/3 = 0.666667.
    prec = _metric_row(factory, "dedup_precision")
    rec = _metric_row(factory, "dedup_recall")
    assert float(prec.value) == pytest.approx(2 / 3, abs=1e-6)
    assert float(rec.value) == pytest.approx(2 / 3, abs=1e-6)
    for row in (prec, rec):
        assert (
            row.details["tp"], row.details["fp"], row.details["fn"],
            row.details["tn"], row.details["no_evaluables_sin_mapeo"],
        ) == (2, 1, 1, 1, 1)
    gates = _gates(factory)
    assert gates["dedup_precision"]["ok"] is False  # 0.667 < 0.95
    # Re-ratificación D2 (2026-08-26): el umbral vinculante de recall es 0.40
    # (techo demostrado del examen congelado) ⇒ 0.667 ahora es VERDE. La
    # mordida del umbral nuevo está en test_d2_recall_umbral_reratificado.
    assert gates["dedup_recall"]["ok"] is True      # 0.667 >= 0.40


def test_dedup_empty_oracle_is_no_data_not_green(db):
    """Regresión P1-2 (rev. externa): BD SIN labels — el revisor computó un
    ciclo con el oráculo VACÍO y dedup_precision/recall persistían 1.0
    (denominador 0 ⇒ "vacuamente cierto") poniendo los gates en VERDE. Ahora:
    centinela + details.no_data ⇒ gates en ROJO, y labels_ready (la
    precondición del oráculo) también en rojo."""
    factory = db
    _compute(factory)
    for metric in ("dedup_precision", "dedup_recall"):
        row = _metric_row(factory, metric)
        assert float(row.value) == metrics.NO_DATA_VALUE  # JAMÁS 1.0
        assert row.details["no_data"] is True
        assert row.details["pares"] == 0
    lr = _metric_row(factory, "labels_ready")
    assert float(lr.value) == 0
    assert lr.details["sets_congelados_ok"] == 0
    assert lr.details["pares_dedup"] == 0
    gates = _gates(factory)
    for key in ("dedup_precision", "dedup_recall"):
        assert gates[key]["ok"] is False and gates[key]["kind"] == "gate"
        assert gates[key]["value"] is None and gates[key]["nota"] == "sin datos"
    assert gates["labels_ready"]["ok"] is False
    assert gates["labels_ready"]["kind"] == "gate"


def test_dedup_recall_informativo_por_cohorte_no_altera_el_veredicto(db):
    """Estrato positivo §4.2 (2026-08-25): el ciclo publica dedup_recall POR
    COHORTE adicional registrada en labeled_dedup_cohorts (scope
    cohort:<source>) como fila INFORMATIVA — se publica, no aprueba. El
    veredicto vinculante sigue siendo SOLO el del holdout: un estrato en 0.5
    (muy por debajo de DEDUP_RECALL_MIN=0.90) queda [alerta] con ok=True y
    no pone nada en rojo ni resetea la racha; una cohorte registrada VACÍA
    tampoco (sin datos, también en verde)."""
    factory = db
    src = _mk_source(factory, "legacy:stratmx")
    p = uuid.uuid4().hex[:6]

    def ref(n):
        return f"{p}-{n}"

    # Holdout (vinculante): 2 TP por attach ⇒ precision = recall = 1.0.
    va, _, _ = _mk_slot(factory, src, ref("ha"))
    _mk_slot(factory, src, ref("hb"), active=False, vacancy_id=va)
    vc, _, _ = _mk_slot(factory, src, ref("hc"))
    _mk_slot(factory, src, ref("hd"), active=False, vacancy_id=vc)
    holdout_pairs = ((ref("ha"), ref("hb")), (ref("hc"), ref("hd")))
    # Estrato (informativo): 1 TP + 1 FN ⇒ recall 0.5, bajo el umbral del gate.
    ve, _, _ = _mk_slot(factory, src, ref("sa"))
    _mk_slot(factory, src, ref("sb"), active=False, vacancy_id=ve)
    _mk_slot(factory, src, ref("sc"))
    _mk_slot(factory, src, ref("sd"))
    stratum_pairs = ((ref("sa"), ref("sb")), (ref("sc"), ref("sd")))
    for pairs, cohorte in (
        (holdout_pairs, labels.DEDUP_EVAL_COHORT),
        (stratum_pairs, stratum.POSITIVE_STRATUM_COHORT),
    ):
        for a, b in pairs:
            _exec(
                factory,
                "INSERT INTO labeled_dedup_pairs "
                "(job_ref_a, job_ref_b, verdict, source) "
                "VALUES (:a, :b, 'duplicate', :src)",
                {"a": a, "b": b, "src": cohorte},
            )
    # Cohortes REGISTRADAS (frozen_at NULL): el estrato y una vacía. Solo lo
    # registrado en labeled_dedup_cohorts produce fila informativa.
    for cohorte in (stratum.POSITIVE_STRATUM_COHORT, "positive-stratum-vacia"):
        _exec(
            factory,
            "INSERT INTO labeled_dedup_cohorts (source) VALUES (:s)",
            {"s": cohorte},
        )

    _compute(factory)
    scope = f"cohort:{stratum.POSITIVE_STRATUM_COHORT}"
    info = _metric_row(factory, "dedup_recall", scope=scope)
    assert float(info.value) == 0.5
    assert info.details["vinculante"] is False
    assert (info.details["tp"], info.details["fn"], info.details["pares"]) == (1, 1, 2)
    assert info.details["cohorte"] == stratum.POSITIVE_STRATUM_COHORT
    # la fila VINCULANTE (scope global) sigue siendo la del holdout, intacta
    hold = _metric_row(factory, "dedup_recall")
    assert float(hold.value) == 1.0
    assert hold.details["cohorte"] == labels.DEDUP_EVAL_COHORT
    assert "vinculante" not in hold.details
    vacia = _metric_row(factory, "dedup_recall", scope="cohort:positive-stratum-vacia")
    assert float(vacia.value) == metrics.NO_DATA_VALUE
    assert vacia.details["no_data"] is True

    gates = _gates(factory)
    assert gates["dedup_recall"]["ok"] is True  # el veredicto: SOLO holdout
    g_info = gates[f"dedup_recall::{scope}"]
    assert g_info["kind"] == "alerta" and g_info["ok"] is True
    assert g_info["value"] == 0.5 and g_info["umbral"] is None
    g_vacia = gates["dedup_recall::cohort:positive-stratum-vacia"]
    assert g_vacia["ok"] is True and g_vacia["value"] is None
    # NINGUNA fila de cohorte entra como [gate]: no puede alterar el veredicto
    assert all(
        gates[k]["kind"] == "alerta" for k in gates if "::cohort:" in k
    )


def test_labels_ready_green_with_dod_oracle(db):
    """P1-2: con el oráculo al DoD de B-03 (>= 2 sets CONGELADOS con >= 30
    juicios cada uno, >= 50 pares dedup, >= 20 MAPEABLES a vacantes core)
    labels_ready pasa a VERDE y dedup se computa con valores reales."""
    factory = db
    src = _mk_source(factory, "legacy:lrfx")
    p = uuid.uuid4().hex[:6]
    pid1 = _mk_profile(factory, str(uuid.uuid4()))
    pid2 = _mk_profile(factory, str(uuid.uuid4()))
    _mk_frozen_set(factory, pid1, {f"{p}-a{i:02d}": i % 4 for i in range(30)})
    _mk_frozen_set(
        factory, pid2, {f"{p}-b{i:02d}": i % 4 for i in range(30)}, name="ronda-2"
    )
    # 25 pares MAPEADOS (ambos refs con slot y MISMA vacante ⇒ TP) + 25 sin
    # slot core (no mapeables): 50 pares >= 50, 25 mapeables >= 20.
    for i in range(25):
        ra, rb = f"{p}-m{i:02d}a", f"{p}-m{i:02d}b"
        vid, _, _ = _mk_slot(factory, src, ra)
        _mk_slot(factory, src, rb, active=False, vacancy_id=vid)
        _exec(
            factory,
            "INSERT INTO labeled_dedup_pairs (job_ref_a, job_ref_b, verdict, "
            "source) VALUES (:a, :b, 'duplicate', '"
            + labels.DEDUP_EVAL_COHORT + "')",
            {"a": ra, "b": rb},
        )
    for i in range(25):
        _exec(
            factory,
            "INSERT INTO labeled_dedup_pairs (job_ref_a, job_ref_b, verdict, "
            "source) VALUES (:a, :b, 'duplicate', '"
            + labels.DEDUP_EVAL_COHORT + "')",
            {"a": f"{p}-u{i:02d}a", "b": f"{p}-u{i:02d}b"},
        )

    _compute(factory)
    lr = _metric_row(factory, "labels_ready")
    assert float(lr.value) == 1
    assert lr.details["sets_congelados"] == 2
    assert lr.details["sets_congelados_ok"] == 2
    assert lr.details["pares_dedup"] == 50
    assert lr.details["pares_mapeables"] == 25
    gates = _gates(factory)
    assert gates["labels_ready"]["ok"] is True
    # dedup con oráculo real: 25 TP / 0 FP / 0 FN ⇒ precision = recall = 1.0
    # (esta vez un 1.0 DEMOSTRADO, no vacuo).
    assert float(_metric_row(factory, "dedup_precision").value) == 1.0
    assert float(_metric_row(factory, "dedup_recall").value) == 1.0
    assert gates["dedup_precision"]["ok"] is True
    assert gates["dedup_recall"]["ok"] is True


def test_labels_ready_red_with_two_sets_one_profile(db):
    """REGRESIÓN P1 rev. externa integral: dos sets congelados del MISMO perfil (cada uno >= 30
    juicios) NO satisfacen el DoD B-03 (">= 2 PERFILES reales"). labels_ready debe quedar ROJO: el
    gate cuenta PERFILES distintos (perfiles_ok=1), no sets (sets_congelados_ok=2)."""
    factory = db
    src = _mk_source(factory, "legacy:lr1p")
    p = uuid.uuid4().hex[:6]
    pid = _mk_profile(factory, str(uuid.uuid4()))  # UN solo perfil, DOS sets
    _mk_frozen_set(factory, pid, {f"{p}-a{i:02d}": i % 4 for i in range(30)})
    _mk_frozen_set(
        factory, pid, {f"{p}-b{i:02d}": i % 4 for i in range(30)}, name="ronda-2"
    )
    # Pares dedup suficientes (>=50, >=20 mapeables): el ROJO viene SOLO del conteo de perfiles.
    for i in range(25):
        ra, rb = f"{p}-m{i:02d}a", f"{p}-m{i:02d}b"
        vid, _, _ = _mk_slot(factory, src, ra)
        _mk_slot(factory, src, rb, active=False, vacancy_id=vid)
        _exec(
            factory,
            "INSERT INTO labeled_dedup_pairs (job_ref_a, job_ref_b, verdict, "
            "source) VALUES (:a, :b, 'duplicate', '"
            + labels.DEDUP_EVAL_COHORT + "')",
            {"a": ra, "b": rb},
        )
    for i in range(25):
        _exec(
            factory,
            "INSERT INTO labeled_dedup_pairs (job_ref_a, job_ref_b, verdict, "
            "source) VALUES (:a, :b, 'duplicate', '"
            + labels.DEDUP_EVAL_COHORT + "')",
            {"a": f"{p}-u{i:02d}a", "b": f"{p}-u{i:02d}b"},
        )
    _compute(factory)
    lr = _metric_row(factory, "labels_ready")
    assert float(lr.value) == 0  # ROJO: 1 solo perfil
    assert lr.details["sets_congelados_ok"] == 2  # 2 sets válidos…
    assert lr.details["perfiles_ok"] == 1  # …pero de UN solo perfil
    gates = _gates(factory)
    assert gates["labels_ready"]["ok"] is False


def test_labels_ready_red_when_effective_set_is_small(db):
    """REGRESIÓN P1 rev. externa integral RONDA 2: labels_ready debe usar el set EFECTIVO (el MÁS
    RECIENTE, el que MIDEN nDCG/falsos_negativos vía _measured_profiles), no 'cualquier set >=30'.
    Dos perfiles con un set VIEJO de 30 juicios + un set NUEVO de 1 juicio: el gate NO debe abrir
    (se mide sobre el set de 1 juicio → oráculo insuficiente, nDCG=1/recall=1 vacuos), aunque
    exista un set viejo válido."""
    factory = db
    src = _mk_source(factory, "legacy:lreff")
    p = uuid.uuid4().hex[:6]
    for who in ("a", "b"):
        pid = _mk_profile(factory, str(uuid.uuid4()))
        _mk_frozen_set(
            factory, pid, {f"{p}-{who}old{i:02d}": i % 4 for i in range(30)}, name="ronda-1"
        )
        # Set NUEVO (más reciente) de UN solo juicio → es el EFECTIVO que se mide.
        _mk_frozen_set(factory, pid, {f"{p}-{who}new": 3}, name="ronda-2")
    # Pares dedup suficientes (>=50, >=20 mapeables): el ROJO viene SOLO del set efectivo pequeño.
    for i in range(25):
        ra, rb = f"{p}-m{i:02d}a", f"{p}-m{i:02d}b"
        vid, _, _ = _mk_slot(factory, src, ra)
        _mk_slot(factory, src, rb, active=False, vacancy_id=vid)
        _exec(
            factory,
            "INSERT INTO labeled_dedup_pairs (job_ref_a, job_ref_b, verdict, "
            "source) VALUES (:a, :b, 'duplicate', '"
            + labels.DEDUP_EVAL_COHORT + "')",
            {"a": ra, "b": rb},
        )
    for i in range(25):
        _exec(
            factory,
            "INSERT INTO labeled_dedup_pairs (job_ref_a, job_ref_b, verdict, "
            "source) VALUES (:a, :b, 'duplicate', '"
            + labels.DEDUP_EVAL_COHORT + "')",
            {"a": f"{p}-u{i:02d}a", "b": f"{p}-u{i:02d}b"},
        )
    _compute(factory)
    lr = _metric_row(factory, "labels_ready")
    assert float(lr.value) == 0  # ROJO: el set EFECTIVO de cada perfil tiene 1 juicio
    assert lr.details["sets_congelados_ok"] == 2  # existen 2 sets viejos >=30 (informativo)…
    assert lr.details["perfiles_ok"] == 0  # …pero 0 perfiles con set EFECTIVO >=30
    gates = _gates(factory)
    assert gates["labels_ready"]["ok"] is False


def test_labels_ready_gates_on_passed_snapshot_not_fresh_query(db):
    """REGRESIÓN P1 rev. externa integral RONDA 3: labels_ready debe gatear sobre el MISMO snapshot
    `measured_profiles` que midió las métricas (compute_cycle), NO re-consultar. Bajo READ COMMITTED
    una reactivación/congelado concurrente entre ambas consultas haría contar un perfil NO medido
    (falso verde). Discriminante: aunque la BD tenga 2 perfiles con set >=30, un snapshot PASADO
    vacío da perfiles_ok=0 (el código viejo re-consultaba y daría 2)."""
    factory = db
    p = uuid.uuid4().hex[:6]
    for who in ("a", "b"):
        pid = _mk_profile(factory, str(uuid.uuid4()))
        _mk_frozen_set(factory, pid, {f"{p}-{who}{i:02d}": i % 4 for i in range(30)})

    async def labels_ready_with(snapshot):
        async with factory() as s:
            _metric, _value, details, _merge = await metrics._labels_ready_row(s, snapshot)
            return details

    # Snapshot VACÍO → perfiles_ok=0 aunque existan 2 perfiles con set >=30 (NO re-consulta).
    assert asyncio.run(labels_ready_with([]))["perfiles_ok"] == 0

    # Snapshot con los 2 perfiles reales → perfiles_ok=2 (mismo helper, mismo estado).
    async def real_snapshot():
        async with factory() as s:
            return await metrics._measured_profiles(s)

    assert asyncio.run(labels_ready_with(asyncio.run(real_snapshot())))["perfiles_ok"] == 2


def test_inactive_profile_excluded_from_metrics_and_labels_ready(db):
    """Regresión NO-GO 2 (decisión delegada 2026-07-28): un perfil cuyo
    external_ref está INACTIVO (último estado `users` del staging aplicado —
    el MISMO mecanismo de exclusión del proyector, inactive_user_refs)
    (a) NO se mide: sin fila ndcg suya ni gate por perfil; (b) su set
    congelado NO cuenta para el >= 2 de labels_ready (evidencia vacua) y
    details.sets_excluidos_inactivos lo registra — el set se CONSERVA
    congelado e intacto. Re-activar (staging users is_active=true aplicado)
    lo devuelve a la medición y al conteo en el siguiente cómputo, sin
    re-congelar nada. Escenario del revisor: usuario de captura inactivo
    con set congelado inflando el requisito de >= 2 sets."""
    factory = db
    src = _mk_source(factory, "legacy:inafx")
    p = uuid.uuid4().hex[:6]
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    pid1 = _mk_profile(factory, str(u1))
    pid2 = _mk_profile(factory, str(u2))
    _mk_frozen_set(factory, pid1, {f"{p}-a{i:02d}": i % 4 for i in range(30)})
    _mk_frozen_set(
        factory, pid2, {f"{p}-b{i:02d}": i % 4 for i in range(30)}, name="ronda-2"
    )
    # Oráculo dedup al DoD (50 pares, 25 mapeables): labels_ready depende
    # SOLO del conteo de sets contables.
    for i in range(25):
        ra, rb = f"{p}-m{i:02d}a", f"{p}-m{i:02d}b"
        vid, _, _ = _mk_slot(factory, src, ra)
        _mk_slot(factory, src, rb, active=False, vacancy_id=vid)
        _exec(
            factory,
            "INSERT INTO labeled_dedup_pairs (job_ref_a, job_ref_b, verdict, "
            "source) VALUES (:a, :b, 'duplicate', '"
            + labels.DEDUP_EVAL_COHORT + "')",
            {"a": ra, "b": rb},
        )
    for i in range(25):
        _exec(
            factory,
            "INSERT INTO labeled_dedup_pairs (job_ref_a, job_ref_b, verdict, "
            "source) VALUES (:a, :b, 'duplicate', '"
            + labels.DEDUP_EVAL_COHORT + "')",
            {"a": f"{p}-u{i:02d}a", "b": f"{p}-u{i:02d}b"},
        )
    # u1 ACTIVO, u2 INACTIVO (último estado users por pk del staging aplicado).
    _stage_users(factory, [
        (1, "I", u1, True), (2, "I", u2, True), (3, "U", u2, False),
    ])

    r1 = _compute(factory)
    assert r1["profiles_measured"] == 1  # u2 fuera de la medición
    assert _metric_row(factory, "ndcg@10", f"profile:{pid1}") is not None
    assert _metric_row(factory, "ndcg@10", f"profile:{pid2}") is None
    lr = _metric_row(factory, "labels_ready")
    assert float(lr.value) == 0  # 1 set contable < 2: gate ROJO
    assert lr.details["sets_congelados"] == 2
    assert lr.details["sets_congelados_ok"] == 1
    assert lr.details["sets_excluidos_inactivos"] == 1
    assert lr.details["pares_dedup"] == 50
    assert lr.details["pares_mapeables"] == 25
    gates = _gates(factory)
    assert gates["labels_ready"]["ok"] is False
    assert f"ndcg@10::profile:{pid2}" not in gates  # sin fila = sin gate suyo
    # El set del inactivo se CONSERVA congelado e INTACTO (inmutable, §4).
    assert _scalar(
        factory,
        "SELECT count(*) FROM labeled_judgments j "
        "JOIN labeled_sets ls ON ls.id = j.set_id WHERE ls.profile_id = :p",
        p=pid2,
    ) == 30
    assert _scalar(
        factory, "SELECT frozen_at FROM labeled_sets WHERE profile_id = :p",
        p=pid2,
    ) is not None

    # RE-ACTIVACIÓN aplicada ⇒ el siguiente ciclo lo mide y lo cuenta.
    _stage_users(factory, [(4, "U", u2, True)])
    cycle2 = CYCLE + timedelta(days=1)
    r2 = _compute(factory, cycle_id=cycle2, now=AFTER + timedelta(days=1))
    assert r2["profiles_measured"] == 2
    assert _metric_row(
        factory, "ndcg@10", f"profile:{pid2}", cycle=cycle2
    ) is not None
    lr2 = _metric_row(factory, "labels_ready", cycle=cycle2)
    assert float(lr2.value) == 1
    assert lr2.details["sets_congelados_ok"] == 2
    assert lr2.details["sets_excluidos_inactivos"] == 0
    assert _gates(factory, cycle=cycle2)["labels_ready"]["ok"] is True


# ------------------------------------------- inmutabilidad de ciclos (P1-4)


def test_sealed_cycle_immutable_and_force_recompute_resets_streak(db):
    """Regresión P1-4 (rev. externa): compute_cycle(cycle_id=pasado) sobre un
    ciclo SELLADO recalculaba con el estado ACTUAL y podía reescribir un
    ROJO histórico. Ahora: (a) sin force NO se recomputa (el rojo sellado
    queda intacto); (b) con force=True recomputa y estampa
    details.recomputed_at en TODAS las filas; (c) gate_status trata el
    ciclo recomputado como NO computable — la racha NO lo cuenta aunque el
    gate rojo haya pasado a verde."""
    factory = db
    src = _mk_source(factory, "legacy:immfx")
    p = uuid.uuid4().hex[:6]
    # Estado del cierre del ciclo: 1 job legacy vivo SIN slot core ⇒
    # perdida = 1 (gate ROJO), sellado.
    _legacy_job(factory, f"{p}-hueco")
    r1 = _compute(factory, cycle_id=CYCLE)
    assert "skipped_sealed" not in r1
    assert float(_metric_row(factory, "perdida").value) == 1
    assert _gates(factory)["perdida"]["ok"] is False

    # El hueco se "repara" DESPUÉS del sellado (estado ACTUAL != histórico).
    _mk_slot(factory, src, f"{p}-hueco")

    # (a) SIN force: INMUTABLE — nada se reescribe, el rojo sigue rojo.
    r2 = _compute(factory, cycle_id=CYCLE)
    assert r2["skipped_sealed"] is True and r2["metrics"] == {}
    row = _metric_row(factory, "perdida")
    assert float(row.value) == 1  # el escenario del revisor ya NO reescribe
    assert "recomputed_at" not in row.details
    assert _gates(factory)["perdida"]["ok"] is False

    # (b) CON force: recomputa (perdida 1 → 0) y lo deja TRAZADO en TODAS
    # las filas del ciclo.
    r3 = _compute(factory, cycle_id=CYCLE, force=True)
    assert r3["recomputed_at"]
    assert float(_metric_row(factory, "perdida").value) == 0
    stamped = _rows(
        factory,
        "SELECT details FROM shadow_cycle_metrics WHERE cycle_id = :c",
        c=CYCLE,
    )
    assert stamped and all(r.details.get("recomputed_at") for r in stamped)

    # (c) la racha NO cuenta un ciclo recomputado: resetea igual que un rojo.
    async def status():
        async with factory() as s:
            return await gate.gate_status(s, now=AFTER)

    st = _run(status())
    assert st["last_cycle"] == CYCLE.isoformat()
    assert st["consecutive_ok"] == 0
    assert st["per_cycle"][0]["recomputado"] is True
    assert st["per_cycle"][0]["ok"] is False

    async def report():
        async with factory() as s:
            return await gate.render_gate_report(s, now=AFTER)

    assert "RECOMPUTADO" in _run(report())  # el informe lo señala


# --------------------------------------------------- falsos_negativos (§5/§6)


def test_falsos_negativos_strict_mode_zero_allowed(db):
    factory = db
    mp = _seed_model_policy(factory)
    pid = _mk_profile(factory, str(uuid.uuid4()))
    src = _mk_source(factory, "legacy:fnfx")
    p = uuid.uuid4().hex[:6]
    f1, f2, f3, f4 = (f"{p}-{n}" for n in ("f1", "f2", "f3", "f4"))
    # f1 vivo y EN el feed; f2 vivo AUSENTE del feed; f3 sin slot core
    # (no presente); f4 mapea a vacante ARCHIVADA (no presente).
    v1, _, _ = _mk_slot(factory, src, f1)
    _mk_slot(factory, src, f2)
    _mk_slot(factory, src, f4, archived=True)
    _mk_frozen_set(factory, pid, {f1: 3, f2: 2, f3: 2, f4: 2, f"{p}-r1": 1})
    _mk_eval(factory, mp, pid, v1, 90)

    _compute(factory)
    row = _metric_row(factory, "falsos_negativos", f"profile:{pid}")
    # A MANO: presentes = {f1, f2}; ausentes = {f2} → 1/2 = 0.5.
    assert float(row.value) == pytest.approx(0.5)
    assert row.details["modo"] == "estricto_0"  # 4 juicios rel>=2 < 50
    assert row.details["rel2_en_set"] == 4
    assert row.details["presentes_en_corpus"] == 2
    assert row.details["ausentes"] == [f2]
    g = _gates(factory)[f"falsos_negativos::profile:{pid}"]
    assert (g["kind"], g["umbral"], g["ok"]) == ("gate", 0.0, False)


def test_falsos_negativos_zero_present_is_no_data_not_green(db):
    """Regresión G1-P3-4: con 0 juicios rel>=2 PRESENTES en el corpus (todos
    archivados o sin slot core), la métrica valía 0.0 y el gate estricto
    (<=0.0) aprobaba — el «0/0 vacuamente verde» que la política NO_DATA
    (P1-2) prohíbe para dedup. Ahora: centinela + details.no_data y gate
    «sin datos» (ok False)."""
    factory = db
    pid = _mk_profile(factory, str(uuid.uuid4()))
    src = _mk_source(factory, "legacy:fnnd")
    p = uuid.uuid4().hex[:6]
    f1, f2 = f"{p}-a", f"{p}-b"
    _mk_slot(factory, src, f1, archived=True)  # mapea a vacante ARCHIVADA
    # f2 sin slot core → tampoco presente
    _mk_frozen_set(factory, pid, {f1: 2, f2: 3})

    _compute(factory)
    row = _metric_row(factory, "falsos_negativos", f"profile:{pid}")
    assert float(row.value) == metrics.NO_DATA_VALUE  # jamás 0.0 vacuo
    assert row.details["no_data"] is True
    assert row.details["presentes_en_corpus"] == 0
    g = _gates(factory)[f"falsos_negativos::profile:{pid}"]
    assert g["kind"] == "gate"
    assert g["value"] is None and g["ok"] is False and g["nota"] == "sin datos"


def test_falsos_negativos_ratio_mode_two_percent(db):
    factory = db
    mp = _seed_model_policy(factory)
    pid = _mk_profile(factory, str(uuid.uuid4()))
    src = _mk_source(factory, "legacy:fnratio")
    p = uuid.uuid4().hex[:6]
    refs = [f"{p}-{i:03d}" for i in range(60)]  # 60 juicios rel>=2 ⇒ modo 2%
    _mk_frozen_set(factory, pid, {r: 2 for r in refs})
    for i, r in enumerate(refs):
        vid, _, _ = _mk_slot(factory, src, r)
        if i > 0:  # el 000 queda FUERA del feed: 1 ausente de 60
            _mk_eval(factory, mp, pid, vid, 90 - i * 0.5)

    _compute(factory)
    row = _metric_row(factory, "falsos_negativos", f"profile:{pid}")
    # A MANO: 1/60 = 0.016667 <= 0.02 → gate OK en modo ratio.
    assert float(row.value) == pytest.approx(1 / 60, abs=1e-6)
    assert row.details["modo"] == "ratio_2pct"
    g = _gates(factory)[f"falsos_negativos::profile:{pid}"]
    assert (g["umbral"], g["ok"]) == (metrics.FN_MAX_RATIO, True)


# ------------------------------------------------ perdida / no_ingeribles (§5)


def test_perdida_zero_on_healthy_mirror_and_gap_when_injected(db):
    factory = db
    src = _mk_source(factory, "legacy:perdfx")
    other = _mk_source(factory, f"plain{uuid.uuid4().hex[:6]}")
    p = uuid.uuid4().hex[:6]
    l1, l2, l3 = f"{p}-l1", f"{p}-l2", f"{p}-l3"
    for h in (l1, l2, l3):
        _legacy_job(factory, h)
        _mk_slot(factory, src, h)
    _legacy_job(factory, f"{p}-inact", active=False)   # jamás en el minuendo
    _legacy_job(factory, f"{p}-dup", dup=l1)           # jamás en el minuendo
    # Cuarentenables del sink: url > MAX_URL_LEN y url NULL → no_ingeribles.
    # 2048 chars EXACTOS sin path: cabe en la columna (2048) pero la
    # NORMALIZADA crece a 2049 ⇒ cuarentenable (frontera core0028)
    _legacy_job(factory, f"{p}-longurl",
                url="https://leg?" + "x" * (2048 - len("https://leg?")))
    _legacy_job(factory, f"{p}-nourl", url=None)
    # Ruido que NO debe contar en el sustraendo: slot cerrado y fuente ajena.
    _legacy_job(factory, f"{p}-closed", active=False)
    _mk_slot(factory, src, f"{p}-closed", active=False)
    _mk_slot(factory, other, f"{p}-ajeno")

    _compute(factory)
    row = _metric_row(factory, "perdida")
    # A MANO: 3 vivos ingeribles − 3 slots activos + 0 backlog = 0.
    assert float(row.value) == 0
    assert row.details["legacy_activos_ingeribles"] == 3
    assert row.details["slots_legacy_activos"] == 3
    assert row.details["staging_sin_aplicar_1h"] == 0
    ni = _metric_row(factory, "no_ingeribles")
    assert float(ni.value) == 2  # longurl + nourl, APARTE del minuendo
    gates = _gates(factory)
    assert gates["perdida"]["ok"] is True and gates["perdida"]["kind"] == "gate"
    assert gates["no_ingeribles"]["ok"] is False  # alerta > 0
    assert gates["no_ingeribles"]["kind"] == "alerta"
    assert gates["reenlace_pct"]["value"] == 0.0  # sin encarnaciones tocadas

    # HUECO inyectado: job vivo sin slot + backlog viejo (> 1h) sin aplicar;
    # lo sin aplicar RECIENTE no cuenta (gracia de 1h). El ciclo quedó
    # sellado por el primer compute ⇒ el recomputo exige force (P1-4).
    _legacy_job(factory, f"{p}-hueco")
    _exec(
        factory,
        "INSERT INTO shadow_change_log (lsn, seq_in_tx, src_table, op, pk, "
        "payload, received_at) VALUES "
        "(9001, 0, 'jobs', 'I', 'x-old', CAST('{}' AS jsonb), :old), "
        "(9002, 0, 'jobs', 'I', 'x-new', CAST('{}' AS jsonb), :new)",
        {"old": AFTER - timedelta(hours=2), "new": AFTER - timedelta(minutes=10)},
    )
    _compute(factory, force=True)
    row = _metric_row(factory, "perdida")
    # A MANO: 4 vivos − 3 slots + 1 backlog viejo = 2.
    assert float(row.value) == 2
    assert row.details["staging_sin_aplicar_1h"] == 1
    assert _gates(factory)["perdida"]["ok"] is False  # gate estricto == 0


def test_perdida_quarantine_frontier_matches_sink(db):
    """La partición vivos/no_ingeribles usa la MISMA ruta de cuarentena del
    sink: una url de exactamente 2048 con path VACÍO (normalize_url añade
    '/' → 2049) y una con corchete IPv6 desbalanceado (ValueError) van a
    no_ingeribles y JAMÁS al minuendo — perdida=0, sin falso positivo
    permanente del gate estricto."""
    factory = db
    src = _mk_source(factory, "legacy:frontfx")
    p = uuid.uuid4().hex[:6]
    ok = f"{p}-ok"
    _legacy_job(factory, ok)
    _mk_slot(factory, src, ok)
    # Borde INGERIBLE: exactamente 2048 CON path — la normalizada no crece.
    edge = f"{p}-edge"
    edge_url = "https://leg/" + "x" * (2048 - len("https://leg/"))
    assert len(edge_url) == len(metrics.normalize_url(edge_url)) == 2048
    _legacy_job(factory, edge, url=edge_url)
    _mk_slot(factory, src, edge)
    # Cuarentenable 1: exactamente 2048 con path VACÍO → normalizada 2049
    # (el sink la rechaza por len(url_norm) > MAX_URL_LEN; la cruda pasa).
    grow_url = "https://x?" + "a" * 2038
    # C8-B.1: espejo en BYTES con mordida — CJK legal en chars, >2048 bytes
    cjk_url = "https://x/" + "\u4e2d" * 800
    assert len(cjk_url) < 2048 and len(cjk_url.encode()) > 2048
    assert metrics._sink_quarantines_url(cjk_url) is True
    assert len(grow_url) == 2048
    assert len(metrics.normalize_url(grow_url)) == 2049
    _legacy_job(factory, f"{p}-grow", url=grow_url)
    # Cuarentenable 2: corchete desbalanceado → ValueError('Invalid IPv6
    # URL') de normalize_url — la ruta try/except del sink.
    _legacy_job(factory, f"{p}-ipv6", url="https://[inv")

    _compute(factory)
    row = _metric_row(factory, "perdida")
    # A MANO: 2 vivos ingeribles (ok + edge) − 2 slots activos + 0 = 0.
    assert float(row.value) == 0
    assert row.details["legacy_activos_ingeribles"] == 2
    assert row.details["slots_legacy_activos"] == 2
    ni = _metric_row(factory, "no_ingeribles")
    assert float(ni.value) == 2  # grow (2049 normalizada) + ipv6 (ValueError)
    assert sorted(ni.details["cuarentenados_muestra"]) == sorted(
        [f"{p}-grow", f"{p}-ipv6"]
    )
    gates = _gates(factory)
    assert gates["perdida"]["ok"] is True         # sin falso perdida>0
    assert gates["no_ingeribles"]["ok"] is False  # alerta > 0, APARTE


# ------------------------------------------- outbox_lag_p99 + muestreador (§5)


def test_sampler_appends_and_p99_exact(db):
    factory = db
    # EVENTO no entregado con 100 s de edad (P2-6: el lag es la edad del
    # evento — integration_outbox.created_at —, no el próximo reintento):
    # oldest_pending_s ≈ 100 aunque next_attempt_at esté en el FUTURO.
    eid = uuid.uuid4()
    _exec(
        factory,
        "INSERT INTO integration_outbox (event_id, aggregate, aggregate_id, "
        "version, type, payload, created_at) VALUES (:e, 'match_evaluation', "
        "'k', 1, 'match.evaluated', CAST('{}' AS jsonb), "
        "clock_timestamp() - make_interval(secs => 100))",
        {"e": eid},
    )
    _exec(
        factory,
        "INSERT INTO integration_outbox_deliveries (event_id, destination, "
        "next_attempt_at) VALUES (:e, 'bff', clock_timestamp() + "
        "make_interval(secs => 300))",
        {"e": eid},
    )

    async def sample(now):
        async with factory() as s:
            r = await metrics.sample_outbox_lag(s, now=now)
            await s.commit()
            return r

    t0 = CSTART + timedelta(hours=1)
    r1 = _run(sample(t0))
    assert r1["cycle_id"] == CYCLE.isoformat()
    assert r1["samples"] == 1
    assert 99 <= r1["oldest_pending_s"] <= 110
    assert r1["dead_total"] == 0  # P2-6: cada sample lleva dead_total
    r2 = _run(sample(t0 + timedelta(minutes=5)))
    assert r2["samples"] == 2  # appendea, no pisa

    row = _metric_row(factory, "outbox_lag_p99")
    assert float(row.value) == metrics.NO_DATA_VALUE  # aún sin computar
    assert len(row.details["samples"]) == 2
    assert row.details["samples"][0]["ts"].startswith("2026-07-20T")
    assert row.details["samples"][0]["dead_total"] == 0

    # p99 EXACTO (percentile_cont): samples deterministas [300, 600, 900, 1200]
    # → 0.99·3 = 2.97 → 900 + 0.97·300 = 1191.0 (>900, umbral 2026-08-22).
    _exec(
        factory,
        "UPDATE shadow_cycle_metrics SET details = jsonb_set(details, "
        "'{samples}', CAST(:j AS jsonb)) WHERE cycle_id = :c AND metric = :m",
        {
            "j": json.dumps(
                [{"ts": "t", "oldest_pending_s": v} for v in (300, 600, 900, 1200)]
            ),
            "c": CYCLE, "m": "outbox_lag_p99",
        },
    )
    _compute(factory)
    row = _metric_row(factory, "outbox_lag_p99")
    assert float(row.value) == pytest.approx(1191.0)
    assert row.details["samples_count"] == 4
    assert len(row.details["samples"]) == 4  # merge: los samples SOBREVIVEN
    g = _gates(factory)["outbox_lag_p99"]
    assert g["ok"] is False  # 1191 > 900


def test_outbox_lag_without_samples_is_no_data_and_gate_fails(db):
    factory = db
    _compute(factory)
    row = _metric_row(factory, "outbox_lag_p99")
    assert float(row.value) == metrics.NO_DATA_VALUE  # el "NULL" del contrato
    assert row.details["no_data"] is True
    g = _gates(factory)["outbox_lag_p99"]
    assert g["value"] is None and g["ok"] is False and g["nota"] == "sin datos"
    # outbox_dead SIEMPRE es computable (conteo actual): sin dead ⇒ 0, verde.
    dead = _metric_row(factory, "outbox_dead")
    assert float(dead.value) == 0
    assert _gates(factory)["outbox_dead"]["ok"] is True


# ------------------------------------------------------- outbox_dead (P2-6)


def _insert_dead(factory, eid, dead_at, dest="bff"):
    """Un evento + delivery en dead con `dead_at` inyectado (lo que delivery
    estampa en la transición real desde core0030)."""
    _exec(
        factory,
        "INSERT INTO integration_outbox (event_id, aggregate, aggregate_id, "
        "version, type, payload) VALUES (:e, 'match_evaluation', :k, 1, "
        "'match.evaluated', CAST('{}' AS jsonb))",
        {"e": eid, "k": f"k-{eid}"},
    )
    _exec(
        factory,
        "INSERT INTO integration_outbox_deliveries (event_id, destination, "
        "state, attempts, last_error, dead_at) "
        "VALUES (:e, :d, 'dead', 8, 'boom', :t)",
        {"e": eid, "d": dest, "t": dead_at},
    )


def test_outbox_dead_gate_red_on_dead_letter(db):
    """Regresión P2-6 (rev. externa parte 2): un evento en DEAD-LETTER
    durante el ciclo (dead_at dentro de la ventana) pone `outbox_dead` en
    ROJO — el muestreador deja la traza (dead_total) y el conteo por
    ventana puntúa."""
    factory = db
    eid = uuid.uuid4()
    _insert_dead(factory, eid, CSTART + timedelta(hours=2))

    async def sample(now):
        async with factory() as s:
            r = await metrics.sample_outbox_lag(s, now=now)
            await s.commit()
            return r

    r = _run(sample(CSTART + timedelta(hours=2)))
    assert r["dead_total"] == 1  # el sample lo capturó dentro del ciclo

    _compute(factory)
    row = _metric_row(factory, "outbox_dead")
    assert float(row.value) == 1
    assert row.details["dead_en_ciclo"] == 1
    assert row.details["dead_actual"] == 1
    assert row.details["dead_max_muestras"] == 1
    g = _gates(factory)["outbox_dead"]
    assert g["kind"] == "gate" and g["ok"] is False  # ROJO: resetea la racha
    # dead NO cuenta en el lag (no es pending/inflight): métricas separadas.
    lag = _metric_row(factory, "outbox_lag_p99")
    assert lag.details["samples"][0]["oldest_pending_s"] == 0.0


def test_outbox_dead_historic_does_not_latch_cycle(db):
    """Regresión G1-P2-3: `outbox_dead` NO es un pestillo histórico. Un dead
    de hace 30 días (anterior a la ventana) y otro creado DESPUÉS del cierre
    (la fuga que pintaba rojo el ciclo de AYER al computar a las 06:05) no
    puntúan el ciclo: value=0 y gate VERDE — la racha de 7 ciclos vuelve a
    ser alcanzable sin cirugía en BD. El histórico queda trazado en details
    (dead_actual), jamás oculto."""
    factory = db
    _insert_dead(factory, uuid.uuid4(), CSTART - timedelta(days=30))
    _insert_dead(factory, uuid.uuid4(), CEND + timedelta(hours=1), dest="bff2")

    _compute(factory)
    row = _metric_row(factory, "outbox_dead")
    assert float(row.value) == 0  # la ventana [start, end) no los ve
    assert row.details["dead_en_ciclo"] == 0
    assert row.details["dead_actual"] == 2  # la traza NO desaparece
    assert row.details["dead_sin_fecha"] == 0
    g = _gates(factory)["outbox_dead"]
    assert g["kind"] == "gate" and g["ok"] is True  # verde: no bloquea la racha


def test_recompute_after_purge_preserves_sealed_p99(db):
    """purge_staging poda details.samples de ciclos fuera de retención; un
    recompute posterior del ciclo (con force — el ciclo quedó sellado,
    P1-4) NO machaca el p99 SELLADO con el centinela sin-datos: el 250.0
    histórico sobrevive (ni siquiera hay upsert) y su gate sigue en verde."""
    factory = db
    _exec(
        factory,
        "INSERT INTO shadow_cycle_metrics (cycle_id, metric, scope, value, "
        "details) VALUES (:c, 'outbox_lag_p99', 'global', -1, "
        "CAST(:j AS jsonb))",
        {"c": CYCLE, "j": json.dumps(
            {"samples": [{"ts": "t", "oldest_pending_s": 250.0}] * 4}
        )},
    )
    _compute(factory)
    row = _metric_row(factory, "outbox_lag_p99")
    assert float(row.value) == pytest.approx(250.0)  # p99 SELLADO

    # Purga con el ciclo YA fuera de retención (cutoff 2026-07-23 > CYCLE).
    purge_now = datetime(2026, 7, 30, 12, 0, tzinfo=metrics.CYCLE_TZ)

    async def purge():
        async with factory() as s:
            r = await metrics.purge_staging(s, now=purge_now)
            await s.commit()
            return r

    assert _run(purge())["sample_rows_pruned"] == 1
    row = _metric_row(factory, "outbox_lag_p99")
    assert "samples" not in row.details
    assert row.details["samples_pruned"] == 4
    sealed_at = row.finished_at

    # Recompute FORZADO del ciclo purgado: se PRESERVA el valor (no hay
    # upsert — la métrica ni aparece en el resumen de este recomputo).
    result = _compute(factory, force=True)
    assert "outbox_lag_p99" not in result["metrics"]
    row = _metric_row(factory, "outbox_lag_p99")
    assert float(row.value) == pytest.approx(250.0)
    assert row.details["samples_pruned"] == 4
    assert row.finished_at == sealed_at  # ni re-sellado: intacta
    assert row.details["recomputed_at"]  # el force queda TRAZADO (P1-4)
    assert _gates(factory)["outbox_lag_p99"]["ok"] is True  # 250 <= 900 (umbral recalibrado 2026-08-22)


# --------------------------------------------------------- latencia_p95 (§5)


def test_latencia_p95_over_batches_tolerating_unsealed_and_foreign(db):
    factory = db
    t0 = CSTART + timedelta(hours=2)
    batches = [(10, 0), (20, 1), (30, 2), (40, 3)]
    for secs, i in batches:
        _exec(
            factory,
            "INSERT INTO shadow_projection_batches (first_lsn, last_lsn, "
            "min_received_at, started_at, finished_at, changes) "
            "VALUES (:f, :f, :recv, :st, :fin, 1)",
            {
                "f": 100 + i,
                "recv": t0 + timedelta(minutes=i * 10),
                "st": t0 + timedelta(minutes=i * 10, seconds=secs - 1),
                "fin": t0 + timedelta(minutes=i * 10, seconds=secs),
            },
        )
    # Lote SIN sellar (finished_at NULL) y lote FUERA de la ventana: fuera.
    _exec(
        factory,
        "INSERT INTO shadow_projection_batches (first_lsn, last_lsn, "
        "min_received_at, started_at, finished_at, changes) VALUES "
        "(200, 200, :r1, :r1, NULL, 1), (201, 201, :r2, :r2, :f2, 1)",
        {
            "r1": t0, "r2": CEND + timedelta(hours=1),
            "f2": CEND + timedelta(hours=1, seconds=999),
        },
    )
    _compute(factory)
    row = _metric_row(factory, "latencia_p95")
    # A MANO: p95 de [10,20,30,40] = 30 + 0.85·10 = 38.5 (percentile_cont).
    assert float(row.value) == pytest.approx(38.5)
    assert row.details["lotes"] == 4
    assert _gates(factory)["latencia_p95"]["ok"] is True  # 38.5 <= 3600 (umbral recalibrado 2026-08-22)


# ---------------------------------------------------------------- coste (§5)


def test_coste_proxy_sums_declared_sources(db):
    factory = db
    mp = _seed_model_policy(factory)
    mid, _polid = mp
    t0 = CSTART + timedelta(hours=3)
    zero_vec = "[" + ",".join(["0"] * 384) + "]"
    for i in range(2):  # 2 embeddings de ofertas en la ventana
        _exec(
            factory,
            "INSERT INTO offer_embeddings (text_hash, model_id, vector, created_at) "
            "VALUES (:t, :m, CAST(:v AS vector), :ca)",
            {"t": f"th-{i}", "m": mid, "v": zero_vec, "ca": t0},
        )
    pid = _mk_profile(factory, str(uuid.uuid4()))
    src = _mk_source(factory, "legacy:costefx")
    for i in range(3):  # 3 evaluaciones nuevas en la ventana
        vid, _, _ = _mk_slot(factory, src, f"co-{i}")
        _mk_eval(factory, mp, pid, vid, 50 + i, created_at=t0)
    # Lotes del proyector: 5 s + 2.5 s de worker (proxy).
    for i, secs in enumerate((5, 2.5)):
        _exec(
            factory,
            "INSERT INTO shadow_projection_batches (first_lsn, last_lsn, "
            "min_received_at, started_at, finished_at, changes) "
            "VALUES (:f, :f, :st, :st, :fin, 1)",
            {"f": 300 + i, "st": t0, "fin": t0 + timedelta(seconds=secs)},
        )
    _compute(factory)
    row = _metric_row(factory, "coste")
    # A MANO: 2 + 3 + 7.5 = 12.5.
    assert float(row.value) == pytest.approx(12.5)
    assert row.details["embeddings_ofertas"] == 2
    assert row.details["evaluaciones_nuevas"] == 3
    assert row.details["worker_s_lotes_proyector"] == pytest.approx(7.5)
    assert "profile_embeddings" in row.details["fuentes"]  # límite declarado
    g = _gates(factory)["coste"]
    assert g["kind"] == "alerta" and g["ok"] is True and g["umbral"] is None


# --------------------------------------------------------- reenlace_pct (§5)


def test_reenlace_pct_attaches_recycles_over_touched(db):
    factory = db
    src = _mk_source(factory, "legacy:reenfx")
    other = _mk_source(factory, f"plain{uuid.uuid4().hex[:6]}")
    t0 = CSTART + timedelta(hours=4)
    before = CSTART - timedelta(days=2)
    # 4 encarnaciones legacy TOCADAS en la ventana:
    _v, l1, _i = _mk_slot(factory, src, "re-1", first_seen=t0, last_seen=t0)
    _mk_slot(  # recycle: seq=2 abierta en la ventana
        factory, src, "re-2", seq=2, first_seen=t0, last_seen=t0
    )
    _mk_slot(  # tocada solo por last_seen (abierta antes)
        factory, src, "re-3", first_seen=before, last_seen=t0
    )
    _mk_slot(  # tocada por cierre en la ventana
        factory, src, "re-4", first_seen=before, last_seen=before,
        active=False, ended=t0,
    )
    # NO cuentan: fuera de ventana y fuente ajena.
    _mk_slot(factory, src, "re-5", first_seen=before, last_seen=before)
    _mk_slot(factory, other, "re-6", first_seen=t0, last_seen=t0)
    # 1 attach en la ventana (method url_normalized); alias y fuera: no.
    vac_re1 = _scalar(
        factory,
        "SELECT vacancy_id FROM source_listing_incarnations WHERE "
        "source_listing_id = :l", l=l1,
    )
    for method, ts in (
        ("url_normalized", t0),                       # attach: cuenta
        ("url_alias", t0),                            # alias: NO re-enlaza
        ("url_normalized", before),                   # fuera de ventana
    ):
        _exec(
            factory,
            "INSERT INTO link_evidence (id, source_listing_id, vacancy_id, "
            "method, created_at) VALUES (:i, :l, :v, :m, :ca)",
            {"i": uuid.uuid4(), "l": l1, "v": vac_re1, "m": method, "ca": ts},
        )
    _compute(factory)
    row = _metric_row(factory, "reenlace_pct")
    # A MANO: (1 attach + 1 recycle) / 4 tocadas = 0.5 (ratio 0..1).
    assert float(row.value) == pytest.approx(0.5)
    assert row.details == {
        "attaches": 1, "recycles": 1, "encarnaciones_tocadas": 4,
    }
    g = _gates(factory)["reenlace_pct"]
    assert g["kind"] == "alerta" and g["ok"] is False  # 0.5 > 0.05


def test_reenlace_pct_multiple_evidencias_no_supera_1(db):
    """Regresión G1 H-14c: varias evidencias de attach sobre el MISMO listing
    contaban N veces contra un denominador por ENCARNACIÓN — el «pct» podía
    superar 1.0. El numerador cuenta ahora listings DISTINTOS: 2 evidencias
    sobre 1 encarnación tocada = 1/1 = 1.0, jamás 2.0."""
    factory = db
    src = _mk_source(factory, "legacy:reenh14c")
    t0 = CSTART + timedelta(hours=4)
    _v, l1, _i = _mk_slot(factory, src, "rh-1", first_seen=t0, last_seen=t0)
    vac = _scalar(
        factory,
        "SELECT vacancy_id FROM source_listing_incarnations "
        "WHERE source_listing_id = :l", l=l1,
    )
    for _ in range(2):  # DOS evidencias del mismo attach (re-cosecha)
        _exec(
            factory,
            "INSERT INTO link_evidence (id, source_listing_id, vacancy_id, "
            "method, created_at) VALUES (:i, :l, :v, 'url_normalized', :ca)",
            {"i": uuid.uuid4(), "l": l1, "v": vac, "ca": t0},
        )
    _compute(factory)
    row = _metric_row(factory, "reenlace_pct")
    assert float(row.value) == pytest.approx(1.0)  # antes: 2.0
    assert row.details["attaches"] == 1  # listings distintos, no eventos


def test_perdida_verde_con_alta_reciente_y_cdc_en_vuelo(db):
    """Regresión G1 H-7: un INSERT legacy con su CDC en vuelo al cómputo
    (job nuevo, staging <1h sin aplicar) daba perdida=1 → falso ROJO con el
    pipeline SANO, y la racha a cero. Ahora el minuendo aplica la MISMA
    gracia de 1h que el backlog (first_seen_at) y los huecos se cuentan por
    anti-join: el transitorio no puntúa; el hueco REAL (>1h sin slot) sigue
    en rojo (test_perdida_zero_on_healthy_mirror_and_gap_when_injected)."""
    factory = db
    p = uuid.uuid4().hex[:6]
    # Job RECIÉN visto (dentro de la gracia respecto al cómputo en AFTER)…
    _legacy_job(factory, f"{p}-fresh", first_seen=AFTER - timedelta(minutes=10))
    # …con su fila CDC capturada y aún sin aplicar (received_at reciente).
    _exec(
        factory,
        "INSERT INTO shadow_change_log (lsn, seq_in_tx, src_table, op, pk, "
        "payload, received_at) VALUES (9101, 0, 'jobs', 'I', :p, "
        "CAST('{}' AS jsonb), :r)",
        {"p": f"{p}-fresh", "r": AFTER - timedelta(minutes=10)},
    )
    _compute(factory)
    row = _metric_row(factory, "perdida")
    assert float(row.value) == 0  # antes: 1 (falso rojo transitorio)
    assert row.details["huecos_sin_slot"] == 0
    assert row.details["legacy_activos_ingeribles"] == 0  # gracia de alta
    assert _gates(factory)["perdida"]["ok"] is True


def test_perdida_verde_con_reactivacion_y_cdc_en_vuelo(db):
    """Regresión G2-P2-2: la gracia de G1 H-7 se apoyaba en `first_seen_at`,
    así que cubría las ALTAS pero NO las REACTIVACIONES (is_active false→true),
    que el upsert de cada cosecha legacy produce de forma RUTINARIA. Un job
    ANTIGUO reactivado con su CDC en vuelo (<1h, aún sin proyectar) y su slot
    legítimamente cerrado daba hueco=+1 ⇒ perdida=1 ⇒ falso ROJO y racha de 7
    ciclos a cero con el pipeline SANO. La gracia se aplica ahora a la
    TRANSICIÓN (cambio pendiente reciente), no solo al alta."""
    factory = db
    src = _mk_source(factory, "legacy:reactfx")
    p = uuid.uuid4().hex[:6]
    react = f"{p}-react"
    # Job ANTIGUO (sin gracia de alta) recién REACTIVADO: su slot se cerró
    # legítimamente cuando se desactivó…
    _legacy_job(factory, react)
    _mk_slot(factory, src, react, active=False)
    # …y el UPDATE de reactivación lleva 5 min en staging sin aplicar.
    _exec(
        factory,
        "INSERT INTO shadow_change_log (lsn, seq_in_tx, src_table, op, pk, "
        "payload, received_at) VALUES (9201, 0, 'jobs', 'U', :p, "
        "CAST('{}' AS jsonb), :r)",
        {"p": react, "r": AFTER - timedelta(minutes=5)},
    )

    _compute(factory)
    row = _metric_row(factory, "perdida")
    assert float(row.value) == 0  # antes: 1 (falso rojo por reactivación)
    assert row.details["huecos_sin_slot"] == 0
    assert row.details["huecos_con_cambio_en_vuelo"] == 1
    assert _gates(factory)["perdida"]["ok"] is True

    # El hueco REAL sigue en rojo: job vivo sin slot y SIN cambio en vuelo.
    _legacy_job(factory, f"{p}-hueco")
    _compute(factory, force=True)
    row = _metric_row(factory, "perdida")
    assert float(row.value) == 1
    assert row.details["huecos_sin_slot"] == 1
    assert _gates(factory)["perdida"]["ok"] is False


def test_perdida_roja_con_cambio_pendiente_que_no_explica_el_hueco(db):
    """Regresión G3-P2-1: la gracia de TRANSICIÓN (G2-P2-2) se concedía por la
    MERA existencia de un cambio pendiente reciente con ese pk. Un job vivo
    cuyo slot NUNCA se creó —su cambio se APLICÓ hace días y el proyector no
    creó slot: la pérdida REAL y PERMANENTE que esta métrica existe para
    cazar— desaparecía del conteo en cuanto llegaba el UPDATE RUTINARIO de la
    re-cosecha: perdida=0, gate VERDE y ciclo SELLADO que cuenta para la racha
    de 7 (falso VERDE, la dirección peligrosa). Ahora la gracia exige que el
    cambio EXPLIQUE el hueco (slot cerrado a la espera de reapertura), y el
    transitorio legítimo de G2-P2-2 sigue en verde."""
    factory = db
    src = _mk_source(factory, "legacy:g3maskfx")
    p = uuid.uuid4().hex[:6]
    perdido, react = f"{p}-perdido", f"{p}-react"

    # (1) PÉRDIDA REAL: job vivo y antiguo, SIN slot jamás, con su cambio YA
    # APLICADO hace 3 días…
    _legacy_job(factory, perdido)
    _exec(
        factory,
        "INSERT INTO shadow_change_log (lsn, seq_in_tx, src_table, op, pk, "
        "payload, received_at, applied_at) VALUES (9401, 0, 'jobs', 'I', :p, "
        "CAST('{}' AS jsonb), :r, :a)",
        {"p": perdido, "r": CSTART - timedelta(days=3),
         "a": CSTART - timedelta(days=3)},
    )
    # …y el UPDATE RUTINARIO de la re-cosecha recién capturado (la gracia
    # indebida). Tras el cierre del ciclo: no bloquea el sellado.
    _exec(
        factory,
        "INSERT INTO shadow_change_log (lsn, seq_in_tx, src_table, op, pk, "
        "payload, received_at) VALUES (9402, 0, 'jobs', 'U', :p, "
        "CAST('{}' AS jsonb), :r)",
        {"p": perdido, "r": AFTER - timedelta(minutes=5)},
    )

    # (2) NO-REGRESIÓN de G2-P2-2: reactivación legítima — slot CERRADO y su
    # CDC en vuelo. Debe seguir GRACIADA (verde).
    _legacy_job(factory, react)
    _mk_slot(factory, src, react, active=False)
    _exec(
        factory,
        "INSERT INTO shadow_change_log (lsn, seq_in_tx, src_table, op, pk, "
        "payload, received_at) VALUES (9403, 0, 'jobs', 'U', :p, "
        "CAST('{}' AS jsonb), :r)",
        {"p": react, "r": AFTER - timedelta(minutes=5)},
    )

    _compute(factory)  # PRIMER cómputo, SIN force: el ciclo queda SELLADO
    row = _metric_row(factory, "perdida")
    assert "recomputed_at" not in row.details  # cuenta para la racha
    assert float(row.value) == 1  # antes del fix: 0 (pérdida enmascarada)
    assert row.details["huecos_sin_slot"] == 1
    assert row.details["huecos_muestra"] == [perdido]
    assert row.details["huecos_con_cambio_en_vuelo"] == 1  # solo la reactivación
    assert row.details["huecos_graciados_muestra"] == [react]
    assert _gates(factory)["perdida"]["ok"] is False  # antes del fix: True

    # El enmascaramiento es AUDITABLE en el informe legible (antes ni se
    # imprimía el contador de graciados).
    async def report():
        async with factory() as s:
            return await metrics.render_report(s, CYCLE)

    assert "GRACIADOS" in _run(report())


def test_perdida_roja_cuando_la_reapertura_ya_se_aplico_sin_crear_slot(db):
    """G3-P2-1, 2ª forma: el slot EXISTE cerrado, pero el proyector ya APLICÓ
    después del cierre un cambio que declaraba el job ACTIVO y aun así no
    reabrió — tuvo su oportunidad. Un UPDATE rutinario posterior no lo gracia.
    El mismo montaje con el cambio aplicado declarando is_active=false (un
    UPDATE cualquiera sobre el job ya cerrado) SÍ conserva la gracia."""
    factory = db
    src = _mk_source(factory, "legacy:g3reopenfx")
    p = uuid.uuid4().hex[:6]
    perdido, benigno = f"{p}-noreabre", f"{p}-benigno"
    cerrado = CSTART - timedelta(days=2)
    aplicado = CSTART - timedelta(days=1)

    for h, activo in ((perdido, "true"), (benigno, "false")):
        _legacy_job(factory, h)
        _mk_slot(factory, src, h, active=False, ended=cerrado)
        _exec(
            factory,
            "INSERT INTO shadow_change_log (lsn, seq_in_tx, src_table, op, pk, "
            "payload, received_at, applied_at) VALUES "
            f"(:l, 0, 'jobs', 'U', :p, CAST('{{\"is_active\": {activo}}}' AS jsonb), "
            ":r, :a)",
            {"l": 9410 + (0 if activo == "true" else 1), "p": h,
             "r": cerrado, "a": aplicado},
        )
        _exec(
            factory,
            "INSERT INTO shadow_change_log (lsn, seq_in_tx, src_table, op, pk, "
            "payload, received_at) VALUES (:l, 0, 'jobs', 'U', :p, "
            "CAST('{}' AS jsonb), :r)",
            {"l": 9420 + (0 if activo == "true" else 1), "p": h,
             "r": AFTER - timedelta(minutes=5)},
        )

    _compute(factory)
    row = _metric_row(factory, "perdida")
    assert float(row.value) == 1  # antes del fix: 0 (ambos graciados)
    assert row.details["huecos_muestra"] == [perdido]
    assert row.details["huecos_graciados_muestra"] == [benigno]


def _cambio(factory, lsn, pk, received, applied=None, payload=None, op="U"):
    """Fila de shadow_change_log con payload y sellado INYECTABLES."""
    _exec(
        factory,
        "INSERT INTO shadow_change_log (lsn, seq_in_tx, src_table, op, pk, "
        "payload, received_at, applied_at) VALUES (:l, 0, 'jobs', :o, :p, "
        "CAST(:j AS jsonb), :r, :a)",
        {"l": lsn, "o": op, "p": pk, "j": json.dumps(payload or {}),
         "r": received, "a": applied},
    )


def test_perdida_gracia_por_estado_aplicado_cubre_las_tres_patologias(db):
    """Regresión G4-P2-1: la gracia se decidía por la FORMA DEL SLOT, así que
    no distinguía «el proyector debía crear slot y no lo creó» (pérdida) de
    «no debía crearlo porque el job estaba INACTIVO» (bootstrap: el backfill
    vuelca `jobs` SIN filtrar is_active y el proyector aplica ese I como
    cierre sin slot). La reactivación posterior —rutinaria en el legacy—
    reabría el falso ROJO de G2-P2-2 y reseteaba la racha de 7 ciclos.

    El criterio es ahora el ESTADO APLICADO (espejo de `projector._is_close`
    sobre el último cambio aplicado) y cubre las TRES patologías en el MISMO
    corpus: transitorio legítimo VERDE, pérdida real ROJA, y reactivación de
    un job que nunca debió tener slot VERDE."""
    factory = db
    src = _mk_source(factory, "legacy:g4estadofx")
    p = uuid.uuid4().hex[:6]
    boot = f"{p}-boot"        # inactivo en el bootstrap, sin slot: GRACIA
    dupl = f"{p}-dupl"        # cerrado por duplicate_of, sin slot: GRACIA (N-5)
    perdido = f"{p}-perdido"  # el proyector debía crear slot y no lo creó: ROJO
    react = f"{p}-react"      # slot cerrado + CDC en vuelo (G2-P2-2): GRACIA
    viejo = CSTART - timedelta(days=3)
    en_vuelo = AFTER - timedelta(minutes=5)

    # (1) G4-P2-1: el backfill emitió su I ya INACTIVO (aplicado como cierre,
    # sin slot) y HOY el legacy lo re-activó con el CDC aún en vuelo.
    _legacy_job(factory, boot)
    _cambio(factory, 9601, boot, viejo, applied=viejo, op="I",
            payload={"is_active": False})
    _cambio(factory, 9602, boot, en_vuelo, payload={"is_active": True})

    # (2) G4-N-5: cierre por DUPLICADO — `is_active` sigue en true y aun así
    # `_is_close` cierra: la gracia debe espejar el predicado COMPLETO.
    _legacy_job(factory, dupl)
    _cambio(factory, 9603, dupl, viejo, applied=viejo,
            payload={"is_active": True, "duplicate_of": "otro"})
    _cambio(factory, 9604, dupl, en_vuelo, payload={"is_active": True})

    # (3) NO-REGRESIÓN de G3-P2-1: el último aplicado ABRE (el proyector tuvo
    # su oportunidad) y no hay slot ⇒ pérdida REAL, la gracia no la tapa.
    _legacy_job(factory, perdido)
    _cambio(factory, 9605, perdido, viejo, applied=viejo, op="I",
            payload={"is_active": True})
    _cambio(factory, 9606, perdido, en_vuelo, payload={"is_active": True})

    # (4) NO-REGRESIÓN de G2-P2-2: slot CERRADO y reapertura en vuelo.
    _legacy_job(factory, react)
    _mk_slot(factory, src, react, active=False)
    _cambio(factory, 9607, react, en_vuelo, payload={"is_active": True})

    _compute(factory)  # PRIMER cómputo, SIN force: el ciclo queda SELLADO
    row = _metric_row(factory, "perdida")
    assert "recomputed_at" not in row.details  # cuenta para la racha
    assert float(row.value) == 1  # antes del fix: 3 (falso ROJO en boot/dupl)
    assert row.details["huecos_muestra"] == [perdido]
    assert set(row.details["huecos_graciados_muestra"]) == {boot, dupl, react}
    assert _gates(factory)["perdida"]["ok"] is False  # la pérdida real, roja

    # Sin la pérdida real, el mismo corpus queda VERDE y SELLABLE.
    _exec(factory, f"DELETE FROM {LEG}.jobs WHERE hash = :h", {"h": perdido})
    _compute(factory, force=True)
    row = _metric_row(factory, "perdida")
    assert float(row.value) == 0
    assert _gates(factory)["perdida"]["ok"] is True


def test_perdida_roja_cuando_la_purga_se_llevo_la_evidencia_del_hueco(db):
    """Regresión G5-P2-1: el criterio de la gracia (G4) decide leyendo el
    ÚLTIMO CAMBIO APLICADO del pk en `shadow_change_log`, y `purge_staging`
    —en el MISMO módulo— borraba esa evidencia a los 7 días preservando solo
    la última fila de `users`. Sin evidencia, la rama «ningún cambio aplicado»
    concedía GRACIA: una pérdida REAL y PERMANENTE se sellaba en VERDE y
    contaba para la racha de 7 (en el clúster real, 6.979 de 10.805 jobs
    legacy ya no tenían ninguna fila en el log).

    Dos mitades, porque el fix tiene dos:
    (1) la purga PRESERVA ya la última fila aplicada de cada pk de `jobs`
        (mismo trato que `users`), así que la evidencia no se evapora;
    (2) y aunque falte —el histórico purgado por el código anterior—, la
        ausencia NO gracia: sin slot y sin cambio aplicado no hay nada que
        explique el hueco (fail-closed).
    El transitorio legítimo de G2-P2-2 (slot CERRADO + CDC en vuelo) sigue
    graciado por la evidencia DURABLE del slot."""
    factory = db
    src = _mk_source(factory, "legacy:g5purgafx")
    p = uuid.uuid4().hex[:6]
    perdido, react = f"{p}-perdido", f"{p}-react"
    hace_30 = CSTART - timedelta(days=30)
    en_vuelo = AFTER - timedelta(minutes=5)

    # PÉRDIDA REAL: job vivo y antiguo, SIN slot jamás, cuyo último cambio
    # APLICADO fue una APERTURA hace 30 días (el proyector tuvo su turno) +
    # el UPDATE rutinario de la re-cosecha recién capturado.
    _legacy_job(factory, perdido)
    _cambio(factory, 9701, perdido, hace_30, applied=hace_30, op="I",
            payload={"is_active": True})
    _cambio(factory, 9702, perdido, en_vuelo, payload={"is_active": True})
    # NO-REGRESIÓN G2-P2-2: reactivación legítima, slot CERRADO, sin ningún
    # cambio aplicado que consultar.
    _legacy_job(factory, react)
    _mk_slot(factory, src, react, active=False)
    _cambio(factory, 9703, react, en_vuelo, payload={"is_active": True})

    # (1) La purga NOMINAL (la que gate.run_cycle ejecuta en CADA ciclo) ya
    # no se lleva la evidencia: es la última fila aplicada de ese pk.
    async def purga():
        async with factory() as s:
            r = await metrics.purge_staging(s, now=AFTER)
            await s.commit()
            return r

    assert _run(purga())["staging_deleted"] == 0  # antes del fix: 1
    assert {r.lsn for r in _rows(
        factory, "SELECT lsn FROM shadow_change_log WHERE pk = :p", p=perdido
    )} == {9701, 9702}

    _compute(factory)  # PRIMER cómputo, SIN force: el ciclo queda SELLADO
    row = _metric_row(factory, "perdida")
    assert "recomputed_at" not in row.details  # contaría para la racha
    assert float(row.value) == 1  # antes del fix: 0 (falso VERDE sellado)
    assert row.details["huecos_muestra"] == [perdido]
    assert row.details["huecos_graciados_muestra"] == [react]
    assert _gates(factory)["perdida"]["ok"] is False

    # (2) Histórico YA purgado por el código anterior: la evidencia no está y
    # no volverá. La ausencia no gracia — y el transitorio legítimo, que se
    # apoya en el slot cerrado, se mantiene verde.
    _exec(factory, "DELETE FROM shadow_change_log WHERE lsn = 9701")
    _compute(factory, force=True)
    row = _metric_row(factory, "perdida")
    assert float(row.value) == 1  # antes del fix: 0
    assert row.details["huecos_muestra"] == [perdido]
    assert row.details["huecos_graciados_muestra"] == [react]
    assert (row.details["huecos_graciados_razones"][react]
            == "slot legacy sin encarnación activa (sin cambio aplicado)")
    assert _gates(factory)["perdida"]["ok"] is False


def test_g6_una_fila_con_omitted_no_gracia_el_hueco_que_el_proyector_abriria(db):
    """Regresión G6-P3-1: G5-N-1 saltaba, al elegir el «último cambio
    aplicado», las filas cuyo `_omitted` declara `is_active`/`duplicate_of`, y
    con eso ROMPÍA el espejo que este criterio dice ser. El proyector NO las
    salta: `_is_close` lee `fold.cols.get("is_active") is False`, y una columna
    AUSENTE no es False ⇒ para él esa fila es una APERTURA y habría creado
    slot. Al saltarla, el criterio juzgaba con una fila ANTERIOR —aquí, un
    cierre— y GRACIABA una pérdida REAL, encima nombrando una razón falsa
    («último cambio aplicado = is_active=false» sobre un cambio que no es el
    último). El falso ROJO que compraba no puede ocurrir: si TODAS las `U`
    omitieran `is_active`, el proyector las trataría como aperturas, crearía
    slot y no habría hueco que graciar."""
    factory = db
    _mk_source(factory, "legacy:g6ciegafx")
    p = uuid.uuid4().hex[:6]
    ciego, control = f"{p}-ciego", f"{p}-plano"
    viejo = CSTART - timedelta(days=3)
    en_vuelo = AFTER - timedelta(minutes=5)

    for pk, omitido in ((ciego, True), (control, False)):
        _legacy_job(factory, pk)
        # 1) cierre APLICADO…
        _cambio(factory, 9910 if omitido else 9920, pk, viejo, applied=viejo,
                op="U", payload={"is_active": False})
        # 2) …y encima el cambio aplicado MÁS RECIENTE, que para el proyector
        #    es una APERTURA (no trae is_active=false). Debía haber slot.
        payload = {"title": "reactivado"}
        if omitido:
            payload["_omitted"] = ["is_active"]
        _cambio(factory, 9911 if omitido else 9921, pk,
                viejo + timedelta(minutes=1),
                applied=viejo + timedelta(minutes=1), op="U", payload=payload)
        # 3) el UPDATE rutinario de la re-cosecha, aún sin aplicar
        _cambio(factory, 9912 if omitido else 9922, pk, en_vuelo,
                payload={"is_active": True})

    _compute(factory)
    row = _metric_row(factory, "perdida")
    # Las DOS son pérdida: el corpus es idéntico salvo la clave `_omitted`.
    assert float(row.value) == 2  # antes del fix: 1 (la ciega, graciada)
    assert sorted(row.details["huecos_muestra"]) == sorted([ciego, control])
    assert row.details["huecos_graciados_muestra"] == []
    assert _gates(factory)["perdida"]["ok"] is False


def test_g6_la_purga_no_retiene_los_pks_que_el_legacy_ya_borro(db):
    """Regresión G6-P2-3: al preservar la última fila aplicada por pk de
    `jobs` (G5-P2-1), `purge_staging` dejó de acotar la tabla que purga. La
    preservación no tenía cota de edad NI condición de que el pk siguiera
    existiendo: una vez preservada, esa fila lo es para siempre (a ese pk ya
    no le llegan cambios). Y el pk es `public.jobs.hash`, que ROTA, así que el
    suelo crecía con los pks HISTÓRICOS, no con los jobs vivos — lápidas `D`
    incluidas, que `_huecos_en_transicion` no puede consultar JAMÁS porque
    solo pregunta por jobs VIVOS.

    Medido en el clúster (2026-08-26, solo SELECT): 5.222 filas preservadas,
    de las que 1.396 (26,7 %) eran lápidas `D` sin job vivo — el desglose por
    op es exacto (las 1.396 `D` sin job, las 3.826 `U` todas con job) — con
    1.506/1.705/2.011 pks NUEVOS por jornada de cosecha ⇒ ~635 k filas y
    ~0,7 GB al año frente al «~1 fila retenida por job (10 k)» declarado.

    Lo que el gate necesita se conserva ENTERO: la evidencia de un pk vivo
    (que es la mitad funcional de G5-P2-1) no se toca."""
    factory = db
    p = uuid.uuid4().hex[:6]
    vivo, muerto = f"{p}-vivo", f"{p}-muerto"
    viejo = CSTART - timedelta(days=30)
    _legacy_job(factory, vivo)  # el pk MUERTO no existe en el legacy

    for i, pk in enumerate((vivo, muerto)):
        _cambio(factory, 9930 + i * 3, pk, viejo, applied=viejo, op="I",
                payload={"is_active": True})
        _cambio(factory, 9931 + i * 3, pk, viejo, applied=viejo, op="U",
                payload={"is_active": True})
    # …y el legacy BORRA el segundo: lápida D, inconsultable por construcción.
    _cambio(factory, 9935, muerto, viejo, applied=viejo, op="D", payload={})

    async def purga():
        async with factory() as s:
            r = await metrics.purge_staging(s, now=AFTER, legacy_schema=LEG)
            await s.commit()
            return r

    r1 = _run(purga())
    quedan = {
        (row.pk, row.op) for row in _rows(
            factory, "SELECT pk, op FROM shadow_change_log WHERE pk = ANY(:p)",
            p=[vivo, muerto],
        )
    }
    # Del pk VIVO se conserva su última fila aplicada (la evidencia del gate);
    # del pk que el legacy ya borró, NADA — ni la lápida.
    assert quedan == {(vivo, "U")}
    assert r1["staging_deleted"] == 4  # antes del fix: 3 (la lápida sobrevivía)

    # IDEMPOTENTE: el segundo pase no encuentra nada más que borrar…
    assert _run(purga())["staging_deleted"] == 0
    # …y la evidencia del pk vivo sigue ahí (el criterio no se queda ciego).
    assert _rows(
        factory, "SELECT lsn FROM shadow_change_log WHERE pk = :p", p=vivo
    )[0].lsn == 9931


def test_g6_sin_tabla_legacy_alcanzable_la_purga_preserva_y_avisa(db, caplog):
    """La cota de G6-P2-3 necesita leer `{legacy}.jobs`. Si no es alcanzable
    no hay forma de saber qué pks siguen vivos, y borrar evidencia es mucho
    peor que retenerla de más: se preserva SIN acotar y se AVISA (nunca en
    silencio)."""
    factory = db
    pk = f"{uuid.uuid4().hex[:6]}-huerfano"
    viejo = CSTART - timedelta(days=30)
    _cambio(factory, 9940, pk, viejo, applied=viejo, op="I",
            payload={"is_active": True})
    _cambio(factory, 9941, pk, viejo, applied=viejo, op="U",
            payload={"is_active": True})

    async def purga():
        async with factory() as s:
            r = await metrics.purge_staging(
                s, now=AFTER, legacy_schema="esquema_que_no_existe"
            )
            await s.commit()
            return r

    with caplog.at_level(logging.WARNING, logger="jobhunt_core.shadow.metrics"):
        assert _run(purga())["staging_deleted"] == 1
    assert any("SIN la cota de pk vivo" in r.getMessage() for r in caplog.records)
    assert _rows(
        factory, "SELECT lsn FROM shadow_change_log WHERE pk = :p", p=pk
    )[0].lsn == 9941


def test_informe_de_la_gracia_no_afirma_un_slot_que_nunca_existio(db):
    """Regresión G5-P3-1: la ÚNICA línea legible que explica un
    enmascaramiento decía «slot cerrado» — justo para la población que
    G4-P2-1 añadió, que JAMÁS tuvo slot. El informe es lo que lee un operador
    para decidir un go/no-go: tiene que decir la verdad y desglosar la razón
    por pk."""
    factory = db
    _mk_source(factory, "legacy:g5lineafx")
    p = uuid.uuid4().hex[:6]
    boot = f"{p}-boot"
    viejo = CSTART - timedelta(days=3)
    _legacy_job(factory, boot)
    # backfill: I ya INACTIVO ⇒ el proyector lo aplicó como CIERRE sin slot
    _cambio(factory, 9801, boot, viejo, applied=viejo, op="I",
            payload={"is_active": False})
    _cambio(factory, 9802, boot, AFTER - timedelta(minutes=5),
            payload={"is_active": True})
    _compute(factory)

    row = _metric_row(factory, "perdida")
    assert float(row.value) == 0  # la gracia es CORRECTA
    assert row.details["huecos_graciados_muestra"] == [boot]

    async def go():
        async with factory() as s:
            slots = await s.scalar(
                sa.text("SELECT count(*) FROM source_listings "
                        "WHERE external_id = :e"), {"e": boot},
            )
            return slots, await metrics.render_report(s, CYCLE)

    slots, informe = _run(go())
    linea = next(x for x in informe.splitlines() if "GRACIADOS" in x)
    assert slots == 0                     # jamás hubo slot, ni cerrado
    assert "slot cerrado" not in linea    # antes del fix: lo afirmaba
    assert "último cambio aplicado = is_active=false" in linea
    assert boot in linea


def test_informe_conserva_la_linea_del_colapso_en_ciclos_sellados_pre_fix(db):
    """Regresión G5-P3-2: la separación de poblaciones (refs que COLAPSAN por
    attach vs refs SIN vacante) sustituyó la resta `refs_juzgados −
    vacantes_juzgadas` por dos claves nuevas de `details`. Pero `render_report`
    lee los `details` PERSISTIDOS: en un ciclo SELLADO antes del cambio —que
    sin `force` no se recomputa— ambas claves faltan, los `.get(..., 0)` valían
    0 y la línea DESAPARECÍA del informe. El commit que existe para preservar
    el único rastro legible del operador lo borraba de todo el histórico."""
    factory = db

    def _fila_ndcg(scope, det):
        _exec(
            factory,
            "INSERT INTO shadow_cycle_metrics (cycle_id, metric, scope, value, "
            "details, started_at, finished_at) VALUES (:c, :m, :s, 0.5, "
            "CAST(:d AS jsonb), now(), now())",
            {"c": CYCLE, "m": metrics.M_NDCG, "s": scope, "d": json.dumps(det)},
        )

    base = {"set_id": str(uuid.uuid4()), "espacio_idcg": "vacante",
            "idcg_ref": 11.416508, "dcg": 3.5, "idcg": 7.0,
            "refs_juzgados": 3, "vacantes_juzgadas": 1}
    # Forma EXACTA de `details` anterior a la separación (sin las dos claves).
    _fila_ndcg("profile:viejo", dict(base, set_name="holdout-viejo"))
    # Y un ciclo POSTERIOR al cambio, que sí las trae.
    _fila_ndcg("profile:nuevo", dict(base, set_name="holdout-nuevo",
                                     refs_colapsados_por_attach=2,
                                     refs_sin_vacante=0))

    async def go():
        async with factory() as s:
            return await metrics.render_report(s, CYCLE)

    informe = _run(go())
    viejo = next(x for x in informe.splitlines()
                 if "ndcg@10 profile:viejo:" in x)
    nuevo = next(x for x in informe.splitlines()
                 if "ndcg@10 profile:nuevo:" in x)
    # El histórico recupera el AGREGADO (3 − 1) y dice que el reparto no está
    # disponible: jamás afirma «attach» sobre lo que puede ser falta de corpus.
    assert "2 ref(s) juzgados FUERA del ideal" in viejo
    assert "NO disponible" in viejo
    assert "COLAPSAN" not in viejo
    # El ciclo nuevo conserva el desglose exacto.
    assert "2 ref(s) juzgados COLAPSAN por attach" in nuevo
    assert "0 NO tienen vacante" in nuevo


def test_umbral_del_ciclo_queda_persistido_y_no_se_recolorea(db):
    """Regresión G1 H-8: evaluate_gates re-evaluaba ciclos SELLADOS con las
    constantes VIGENTES — un cambio de umbral recoloreaba la historia sin
    rastro. compute_cycle persiste ahora los umbrales del ciclo
    (M_UMBRALES) y evaluate_gates los usa; los ciclos históricos SIN fila
    conservan el fallback documentado (constantes vigentes)."""
    import unittest.mock as um

    factory = db
    _compute(factory)  # persiste M_UMBRALES con las constantes vigentes
    thr_row = _metric_row(factory, metrics.M_UMBRALES)
    assert thr_row is not None
    assert thr_row.details["dedup_recall_min"] == metrics.DEDUP_RECALL_MIN

    # dedup_recall sellado en 0.5 — con el umbral persistido (0.40) es verde.
    _exec(
        factory,
        "UPDATE shadow_cycle_metrics SET value = 0.5, details = '{}'::jsonb "
        "WHERE cycle_id = :c AND metric = 'dedup_recall' AND scope = 'global'",
        {"c": CYCLE},
    )
    with um.patch.object(metrics, "DEDUP_RECALL_MIN", 0.9):
        g = _gates(factory)["dedup_recall"]
        # ANTES: la constante vigente (0.9) recoloreaba el ciclo a rojo.
        assert g["umbral"] == thr_row.details["dedup_recall_min"]
        assert g["ok"] is True
    # La fila de umbrales NO aparece como gate ni en el informe.
    assert metrics.M_UMBRALES not in _gates(factory)

    async def report():
        async with factory() as s:
            return await metrics.render_report(s, CYCLE)

    assert metrics.M_UMBRALES not in _run(report())

    # Ciclo histórico SIN fila de umbrales: fallback a la constante vigente.
    cyc_old = CYCLE - timedelta(days=7)
    _seed_metric(factory, cyc_old, "dedup_recall", "global", 0.5, {})
    with um.patch.object(metrics, "DEDUP_RECALL_MIN", 0.9):
        assert _gates(factory, cycle=cyc_old)["dedup_recall"]["ok"] is False
    with um.patch.object(metrics, "DEDUP_RECALL_MIN", 0.4):
        assert _gates(factory, cycle=cyc_old)["dedup_recall"]["ok"] is True


# ------------------------------------------------------ purga del staging (§7)


def test_purge_deletes_old_applied_preserving_last_users_and_unapplied(db):
    """G5-P2-1: la purga preserva la ÚLTIMA fila aplicada de CADA pk de
    `users` (inactive_user_refs) Y de `jobs` (_huecos_en_transicion) — la
    versión anterior solo conocía el primer consumidor y borraba la evidencia
    del segundo, que es la que decide si un hueco del espejo está EXPLICADO."""
    factory = db
    now = datetime(2026, 7, 25, 12, 0, tzinfo=metrics.CYCLE_TZ)
    # cutoff = cierre del ciclo actual (2026-07-26 06:00) − 7 días.
    old = datetime(2026, 7, 15, 12, 0, tzinfo=metrics.CYCLE_TZ)
    recent = datetime(2026, 7, 24, 12, 0, tzinfo=metrics.CYCLE_TZ)
    u1, u2 = str(uuid.uuid4()), str(uuid.uuid4())
    rows = [
        # (lsn, tabla, op, pk, payload, received, applied?)
        (1, "jobs", "I", "j-old", {}, old, True),          # borra (superada)
        (2, "users", "I", u1, {"id": u1, "is_active": True}, old, True),   # borra
        (3, "users", "U", u1, {"id": u1, "is_active": False}, old, True),  # PRESERVA (última users de u1)
        (4, "users", "I", u2, {"id": u2, "is_active": False}, old, True),  # PRESERVA (última users de u2)
        (5, "users", "D", "u-gone", {}, old, True),        # borra (el ERASE ya corrió)
        (6, "jobs", "U", "j-pend", {}, old, False),        # PRESERVA (sin aplicar)
        (7, "jobs", "I", "j-recent", {}, recent, True),    # PRESERVA (reciente)
        (8, "jobs", "U", "j-old", {}, old, True),          # PRESERVA (última de j-old)
        # G5-P2-1: para `jobs` la última puede ser una D — un borrado es un
        # CIERRE y es EVIDENCIA para el criterio de la gracia (a diferencia de
        # la D de `users`, que no aporta nada a inactive_user_refs).
        (9, "jobs", "D", "j-gone", {}, old, True),         # PRESERVA (última de j-gone)
    ]
    for lsn, t, op, pk, payload, recv, applied in rows:
        _exec(
            factory,
            "INSERT INTO shadow_change_log (lsn, seq_in_tx, src_table, op, pk, "
            "payload, received_at, applied_at) VALUES (:l, 0, :t, :o, :p, "
            "CAST(:j AS jsonb), CAST(:r AS timestamptz), "
            "CASE WHEN :a THEN CAST(:r AS timestamptz) END)",
            {"l": lsn, "t": t, "o": op, "p": pk, "j": json.dumps(payload),
             "r": recv, "a": applied},
        )
    # Samples de ciclos FUERA de retención se podan SOLO si el p99 quedó
    # SELLADO (guard value <> centinela — P3); los del ciclo actual, nunca.
    for cid, n in ((date(2026, 7, 10), 3), (date(2026, 7, 25), 2)):
        _exec(
            factory,
            "INSERT INTO shadow_cycle_metrics (cycle_id, metric, scope, value, "
            "details) VALUES (:c, 'outbox_lag_p99', 'global', -1, "
            "CAST(:j AS jsonb))",
            {"c": cid, "j": json.dumps({"samples": [{"oldest_pending_s": i} for i in range(n)]})},
        )
    # Sella el p99 del ciclo viejo: sin sellar, el guard lo dejaría intacto.
    _compute(factory, cycle_id=date(2026, 7, 10))

    async def exclusion():
        async with factory() as s:
            return await projector.inactive_user_refs(s, [u1, u2])

    assert _run(exclusion()) == {u1, u2}  # ambos inactivos ANTES de purgar

    async def purge():
        async with factory() as s:
            r = await metrics.purge_staging(s, now=now)
            await s.commit()
            return r

    result = _run(purge())
    assert result["staging_deleted"] == 3  # lsn 1, 2 y 5
    assert result["sample_rows_pruned"] == 1
    kept = {r.lsn for r in _rows(factory, "SELECT lsn FROM shadow_change_log")}
    assert kept == {3, 4, 6, 7, 8, 9}
    # La NOTA del proyector se cumple: la exclusión de inactivos SIGUE viva.
    assert _run(exclusion()) == {u1, u2}
    # Poda de samples: el ciclo viejo queda con el rastro, el actual intacto.
    old_row = _metric_row(factory, "outbox_lag_p99", cycle=date(2026, 7, 10))
    assert "samples" not in old_row.details
    assert old_row.details["samples_pruned"] == 3
    cur_row = _metric_row(factory, "outbox_lag_p99", cycle=date(2026, 7, 25))
    assert len(cur_row.details["samples"]) == 2

    # IDEMPOTENTE: el segundo pase no borra ni poda nada.
    result2 = _run(purge())
    assert result2 == result | {"staging_deleted": 0, "sample_rows_pruned": 0}
    assert {r.lsn for r in _rows(factory, "SELECT lsn FROM shadow_change_log")} == kept


def test_purge_keeps_unsealed_samples_until_compute_seals_them(db):
    """Regresión P3: una fila de outbox_lag_p99 MUESTREADA pero NO sellada
    (value = centinela) con ciclo ya fuera de retención NO se poda — sus
    samples sobreviven a purge_staging para que un compute_cycle tardío
    (>= 7 días de retraso o backfill histórico) selle el p99 correcto desde
    ellos, en vez de dejar el [gate] en no_data PERMANENTE. Una vez sellada,
    la siguiente purga ya puede podarla."""
    factory = db
    # Fila del muestreador SIN computar: value = centinela, solo samples.
    _exec(
        factory,
        "INSERT INTO shadow_cycle_metrics (cycle_id, metric, scope, value, "
        "details) VALUES (:c, 'outbox_lag_p99', 'global', :nodata, "
        "CAST(:j AS jsonb))",
        {"c": CYCLE, "nodata": metrics.NO_DATA_VALUE, "j": json.dumps(
            {"samples": [
                {"ts": "t", "oldest_pending_s": v} for v in (100, 150, 200, 250)
            ]}
        )},
    )
    # Purga con el ciclo YA fuera de retención (cutoff 2026-07-23 > CYCLE)
    # y el compute aún sin correr: el guard la deja INTACTA.
    purge_now = datetime(2026, 7, 30, 12, 0, tzinfo=metrics.CYCLE_TZ)

    async def purge():
        async with factory() as s:
            r = await metrics.purge_staging(s, now=purge_now)
            await s.commit()
            return r

    assert _run(purge())["sample_rows_pruned"] == 0  # no sellada: no se poda
    row = _metric_row(factory, "outbox_lag_p99")
    assert float(row.value) == metrics.NO_DATA_VALUE
    assert len(row.details["samples"]) == 4  # samples INTACTOS

    # compute_cycle posterior (backfill) sella el p99 desde esos samples.
    # A MANO: p99 de [100,150,200,250] = 200 + 0.97·50 = 248.5.
    _compute(factory, cycle_id=CYCLE, now=purge_now)
    row = _metric_row(factory, "outbox_lag_p99")
    assert float(row.value) == pytest.approx(248.5)
    assert row.details["no_data"] is False
    g = _gates(factory)["outbox_lag_p99"]
    assert g["ok"] is True  # 248.5 <= 300: gate en VERDE, no no_data eterno

    # Ya SELLADA: la siguiente purga poda los samples y el p99 sobrevive.
    assert _run(purge())["sample_rows_pruned"] == 1
    row = _metric_row(factory, "outbox_lag_p99")
    assert "samples" not in row.details
    assert row.details["samples_pruned"] == 4
    assert float(row.value) == pytest.approx(248.5)
    assert _gates(factory)["outbox_lag_p99"]["ok"] is True


# ---------------------------------------------- recompute sin perfiles idos


def test_recompute_removes_stale_profile_rows(db):
    """Un recompute (forzado — el ciclo quedó sellado, P1-4) que mide MENOS
    perfiles (p.ej. uno borrado por GDPR) elimina las filas profile:<id>
    obsoletas del cálculo anterior — en la misma transacción del cómputo,
    sin tocar las globales ni otros scopes."""
    factory = db
    pid1 = _mk_profile(factory, str(uuid.uuid4()))
    pid2 = _mk_profile(factory, str(uuid.uuid4()))
    _mk_frozen_set(factory, pid1, {"st-1": 3})
    _mk_frozen_set(factory, pid2, {"st-2": 2})
    assert _compute(factory)["profiles_measured"] == 2

    def profile_scopes():
        return {
            r.scope for r in _rows(
                factory,
                "SELECT DISTINCT scope FROM shadow_cycle_metrics "
                "WHERE cycle_id = :c AND scope LIKE 'profile:%'",
                c=CYCLE,
            )
        }

    assert profile_scopes() == {f"profile:{pid1}", f"profile:{pid2}"}

    # GDPR: el perfil 2 desaparece (labeled_sets cae por ON DELETE CASCADE).
    _exec(factory, "DELETE FROM profiles WHERE id = :p", {"p": pid2})
    assert _compute(factory, force=True)["profiles_measured"] == 1
    assert profile_scopes() == {f"profile:{pid1}"}  # sin filas huérfanas
    # Las globales del ciclo siguen intactas tras el DELETE selectivo.
    assert _metric_row(factory, "perdida") is not None
    assert _metric_row(factory, "ndcg@10", f"profile:{pid1}") is not None


# ------------------------------------------------- gates con umbrales forzados


def _seed_metric(factory, cycle, metric, scope, value, details=None):
    _exec(
        factory,
        "INSERT INTO shadow_cycle_metrics (cycle_id, metric, scope, value, "
        "details) VALUES (:c, :m, :s, :v, CAST(:d AS jsonb))",
        {"c": cycle, "m": metric, "s": scope, "v": value,
         "d": json.dumps(details or {})},
    )


def test_evaluate_gates_ok_and_failed_with_forced_values(db):
    factory = db
    cyc = date(2026, 7, 1)
    p1, p2 = "profile:aaaa", "profile:bbbb"
    seed = [
        # p1: ndcg 0.70 con legacy 0.80 → umbral 0.75 → FALLO del gate.
        ("ndcg@10", p1, 0.70, {}),
        ("ndcg@10_legacy", p1, 0.80, {}),
        ("falsos_negativos", p1, 0.5, {"modo": "estricto_0"}),   # FALLO (>0)
        ("overlap@10", p1, 0.3, {}),                             # informativa
        # p2: ndcg 0.62 con legacy 0.50 → umbral max(0.60, 0.45) = 0.60 → OK.
        ("ndcg@10", p2, 0.62, {}),
        ("ndcg@10_legacy", p2, 0.50, {}),
        ("falsos_negativos", p2, 0.015, {"modo": "ratio_2pct"}),  # OK (<=0.02)
        ("labels_ready", "global", 1, {}),                        # OK (P1-2)
        ("dedup_precision", "global", 0.96, {}),                  # OK
        ("dedup_recall", "global", 0.39, {}),                    # FALLO (<0.40, D2)
        ("perdida", "global", 1, {}),                             # FALLO (==0)
        ("no_ingeribles", "global", 2, {}),                       # ALERTA (>0)
        ("outbox_lag_p99", "global", 299.0, {"samples_count": 9}),  # OK
        ("outbox_dead", "global", 1, {"dead_actual": 1}),           # FALLO (P2-6)
        ("latencia_p95", "global", 4000.0, {"lotes": 4}),           # FALLO (>3600, umbral 2026-08-22)
        ("coste", "global", 1234.0, {}),                            # informativa
        ("reenlace_pct", "global", 0.06, {}),                       # ALERTA
    ]
    for m, sc, v, d in seed:
        _seed_metric(factory, cyc, m, sc, v, d)

    gates = _gates(factory, cycle=cyc)
    for key, g in gates.items():
        assert set(g) >= {"value", "umbral", "kind", "ok"}, key
    assert gates[f"ndcg@10::{p1}"]["ok"] is False
    assert gates[f"ndcg@10::{p1}"]["umbral"] == pytest.approx(0.75)
    assert gates[f"ndcg@10::{p2}"]["ok"] is True
    assert gates[f"ndcg@10::{p2}"]["umbral"] == pytest.approx(0.60)
    assert gates[f"falsos_negativos::{p1}"] ["ok"] is False
    assert gates[f"falsos_negativos::{p2}"]["ok"] is True
    assert gates["labels_ready"] == {
        "value": 1.0, "umbral": 1, "kind": "gate", "ok": True,
    }
    assert gates["dedup_precision"]["ok"] is True
    assert gates["dedup_recall"]["ok"] is False
    assert gates["perdida"]["ok"] is False
    assert gates["no_ingeribles"] == {
        "value": 2.0, "umbral": 0, "kind": "alerta", "ok": False,
    }
    assert gates["outbox_lag_p99"]["ok"] is True
    assert gates["outbox_dead"] == {
        "value": 1.0, "umbral": 0, "kind": "gate", "ok": False,  # P2-6
    }
    assert gates["latencia_p95"]["ok"] is False
    assert gates["coste"] == {
        "value": 1234.0, "umbral": None, "kind": "alerta", "ok": True,
    }
    assert gates["reenlace_pct"]["ok"] is False

    # Ciclo VACÍO: gates sin datos NO demostrables (ok False) y sin perfiles.
    empty = _gates(factory, cycle=date(2026, 6, 1))
    assert empty["ndcg@10"]["ok"] is False
    assert "sin perfiles" in empty["ndcg@10"]["nota"]
    for m in ("labels_ready", "dedup_precision", "dedup_recall", "perdida",
              "outbox_lag_p99", "outbox_dead", "latencia_p95"):
        assert empty[m]["ok"] is False and empty[m]["nota"] == "sin datos"
    for m in ("no_ingeribles", "coste", "reenlace_pct"):
        assert empty[m]["ok"] is True and empty[m]["nota"] == "sin datos"


# ------------------------------------------------------------- informe (DoD)


def test_render_report_is_readable_text(db):
    factory = db
    cyc = date(2026, 7, 1)
    p1 = "profile:aaaa"
    _seed_metric(factory, cyc, "ndcg@10", p1, 0.70, {})
    _seed_metric(factory, cyc, "ndcg@10_legacy", p1, 0.80, {})
    _seed_metric(factory, cyc, "falsos_negativos", p1, 0.0, {"modo": "estricto_0"})
    _seed_metric(factory, cyc, "overlap@10", p1, 0.3, {})
    _seed_metric(factory, cyc, "labels_ready", "global", 1, {})
    _seed_metric(factory, cyc, "dedup_precision", "global", 0.96, {})
    _seed_metric(factory, cyc, "dedup_recall", "global", 0.95, {})
    _seed_metric(
        factory, cyc, "perdida", "global", 0,
        {"legacy_activos_ingeribles": 5, "slots_legacy_activos": 5,
         "staging_sin_aplicar_1h": 0},
    )
    _seed_metric(factory, cyc, "no_ingeribles", "global", 0, {})
    _seed_metric(factory, cyc, "outbox_lag_p99", "global", 12.0, {})
    _seed_metric(factory, cyc, "outbox_dead", "global", 0, {"dead_actual": 0})
    _seed_metric(factory, cyc, "latencia_p95", "global", 38.5, {})
    _seed_metric(
        factory, cyc, "coste", "global", 12.5,
        {"embeddings_ofertas": 2, "evaluaciones_nuevas": 3,
         "worker_s_lotes_proyector": 7.5},
    )
    _seed_metric(
        factory, cyc, "reenlace_pct", "global", 0.01,
        {"attaches": 1, "recycles": 0, "encarnaciones_tocadas": 100},
    )

    async def report():
        async with factory() as s:
            return await metrics.render_report(s, cyc)

    text = _run(report())
    assert "INFORME DE CICLO SOMBRA — 2026-07-01" in text
    assert "[2026-07-01T06:00:00+02:00 .. 2026-07-02T06:00:00+02:00)" in text
    for needle in (
        f"ndcg@10::{p1}", "overlap@10", "labels_ready", "dedup_precision",
        "perdida", "outbox_lag_p99", "outbox_dead", "latencia_p95", "coste",
        "reenlace_pct",
    ):
        assert needle in text
    # ndcg 0.70 < 0.75 falla: el único gate en rojo de este ciclo forzado
    # (gates: ndcg + falsos_negativos del perfil + 7 globales = 9, con
    # labels_ready — P1-2 — y outbox_dead — P2-6 — incluidos).
    assert "CICLO NO APTO: 1/9" in text
    assert "alertas activas: 0" in text
    assert (
        "perdida = 0 legacy vivos >1h sin slot + 0 staging sin aplicar >1h "
        "(vivos ingeribles: 5, slots activos: 5)" in text
    )
    assert "coste = 2 embeddings + 3 evaluaciones + 7.5s de worker" in text
    # Legible: tabla con cabecera y una línea por métrica evaluada.
    assert "métrica" in text and "estado" in text
    assert text.count("\n") > 15


def test_render_report_ciclo_mixto_dice_inelegible_no_apto(db):
    """Regresión ronda 2 de la revisión solo-código (IMPORTANTE 2):
    metrics.render_report se documenta como el veredicto del ciclo para el
    contador de §6, pero solo miraba los gates — con los 9 en verde y el
    freeze a MITAD de la ventana imprimía literalmente «CICLO APTO: 9/9»,
    contradiciendo a gate_status/run_cycle. Ahora consulta el mismo
    congelado persistido: ventana mixta ⇒ CICLO INELEGIBLE, jamás APTO."""
    factory = db
    cyc = date(2026, 7, 1)
    p1 = "profile:aaaa"
    # los 9 gates en verde (misma forma que el seeder del gate)
    _seed_metric(factory, cyc, "ndcg@10", p1, 0.90, {})
    _seed_metric(factory, cyc, "ndcg@10_legacy", p1, 0.70, {})
    _seed_metric(factory, cyc, "falsos_negativos", p1, 0.0, {"modo": "estricto_0"})
    _seed_metric(factory, cyc, "labels_ready", "global", 1, {})
    _seed_metric(factory, cyc, "dedup_precision", "global", 1.0, {})
    _seed_metric(factory, cyc, "dedup_recall", "global", 1.0, {})
    _seed_metric(factory, cyc, "perdida", "global", 0, {})
    _seed_metric(factory, cyc, "outbox_lag_p99", "global", 10.0, {})
    _seed_metric(factory, cyc, "outbox_dead", "global", 0, {"dead_actual": 0})
    _seed_metric(factory, cyc, "latencia_p95", "global", 20.0, {})

    def freeze(ts):
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
            {"src": labels.DEDUP_EVAL_COHORT, "ts": ts},
        )
        _exec(
            factory,
            "ALTER TABLE labeled_dedup_cohorts "
            "ENABLE TRIGGER labeled_dedup_cohorts_frozen_guard",
        )

    async def report():
        async with factory() as s:
            return await metrics.render_report(s, cyc)

    # Sin cohorte congelada: inelegible (fail-closed, jamás APTO).
    text = _run(report())
    assert "CICLO INELEGIBLE" in text and "CICLO APTO" not in text

    # Freeze a MITAD de la ventana: sigue inelegible con los 9 en verde.
    freeze(metrics.cycle_bounds(cyc)[0] + timedelta(hours=6))
    text = _run(report())
    assert "CICLO INELEGIBLE" in text and "gates en verde" in text
    assert "CICLO APTO" not in text

    # Freeze ANTERIOR a la ventana: ahora sí APTO.
    freeze(metrics.cycle_bounds(cyc)[0] - timedelta(days=1))
    text = _run(report())
    assert "CICLO APTO: " in text and "INELEGIBLE" not in text


# ------------------------------------------------ tareas Celery registradas


def test_tasks_registered_routed_to_core_default_and_run(db):
    from jobhunt_core.celery_app import celery_app
    from jobhunt_core.tasks.shadow import (
        purge_staging_task,
        sample_outbox_lag_task,
    )

    for name in (
        "jobhunt.shadow.sample_outbox_lag",
        "jobhunt.shadow.compute_cycle",
        "jobhunt.shadow.purge_staging",
    ):
        assert name in celery_app.tasks
        # Decisión B-04: observabilidad/mantenimiento → core.default (jamás
        # detrás de un lote del proyector en core.harvest).
        assert celery_app.conf.task_routes[name] == {"queue": "core.default"}
    # Cadencia cableada por B-05: el muestreador SÍ va en el beat (5 min);
    # compute_cycle/purge NO — los orquesta jobhunt.shadow.run_cycle.
    beat_tasks = {e["task"] for e in celery_app.conf.beat_schedule.values()}
    assert "jobhunt.shadow.sample_outbox_lag" in beat_tasks
    assert "jobhunt.shadow.compute_cycle" not in beat_tasks
    assert "jobhunt.shadow.purge_staging" not in beat_tasks

    result = purge_staging_task.apply()  # staging vacío: no-op idempotente
    assert result.successful()
    assert result.result["staging_deleted"] == 0
    sampled = sample_outbox_lag_task.apply()  # outbox vacío: lag 0.0
    assert sampled.successful()
    assert sampled.result["oldest_pending_s"] == 0.0
    assert sampled.result["samples"] >= 1


# ------------------------------------------------------- fórmula de referencia


def test_dcg_formula_matches_contract():
    """La fórmula EXACTA de §5: DCG = Σ (2^rel_i − 1)/log2(i+1), i desde 1."""
    assert metrics._dcg([]) == 0.0
    assert metrics._dcg([3]) == pytest.approx(7.0)          # (2^3−1)/log2(2)
    assert metrics._dcg([0, 2]) == pytest.approx(3 / math.log2(3))
    assert metrics._dcg([3, 2, 1, 0]) == pytest.approx(
        7 + 3 / math.log2(3) + 1 / math.log2(4), abs=1e-9
    )


def test_d2_recall_umbral_reratificado(db):
    """Mordida de la re-ratificación D2 (ACTA_DECISIONES_2026-08-26): el
    umbral vinculante de dedup_recall es 0.40 — el techo demostrado del
    examen congelado. recall 0.333 (< 0.40) DEBE seguir siendo rojo: la
    re-ratificación acepta el techo, no apaga el gate. (El caso verde por
    encima de 0.40 lo cubre la matriz 0.667 del test de confusión.)"""
    factory = db
    src = _mk_source(factory, "legacy:d2fx")
    p = uuid.uuid4().hex[:6]

    def ref(n):
        return f"{p}-{n}"

    # TP=1 (misma vacante) + FN=2 (duplicates sin candidato) → recall 1/3.
    va, _, _ = _mk_slot(factory, src, ref("a1"))
    _mk_slot(factory, src, ref("a2"), active=False, vacancy_id=va)
    for n in ("b1", "b2", "c1", "c2"):
        _mk_slot(factory, src, ref(n))
    pairs = [
        (ref("a1"), ref("a2"), "duplicate"),  # TP
        (ref("b1"), ref("b2"), "duplicate"),  # FN
        (ref("c1"), ref("c2"), "duplicate"),  # FN
    ]
    for a, b, v in pairs:
        _exec(
            factory,
            "INSERT INTO labeled_dedup_pairs (job_ref_a, job_ref_b, verdict, source) "
            "VALUES (:a, :b, :v, :src)",
            {"a": a, "b": b, "v": v, "src": labels.DEDUP_EVAL_COHORT},
        )
    _compute(factory)
    rec = _metric_row(factory, "dedup_recall")
    assert float(rec.value) == pytest.approx(1 / 3, abs=1e-6)
    gates = _gates(factory)
    assert gates["dedup_recall"]["ok"] is False  # 0.333 < 0.40: sigue mordiendo
