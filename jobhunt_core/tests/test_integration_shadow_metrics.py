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
from jobhunt_core.shadow import labels, metrics, projector
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
            c.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
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
                    f"duplicate_of varchar(32))"
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
            await c.execute(
                sa.text(
                    "TRUNCATE shadow_change_log, shadow_projection_batches, "
                    "shadow_cycle_metrics, labeled_dedup_pairs"
                )
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


def _legacy_job(factory, h, active=True, dup=None, url=_URL_DEFAULT):
    _exec(
        factory,
        f"INSERT INTO {LEG}.jobs (hash, source, url, is_active, duplicate_of) "
        f"VALUES (:h, 'metfx', :u, :a, :d)",
        {"h": h, "u": f"https://leg/{h}" if url is _URL_DEFAULT else url,
         "a": active, "d": dup},
    )


def _legacy_result(factory, user_id, job_hash, score, feedback=None):
    _exec(
        factory,
        f"INSERT INTO {LEG}.match_results (user_id, job_hash, feedback, score_final) "
        f"VALUES (:u, :j, :f, :s)",
        {"u": user_id, "j": job_hash, "f": feedback, "s": score},
    )


def _compute(factory, cycle_id=None, now=AFTER):
    async def go():
        async with factory() as s:
            r = await metrics.compute_cycle(
                s, cycle_id=cycle_id, legacy_schema=LEG, now=now
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

    # Recomputar es IDEMPOTENTE (upsert por PK): mismos valores, sin filas dup.
    n_before = _scalar(
        factory, "SELECT count(*) FROM shadow_cycle_metrics WHERE cycle_id = :c",
        c=CYCLE,
    )
    _compute(factory)
    assert _scalar(
        factory, "SELECT count(*) FROM shadow_cycle_metrics WHERE cycle_id = :c",
        c=CYCLE,
    ) == n_before
    assert float(_metric_row(factory, "ndcg@10", scope).value) == pytest.approx(
        0.949980, abs=1e-6
    )


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
            "INSERT INTO labeled_dedup_pairs (job_ref_a, job_ref_b, verdict, source) "
            "VALUES (:a, :b, :v, 'manual')",
            {"a": a, "b": b, "v": v},
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
    assert gates["dedup_recall"]["ok"] is False     # 0.667 < 0.90


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
    _legacy_job(factory, f"{p}-longurl", url="https://leg/" + "x" * 1010)
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
    # lo sin aplicar RECIENTE no cuenta (gracia de 1h).
    _legacy_job(factory, f"{p}-hueco")
    _exec(
        factory,
        "INSERT INTO shadow_change_log (lsn, seq_in_tx, src_table, op, pk, "
        "payload, received_at) VALUES "
        "(9001, 0, 'jobs', 'I', 'x-old', CAST('{}' AS jsonb), :old), "
        "(9002, 0, 'jobs', 'I', 'x-new', CAST('{}' AS jsonb), :new)",
        {"old": AFTER - timedelta(hours=2), "new": AFTER - timedelta(minutes=10)},
    )
    _compute(factory)
    row = _metric_row(factory, "perdida")
    # A MANO: 4 vivos − 3 slots + 1 backlog viejo = 2.
    assert float(row.value) == 2
    assert row.details["staging_sin_aplicar_1h"] == 1
    assert _gates(factory)["perdida"]["ok"] is False  # gate estricto == 0


def test_perdida_quarantine_frontier_matches_sink(db):
    """La partición vivos/no_ingeribles usa la MISMA ruta de cuarentena del
    sink: una url de exactamente 1000 con path VACÍO (normalize_url añade
    '/' → 1001) y una con corchete IPv6 desbalanceado (ValueError) van a
    no_ingeribles y JAMÁS al minuendo — perdida=0, sin falso positivo
    permanente del gate estricto."""
    factory = db
    src = _mk_source(factory, "legacy:frontfx")
    p = uuid.uuid4().hex[:6]
    ok = f"{p}-ok"
    _legacy_job(factory, ok)
    _mk_slot(factory, src, ok)
    # Borde INGERIBLE: exactamente 1000 CON path — la normalizada no crece.
    edge = f"{p}-edge"
    edge_url = "https://leg/" + "x" * (1000 - len("https://leg/"))
    assert len(edge_url) == len(metrics.normalize_url(edge_url)) == 1000
    _legacy_job(factory, edge, url=edge_url)
    _mk_slot(factory, src, edge)
    # Cuarentenable 1: exactamente 1000 con path VACÍO → normalizada 1001
    # (el sink la rechaza por len(url_norm) > MAX_URL_LEN; la cruda pasa).
    grow_url = "https://x?" + "a" * 990
    assert len(grow_url) == 1000
    assert len(metrics.normalize_url(grow_url)) == 1001
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
    assert float(ni.value) == 2  # grow (1001 normalizada) + ipv6 (ValueError)
    assert sorted(ni.details["cuarentenados_muestra"]) == sorted(
        [f"{p}-grow", f"{p}-ipv6"]
    )
    gates = _gates(factory)
    assert gates["perdida"]["ok"] is True         # sin falso perdida>0
    assert gates["no_ingeribles"]["ok"] is False  # alerta > 0, APARTE


# ------------------------------------------- outbox_lag_p99 + muestreador (§5)


def test_sampler_appends_and_p99_exact(db):
    factory = db
    # Delivery pending con 100 s de edad: oldest_pending_s ≈ 100.
    eid = uuid.uuid4()
    _exec(
        factory,
        "INSERT INTO integration_outbox (event_id, aggregate, aggregate_id, "
        "version, type, payload) VALUES (:e, 'match_evaluation', 'k', 1, "
        "'match.evaluated', CAST('{}' AS jsonb))",
        {"e": eid},
    )
    _exec(
        factory,
        "INSERT INTO integration_outbox_deliveries (event_id, destination, "
        "next_attempt_at) VALUES (:e, 'bff', clock_timestamp() - "
        "make_interval(secs => 100))",
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
    r2 = _run(sample(t0 + timedelta(minutes=5)))
    assert r2["samples"] == 2  # appendea, no pisa

    row = _metric_row(factory, "outbox_lag_p99")
    assert float(row.value) == metrics.NO_DATA_VALUE  # aún sin computar
    assert len(row.details["samples"]) == 2
    assert row.details["samples"][0]["ts"].startswith("2026-07-20T")

    # p99 EXACTO (percentile_cont): samples deterministas [100, 200, 300, 400]
    # → 0.99·3 = 2.97 → 300 + 0.97·100 = 397.0.
    _exec(
        factory,
        "UPDATE shadow_cycle_metrics SET details = jsonb_set(details, "
        "'{samples}', CAST(:j AS jsonb)) WHERE cycle_id = :c AND metric = :m",
        {
            "j": json.dumps(
                [{"ts": "t", "oldest_pending_s": v} for v in (100, 200, 300, 400)]
            ),
            "c": CYCLE, "m": "outbox_lag_p99",
        },
    )
    _compute(factory)
    row = _metric_row(factory, "outbox_lag_p99")
    assert float(row.value) == pytest.approx(397.0)
    assert row.details["samples_count"] == 4
    assert len(row.details["samples"]) == 4  # merge: los samples SOBREVIVEN
    g = _gates(factory)["outbox_lag_p99"]
    assert g["ok"] is False  # 397 > 300


def test_outbox_lag_without_samples_is_no_data_and_gate_fails(db):
    factory = db
    _compute(factory)
    row = _metric_row(factory, "outbox_lag_p99")
    assert float(row.value) == metrics.NO_DATA_VALUE  # el "NULL" del contrato
    assert row.details["no_data"] is True
    g = _gates(factory)["outbox_lag_p99"]
    assert g["value"] is None and g["ok"] is False and g["nota"] == "sin datos"


def test_recompute_after_purge_preserves_sealed_p99(db):
    """purge_staging poda details.samples de ciclos fuera de retención; un
    recompute posterior del ciclo NO machaca el p99 SELLADO con el
    centinela sin-datos: el 250.0 histórico sobrevive (ni siquiera hay
    upsert) y su gate sigue en verde."""
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

    # Recompute del ciclo purgado: se PRESERVA el valor (no hay upsert —
    # la métrica ni aparece en el resumen de este recomputo).
    result = _compute(factory)
    assert "outbox_lag_p99" not in result["metrics"]
    row = _metric_row(factory, "outbox_lag_p99")
    assert float(row.value) == pytest.approx(250.0)
    assert row.details["samples_pruned"] == 4
    assert row.finished_at == sealed_at  # ni re-sellado: intacta
    assert _gates(factory)["outbox_lag_p99"]["ok"] is True  # 250 <= 300


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
    assert _gates(factory)["latencia_p95"]["ok"] is True  # 38.5 <= 600


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


# ------------------------------------------------------ purga del staging (§7)


def test_purge_deletes_old_applied_preserving_last_users_and_unapplied(db):
    factory = db
    now = datetime(2026, 7, 25, 12, 0, tzinfo=metrics.CYCLE_TZ)
    # cutoff = cierre del ciclo actual (2026-07-26 06:00) − 7 días.
    old = datetime(2026, 7, 15, 12, 0, tzinfo=metrics.CYCLE_TZ)
    recent = datetime(2026, 7, 24, 12, 0, tzinfo=metrics.CYCLE_TZ)
    u1, u2 = str(uuid.uuid4()), str(uuid.uuid4())
    rows = [
        # (lsn, tabla, op, pk, payload, received, applied?)
        (1, "jobs", "I", "j-old", {}, old, True),          # borra
        (2, "users", "I", u1, {"id": u1, "is_active": True}, old, True),   # borra
        (3, "users", "U", u1, {"id": u1, "is_active": False}, old, True),  # PRESERVA (última users de u1)
        (4, "users", "I", u2, {"id": u2, "is_active": False}, old, True),  # PRESERVA (última users de u2)
        (5, "users", "D", "u-gone", {}, old, True),        # borra (el ERASE ya corrió)
        (6, "jobs", "U", "j-pend", {}, old, False),        # PRESERVA (sin aplicar)
        (7, "jobs", "I", "j-recent", {}, recent, True),    # PRESERVA (reciente)
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
            return await projector._inactive_user_refs(s, [u1, u2])

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
    assert kept == {3, 4, 6, 7}
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
    """Un recompute que mide MENOS perfiles (p.ej. uno borrado por GDPR)
    elimina las filas profile:<id> obsoletas del cálculo anterior — en la
    misma transacción del cómputo, sin tocar las globales ni otros scopes."""
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
    assert _compute(factory)["profiles_measured"] == 1
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
        ("dedup_precision", "global", 0.96, {}),                  # OK
        ("dedup_recall", "global", 0.89, {}),                     # FALLO (<0.90)
        ("perdida", "global", 1, {}),                             # FALLO (==0)
        ("no_ingeribles", "global", 2, {}),                       # ALERTA (>0)
        ("outbox_lag_p99", "global", 299.0, {"samples_count": 9}),  # OK
        ("latencia_p95", "global", 700.0, {"lotes": 4}),            # FALLO
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
    assert gates["dedup_precision"]["ok"] is True
    assert gates["dedup_recall"]["ok"] is False
    assert gates["perdida"]["ok"] is False
    assert gates["no_ingeribles"] == {
        "value": 2.0, "umbral": 0, "kind": "alerta", "ok": False,
    }
    assert gates["outbox_lag_p99"]["ok"] is True
    assert gates["latencia_p95"]["ok"] is False
    assert gates["coste"] == {
        "value": 1234.0, "umbral": None, "kind": "alerta", "ok": True,
    }
    assert gates["reenlace_pct"]["ok"] is False

    # Ciclo VACÍO: gates sin datos NO demostrables (ok False) y sin perfiles.
    empty = _gates(factory, cycle=date(2026, 6, 1))
    assert empty["ndcg@10"]["ok"] is False
    assert "sin perfiles" in empty["ndcg@10"]["nota"]
    for m in ("dedup_precision", "dedup_recall", "perdida", "outbox_lag_p99",
              "latencia_p95"):
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
    _seed_metric(factory, cyc, "dedup_precision", "global", 0.96, {})
    _seed_metric(factory, cyc, "dedup_recall", "global", 0.95, {})
    _seed_metric(
        factory, cyc, "perdida", "global", 0,
        {"legacy_activos_ingeribles": 5, "slots_legacy_activos": 5,
         "staging_sin_aplicar_1h": 0},
    )
    _seed_metric(factory, cyc, "no_ingeribles", "global", 0, {})
    _seed_metric(factory, cyc, "outbox_lag_p99", "global", 12.0, {})
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
        f"ndcg@10::{p1}", "overlap@10", "dedup_precision", "perdida",
        "outbox_lag_p99", "latencia_p95", "coste", "reenlace_pct",
    ):
        assert needle in text
    # ndcg 0.70 < 0.75 falla: el único gate en rojo de este ciclo forzado
    # (gates: ndcg + falsos_negativos del perfil + 5 globales = 7).
    assert "CICLO NO APTO: 1/7" in text
    assert "alertas activas: 0" in text
    assert "perdida = 5 legacy vivos - 5 slots activos + 0 staging" in text
    assert "coste = 2 embeddings + 3 evaluaciones + 7.5s de worker" in text
    # Legible: tabla con cabecera y una línea por métrica evaluada.
    assert "métrica" in text and "estado" in text
    assert text.count("\n") > 15


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
