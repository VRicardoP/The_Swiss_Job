"""Proyector de la sombra B-02 (shadow/projector) contra Postgres real.

DoD (CONTRATOS_FASE_B §3/§7): (a) jobs I/U/D end-to-end hasta canónica —
corpus core espejo del legacy ACTIVO (perdida=0 sobre el fixture tras
drenar); (b) merge TOAST: U con `_omitted` preserva la description desde el
último raw conocido, y degrada con ALERTA si no hay previo; (c) cierre por
D/is_active/duplicate_of con la identidad compartida INTACTA (attach
cross-source previo + primary reparado — patrón del gate); (d) perfil sombra
(consumer "swissjob-shadow") con revisión y evaluación; (e) users inactivo
EXCLUIDO de evaluación sin borrar (y re-incluido al reactivar); (f) users
op=D → ERASE completo verificado tabla a tabla; (g) re-proyección
idempotente (segunda pasada sin cambios); (h) lotes registrados en
shadow_projection_batches con marcas coherentes; (i) tarea Celery
registrada y enrutada a core.harvest SIN beat. Además (1er análisis B-02):
single-flight con try-lock (2ª invocación sale limpia), una transacción POR
FUENTE con applied_at sellado en ella (crash entre fuentes → retry continúa)
y recuperación del flujo post-lote a la SALIDA condicionada a señal real
(2º análisis B-02): crash en _after_batch ⇒ la siguiente invocación
re-evalúa; sin crash ⇒ recovery_evaluated==0 sin scans extra, con las
marcas de lote escritas ANTES del replay (latencia_p95 intacta) y la
detección en UNA consulta por invocación (no una por perfil).

Aislamiento: BD DESECHABLE (jobhunt_proj_<hex>) migrada a head; el staging
se SIEMBRA SINTÉTICO (no se necesita el slot real, §7 "B-02 parcial") y
settings.CORE_DATABASE_URL se parchea para que task_session_factory (el
proyector y los flujos normales de embeddings/matching que dispara) apunte a
ella — estos tests JAMÁS tocan el staging del slot real de la BD compartida.
Ejecutar vía core-migrate.
"""

import asyncio
import itertools
import json
import logging
import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import embeddings, matching
from jobhunt_core.config import settings
from jobhunt_core.shadow import projector
from jobhunt_core.tests.alembic_runner import run_alembic
from jobhunt_core.tests.test_integration_matching import DirectionalBackend

_ADMIN = os.getenv("CORE_ADMIN_DATABASE_URL")
S = settings.CORE_DB_SCHEMA
SHA = "b" * 40

pytestmark = pytest.mark.skipif(
    not _ADMIN, reason="requiere BD (ejecutar vía core-migrate)"
)

# LSN sintético creciente (una "tx" por cambio; el orden es lo contractual).
_LSN = itertools.count(10_000)


def _admin_autocommit():
    return sa.create_engine(
        _ADMIN, poolclass=sa.pool.NullPool, isolation_level="AUTOCOMMIT"
    )


@pytest.fixture(scope="module")
def proj_db():
    """BD desechable con el esquema core migrado a head (URL asyncpg)."""
    dbname = f"jobhunt_proj_{uuid.uuid4().hex[:12]}"
    parts = urlsplit(_ADMIN)
    admin_engine = _admin_autocommit()
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
        yield async_url
    finally:
        engine.dispose()
        with admin_engine.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
        admin_engine.dispose()


@pytest.fixture()
def db(proj_db, monkeypatch):
    """Factory async sobre la BD desechable + settings parcheados (el
    proyector, embeddings y matching crean sus engines desde settings). La
    limpieza entre tests es un TRUNCATE CASCADE: la BD es de usar y tirar."""
    monkeypatch.setattr(settings, "CORE_DATABASE_URL", proj_db)
    engine = create_async_engine(
        proj_db,
        poolclass=sa.pool.NullPool,
        connect_args={"server_settings": {"search_path": f"{S}, public"}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory

    async def cleanup():
        async with engine.begin() as c:
            await c.execute(
                sa.text("TRUNCATE shadow_change_log, shadow_projection_batches")
            )
            await c.execute(sa.text("TRUNCATE integration_outbox CASCADE"))
            await c.execute(
                sa.text(
                    "TRUNCATE consumers, sources, vacancies, scoring_policies, "
                    "embedding_models CASCADE"
                )
            )
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


def _exec(factory, sql, **params):
    async def go():
        async with factory() as s:
            await s.execute(sa.text(sql), params)
            await s.commit()

    asyncio.run(go())


def _seed(factory, changes):
    """changes = [(src_table, op, pk, payload)] → staging sintético en orden
    LSN creciente (misma forma que deja la captura de B-01)."""
    rows = [
        {"l": next(_LSN), "s": 0, "t": t, "o": op, "p": pk, "j": json.dumps(payload)}
        for (t, op, pk, payload) in changes
    ]

    async def go():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO shadow_change_log "
                    "(lsn, seq_in_tx, src_table, op, pk, payload) "
                    "VALUES (:l, :s, :t, :o, :p, CAST(:j AS jsonb)) "
                    "ON CONFLICT (lsn, seq_in_tx) DO NOTHING"
                ),
                rows,
            )
            await s.commit()

    _run(go())


def _project(**kwargs):
    return _run(projector.project_pending(**kwargs))


def _job(pk, source, title="Backend Dev", company="ACME AG",
         description="python backend", active=True, dup=None, url=None,
         chash=None, **extra):
    """Payload de staging estilo mini-tabla legacy (whitelist ∩ esquema) con
    TODAS las columnas de contenido contractuales (JOB_PAYLOAD_MAP, §3): un
    U con solo `description` en `_omitted` debe dar _missing_cols() ==
    ('description',) — la degradación del caso huérfano depende SOLO de la
    omisión TOAST, no de columnas que el fixture no traía."""
    payload = {
        "title": title, "company": company, "description": description,
        "tags": ["py"], "location": "Zurich", "canton": "ZH",
        "language": "en", "seniority": "senior", "contract_type": "permanent",
        "remote": False, "salary_min_chf": 100000, "salary_max_chf": 130000,
        "salary_original": "100'000 - 130'000 CHF", "salary_currency": "CHF",
        "salary_period": "year",
        "url": url or f"https://legacy/{pk}", "source": source,
        "is_active": active, "duplicate_of": dup,
        "content_hash": chash or f"ch-{pk}-1",
    }
    payload.update(extra)
    return payload


def _profile(user_id, title="python developer",
             cv="cv con python y fastapi", skills=("python",)):
    return {
        "user_id": str(user_id), "title": title, "cv_text": cv,
        "skills": list(skills), "updated_at": "2026-07-25T10:00:00+00:00",
    }


def _source_id(factory, source):
    return _scalar(
        factory, "SELECT id FROM sources WHERE name = :n", n=f"legacy:{source}"
    )


def _active_exts(factory, source_id):
    return {
        r.external_id
        for r in _rows(
            factory,
            "SELECT l.external_id FROM source_listings l "
            "JOIN source_listing_incarnations i "
            "  ON i.source_listing_id = l.id AND i.ended_at IS NULL "
            "WHERE l.source_id = :s", s=source_id,
        )
    }


def _rev_counts(factory, source_id):
    return {
        r.ext: r.n
        for r in _rows(
            factory,
            "SELECT l.external_id AS ext, count(*) AS n "
            "FROM source_listing_revisions r "
            "JOIN source_listing_incarnations i ON i.id = r.incarnation_id "
            "JOIN source_listings l ON l.id = i.source_listing_id "
            "WHERE l.source_id = :s GROUP BY l.external_id", s=source_id,
        )
    }


def _seed_model_policy(factory, name):
    async def go():
        async with factory() as s:
            mid = await embeddings.register_model(s, name, SHA)
            polid = await matching.ensure_policy(s, "cosine", "v1")
            await s.commit()
            return mid, polid

    return _run(go())


# --------------------------------------------- (a) jobs I/U/D e2e + perdida=0


def test_jobs_i_u_d_end_to_end_mirror(db):
    factory = db
    src = f"fx{uuid.uuid4().hex[:6]}"
    # Ronda 1 — "backfill": activos, un inactivo y un duplicado (estos dos
    # JAMÁS entran al corpus: espejo del legacy ACTIVO).
    _seed(factory, [
        ("jobs", "I", "job-a", _job("job-a", src)),
        ("jobs", "I", "job-b", _job("job-b", src)),
        ("jobs", "I", "job-c", _job("job-c", src, active=False)),
        ("jobs", "I", "job-e", _job("job-e", src, dup="job-a")),
        ("jobs", "I", "job-d", _job("job-d", src)),
    ])
    t1 = _project()
    assert (t1["batches"], t1["changes"], t1["upserts"], t1["closes"]) == (1, 5, 3, 0)

    # Fuente core por fuente legacy + scope sombra idempotente DESHABILITADO.
    sid = _source_id(factory, src)
    assert sid is not None
    assert _scalar(factory, "SELECT tier FROM sources WHERE id = :s", s=sid) == 0
    scopes = _rows(
        factory,
        "SELECT params, tier, enabled FROM harvest_scopes WHERE source_id = :s",
        s=sid,
    )
    assert len(scopes) == 1
    assert scopes[0].params == {"shadow": True}
    assert scopes[0].tier == 0 and scopes[0].enabled is False

    # Ronda 2 — streaming: contenido nuevo, DELETE y refresco sin cambio.
    _seed(factory, [
        ("jobs", "U", "job-b", _job("job-b", src, description="python backend v2",
                                    chash="ch-job-b-2")),
        ("jobs", "D", "job-d", {}),
        ("jobs", "U", "job-a", _job("job-a", src)),  # mismo content_hash
    ])
    t2 = _project()
    assert (t2["batches"], t2["changes"], t2["upserts"], t2["closes"]) == (1, 3, 2, 1)
    assert t2["revisions_new"] == 1  # SOLO la v2 de job-b (el refresco no crea)

    # perdida = 0 sobre el fixture tras drenar: activos legacy {a, b} ==
    # encarnaciones activas de la fuente legacy:*.
    assert _active_exts(factory, sid) == {"job-a", "job-b"}
    slots = {
        r.external_id
        for r in _rows(
            factory,
            "SELECT external_id FROM source_listings WHERE source_id = :s", s=sid,
        )
    }
    assert slots == {"job-a", "job-b", "job-d"}  # inactivo/duplicado sin slot
    assert _rev_counts(factory, sid) == {"job-a": 1, "job-b": 2, "job-d": 1}

    # job-d: cerrado (ended_at) — su vacante conserva la historia.
    ended = _scalar(
        factory,
        "SELECT i.ended_at FROM source_listing_incarnations i "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "WHERE l.source_id = :s AND l.external_id = 'job-d'", s=sid,
    )
    assert ended is not None

    # Canónica de job-b = contenido v2 (end-to-end hasta offer_revisions).
    canon = _scalar(
        factory,
        "SELECT o.content->>'description' FROM vacancies v "
        "JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
        "JOIN source_listing_incarnations i ON i.id = v.primary_incarnation_id "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "WHERE l.source_id = :s AND l.external_id = 'job-b'", s=sid,
    )
    assert canon == "python backend v2"

    # Staging drenado y sellado.
    assert _scalar(
        factory,
        "SELECT count(*) FROM shadow_change_log WHERE applied_at IS NULL",
    ) == 0


# ------------------------------------------------------- (b) merge TOAST (§3)


def test_u_omitted_merges_description_from_last_known_raw(db):
    factory = db
    src = f"fx{uuid.uuid4().hex[:6]}"
    big = "descripción TOAST completa del puesto " * 50
    _seed(factory, [("jobs", "I", "job-t", _job("job-t", src, description=big))])
    _project()
    sid = _source_id(factory, src)

    # U TOAST real: description AUSENTE del payload (≠ NULL), _omitted la
    # registra (forma exacta que deja la captura para jobs, sin re-lectura).
    u = _job("job-t", src, title="Dev v2", chash="ch-job-t-2")
    del u["description"]
    u["_omitted"] = ["description"]
    _seed(factory, [("jobs", "U", "job-t", u)])
    _project()

    revs = _rows(
        factory,
        "SELECT r.raw FROM source_listing_revisions r "
        "JOIN source_listing_incarnations i ON i.id = r.incarnation_id "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "WHERE l.source_id = :s AND l.external_id = 'job-t' "
        "ORDER BY r.fetched_at, r.id", s=sid,
    )
    assert len(revs) == 2
    assert revs[-1].raw["title"] == "Dev v2"
    assert revs[-1].raw["description"] == big  # description PRESERVADA
    canon = _scalar(
        factory,
        "SELECT o.content->>'description' FROM vacancies v "
        "JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
        "JOIN source_listing_incarnations i ON i.id = v.primary_incarnation_id "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "WHERE l.source_id = :s AND l.external_id = 'job-t'", s=sid,
    )
    # La canónica pasa por la coerción central (_text hace strip); el raw de
    # arriba conserva el texto EXACTO.
    assert canon == big.strip()

    # Refresco TOAST SIN cambio (mismo content_hash legacy): el DO NOTHING
    # del sink lo absorbe — ni revisión ni canónica nuevas.
    u2 = _job("job-t", src, title="Dev v2", chash="ch-job-t-2")
    del u2["description"]
    u2["_omitted"] = ["description"]
    _seed(factory, [("jobs", "U", "job-t", u2)])
    t3 = _project()
    assert t3["revisions_new"] == 0
    assert _rev_counts(factory, sid) == {"job-t": 2}


def test_u_orphan_without_previous_value_degrades_with_alert(db, caplog):
    """Regla 3 del merge: U con columnas omitidas y SIN valor previo conocido
    (imposible salvo corrupción) NO crea revisión con contenido perdido —
    degrada a refresco de last_seen con ALERTA persistente."""
    factory = db
    src = f"fx{uuid.uuid4().hex[:6]}"
    # Fixture FIEL: _job cubre TODAS las columnas de contenido contractuales
    # — la ÚNICA ausente es la omitida TOAST y la degradación depende de ella.
    assert set(projector.JOB_PAYLOAD_MAP) <= set(_job("x", "s"))
    u = _job("job-orphan", src, chash="ch-x")
    del u["description"]
    u["_omitted"] = ["description"]
    _seed(factory, [("jobs", "U", "job-orphan", u)])
    with caplog.at_level(logging.ERROR, logger="jobhunt_core.shadow.projector"):
        totals = _project()
    assert totals["upserts"] == 0
    assert "SIN valor previo conocido" in caplog.text
    sid = _source_id(factory, src)
    assert _scalar(
        factory,
        "SELECT count(*) FROM source_listings WHERE source_id = :s", s=sid,
    ) == 0  # ni slot ni revisión: nada inventado
    # El cambio quedó SELLADO (no se re-procesa en bucle).
    assert _scalar(
        factory,
        "SELECT count(*) FROM shadow_change_log WHERE applied_at IS NULL",
    ) == 0


def test_alert_when_normalize_returns_none_for_legacy_source(db, caplog):
    """§3: normalize→None en fuente legacy:* es ALERTA (no solo warning) —
    el raw se persiste pero la vacante queda sin canónica."""
    factory = db
    src = f"fx{uuid.uuid4().hex[:6]}"
    _seed(factory, [("jobs", "I", "job-nt", _job("job-nt", src, title=None))])
    with caplog.at_level(logging.ERROR, logger="jobhunt_core.shadow.projector"):
        _project()
    assert "normalize_offer devolvió None" in caplog.text
    sid = _source_id(factory, src)
    assert _rev_counts(factory, sid) == {"job-nt": 1}  # raw persistido
    assert _scalar(
        factory,
        "SELECT v.current_offer_revision_id FROM vacancies v "
        "JOIN source_listing_incarnations i ON i.id = v.primary_incarnation_id "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "WHERE l.source_id = :s AND l.external_id = 'job-nt'", s=sid,
    ) is None


# ------------------------------- (c) cierres con identidad compartida intacta


def test_delete_closes_without_breaking_shared_identity(db, monkeypatch):
    """Patrón del gate (4/4b): attach cross-source previo por URL, luego el
    DELETE legacy cierra SOLO la encarnación legacy — la vacante compartida
    sigue viva con el primary REPARADO y la canónica del nuevo primary."""
    from jobhunt_core.harvest import identity as identity_mod
    from jobhunt_core.harvest import normalize as normalize_mod
    from jobhunt_core.harvest.sink import RawListingSink
    from jobhunt_core.harvest.types import RawListing

    factory = db
    src = f"fx{uuid.uuid4().hex[:6]}"
    _seed(factory, [
        ("jobs", "I", "job-x", _job("job-x", src, url="https://feed/x1")),
    ])
    _project()
    sid = _source_id(factory, src)

    other = f"otherboard{uuid.uuid4().hex[:6]}"
    monkeypatch.setitem(
        identity_mod._EXTRACTORS, other,
        lambda p: (p.get("title"), p.get("company_name")),
    )
    monkeypatch.setitem(
        normalize_mod._NORMALIZERS, other,
        lambda raw: {"title": raw.get("title"), "company": raw.get("company_name"),
                     "description": raw.get("description"), "tags": raw.get("tags"),
                     "location": None, "remote": None, "salary": None},
    )

    async def cross_source():
        async with factory() as s:
            src2, scope2 = uuid.uuid4(), uuid.uuid4()
            await s.execute(
                sa.text("INSERT INTO sources (id, name, tier) VALUES (:i, :n, 0)"),
                {"i": src2, "n": other},
            )
            await s.execute(
                sa.text(
                    "INSERT INTO harvest_scopes (id, source_id, params, tier) "
                    "VALUES (:i, :s, '{}'::jsonb, 0)"
                ),
                {"i": scope2, "s": src2},
            )
            # MISMA URL → attach a la vacante existente. Título DISTINTO a
            # propósito: es el discriminador de la canónica reconstruida.
            await RawListingSink().handle(
                s, str(scope2),
                (RawListing(external_id="x1", url="https://feed/x1",
                            payload={"title": "Python Engineer",
                                     "company_name": "ACME AG"}),),
            )
            await s.commit()
            return src2

    src2 = _run(cross_source())
    shared = _rows(
        factory,
        "SELECT count(*) AS n_inc, count(DISTINCT l.source_id) AS n_src, "
        "count(DISTINCT i.vacancy_id) AS n_vac "
        "FROM source_listing_incarnations i "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "WHERE l.url_normalized = 'https://feed/x1' AND i.ended_at IS NULL",
    )[0]
    assert (shared.n_inc, shared.n_src, shared.n_vac) == (2, 2, 1)
    vac_id = _scalar(
        factory,
        "SELECT i.vacancy_id FROM source_listing_incarnations i "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "WHERE l.source_id = :s AND l.external_id = 'job-x'", s=sid,
    )

    _seed(factory, [("jobs", "D", "job-x", {})])
    totals = _project()
    assert totals["closes"] == 1

    # La encarnación legacy quedó cerrada; la de la otra fuente sigue ACTIVA
    # sobre la MISMA vacante (identidad compartida intacta, DoD).
    legacy_inc = _rows(
        factory,
        "SELECT i.ended_at, i.vacancy_id FROM source_listing_incarnations i "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "WHERE l.source_id = :s AND l.external_id = 'job-x'", s=sid,
    )
    assert len(legacy_inc) == 1 and legacy_inc[0].ended_at is not None
    assert legacy_inc[0].vacancy_id == vac_id
    prim = _rows(
        factory,
        "SELECT i.ended_at, l.source_id, o.content->>'title' AS title "
        "FROM vacancies v "
        "JOIN source_listing_incarnations i ON i.id = v.primary_incarnation_id "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "LEFT JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
        "WHERE v.id = :v", v=vac_id,
    )[0]
    # Primary REPARADO a la activa de la otra fuente y canónica reconstruida
    # desde ESE primary (título discriminador — un puntero stale no pasaría).
    assert prim.ended_at is None and prim.source_id == src2
    assert prim.title == "Python Engineer"
    assert _scalar(
        factory, "SELECT archived_at FROM vacancies WHERE id = :v", v=vac_id
    ) is None


def test_close_by_inactive_or_duplicate_and_reopen(db):
    factory = db
    src = f"fx{uuid.uuid4().hex[:6]}"
    _seed(factory, [
        ("jobs", "I", "job-y", _job("job-y", src)),
        ("jobs", "I", "job-z", _job("job-z", src)),
    ])
    _project()
    sid = _source_id(factory, src)
    assert _active_exts(factory, sid) == {"job-y", "job-z"}

    _seed(factory, [
        ("jobs", "U", "job-y", _job("job-y", src, active=False)),
        ("jobs", "U", "job-z", _job("job-z", src, dup="job-y")),
    ])
    totals = _project()
    assert (totals["upserts"], totals["closes"]) == (0, 2)
    assert _active_exts(factory, sid) == set()

    # Reactivación: el slot reabre con una encarnación NUEVA (seq=2).
    _seed(factory, [("jobs", "U", "job-y", _job("job-y", src))])
    _project()
    seq = _scalar(
        factory,
        "SELECT i.seq FROM source_listing_incarnations i "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "WHERE l.source_id = :s AND l.external_id = 'job-y' "
        "AND i.ended_at IS NULL", s=sid,
    )
    assert seq == 2


# ------------------- (d)+(e) perfil sombra, evaluación y exclusión de inactivos


def test_profiles_evaluation_and_inactive_user_exclusion(db):
    factory = db
    src = f"fx{uuid.uuid4().hex[:6]}"
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    _seed_model_policy(factory, f"modelo-{uuid.uuid4().hex[:6]}")
    embeddings.set_backend_factory(lambda name, version: DirectionalBackend())
    try:
        _seed(factory, [
            ("jobs", "I", "job-p1", _job("job-p1", src, title="Python Developer",
                                         description="backend python fastapi")),
            ("users", "I", str(u1), {"id": str(u1), "is_active": True}),
            ("users", "I", str(u2), {"id": str(u2), "is_active": False}),
            ("user_profiles", "I", "prof-1", _profile(u1)),
            ("user_profiles", "I", "prof-2",
             _profile(u2, title="contable", cv="cv de contabilidad")),
        ])
        t1 = _project()

        # Consumer sombra ÚNICO + perfiles por external_ref = user_id.
        cons = _rows(factory, "SELECT id, name FROM consumers")
        assert [c.name for c in cons] == ["swissjob-shadow"]
        profs = {
            r.external_ref: r.id
            for r in _rows(
                factory,
                "SELECT id, external_ref FROM profiles WHERE consumer_id = :c",
                c=cons[0].id,
            )
        }
        assert set(profs) == {str(u1), str(u2)}

        # Contenido EXACTO {title, cv_text, skills} (PF.5) en la revisión
        # vigente — el resto de campos canónicos degradan a vacío/None.
        content = _rows(
            factory,
            "SELECT pr.content FROM profile_revision_activations a "
            "JOIN profile_revisions pr ON pr.id = a.revision_id "
            "WHERE a.profile_id = :p ORDER BY a.seq DESC LIMIT 1",
            p=profs[str(u1)],
        )[0].content
        assert content["title"] == "python developer"
        assert content["cv_text"] == "cv con python y fastapi"
        assert content["skills"] == ["python"]
        assert content["languages"] == [] and content["salary_min"] is None

        # u1 evaluado (corpus + vectores del flujo normal); u2 EXCLUIDO por
        # users.is_active=false — su revisión EXISTE (sin borrar nada).
        assert t1["profiles_evaluated"] == 1
        n1 = _scalar(
            factory,
            "SELECT count(*) FROM match_evaluations WHERE profile_id = :p",
            p=profs[str(u1)],
        )
        assert n1 >= 1
        assert _scalar(
            factory,
            "SELECT count(*) FROM match_evaluations WHERE profile_id = :p",
            p=profs[str(u2)],
        ) == 0
        assert _scalar(
            factory,
            "SELECT count(*) FROM profile_revisions WHERE profile_id = :p",
            p=profs[str(u2)],
        ) == 1

        # Reactivación + corpus nuevo ⇒ u2 entra al flujo normal.
        _seed(factory, [
            ("users", "U", str(u2), {"id": str(u2), "is_active": True}),
            ("jobs", "U", "job-p1", _job("job-p1", src, title="Python Developer",
                                         description="backend python fastapi v2",
                                         chash="ch-p1-2")),
        ])
        t2 = _project()
        assert t2["profiles_evaluated"] == 2
        assert _scalar(
            factory,
            "SELECT count(*) FROM match_evaluations WHERE profile_id = :p",
            p=profs[str(u2)],
        ) >= 1
    finally:
        embeddings.set_backend_factory(None)


def test_profile_omitted_cv_preserved_or_skipped_with_alert(db, caplog):
    """Fail-safe del contrato: un U de user_profiles con cv_text en _omitted
    SIN _backfilled (la captura no pudo re-leer) jamás crea una revisión con
    el CV vacío — completa desde la vigente; sin vigente, salta con alerta."""
    factory = db
    u = uuid.uuid4()
    _seed(factory, [("user_profiles", "I", "prof-c", _profile(u))])
    _project()

    partial = {"user_id": str(u), "title": "senior dev",
               "skills": ["python", "sql"], "_omitted": ["cv_text"]}
    _seed(factory, [("user_profiles", "U", "prof-c", partial)])
    _project()
    content = _rows(
        factory,
        "SELECT pr.content FROM profile_revision_activations a "
        "JOIN profile_revisions pr ON pr.id = a.revision_id "
        "JOIN profiles p ON p.id = a.profile_id "
        "WHERE p.external_ref = :r ORDER BY a.seq DESC LIMIT 1", r=str(u),
    )[0].content
    assert content["title"] == "senior dev"
    assert content["cv_text"] == "cv con python y fastapi"  # PRESERVADO

    # Sin revisión previa que preserve → se salta con ALERTA.
    u_new = uuid.uuid4()
    orphan = {"user_id": str(u_new), "title": "dev",
              "skills": [], "_omitted": ["cv_text"]}
    with caplog.at_level(logging.ERROR, logger="jobhunt_core.shadow.projector"):
        _seed(factory, [("user_profiles", "U", "prof-h", orphan)])
        _project()
    assert "SIN revisión previa" in caplog.text
    assert _scalar(
        factory,
        "SELECT count(*) FROM profile_revisions pr "
        "JOIN profiles p ON p.id = pr.profile_id WHERE p.external_ref = :r",
        r=str(u_new),
    ) == 0


# ------------------------------------------------ (f) users op=D → ERASE GDPR


def test_users_delete_erases_shadow_profile_table_by_table(db):
    factory = db
    src = f"fx{uuid.uuid4().hex[:6]}"
    u3 = uuid.uuid4()
    _seed_model_policy(factory, f"modelo-{uuid.uuid4().hex[:6]}")
    embeddings.set_backend_factory(lambda name, version: DirectionalBackend())
    try:
        _seed(factory, [
            ("jobs", "I", "job-g1", _job("job-g1", src, title="Python Developer")),
            ("users", "I", str(u3), {"id": str(u3), "is_active": True}),
            ("user_profiles", "I", "prof-g", _profile(u3)),
        ])
        _project()
    finally:
        embeddings.set_backend_factory(None)

    pid = _scalar(
        factory, "SELECT id FROM profiles WHERE external_ref = :r", r=str(u3)
    )
    assert pid is not None
    event_ids = [
        r.event_id
        for r in _rows(
            factory,
            "SELECT event_id FROM integration_outbox "
            "WHERE subject_profile_id = :p", p=pid,
        )
    ]
    # Sanidad: el grafo COMPLETO existe antes del borrado (si no, el test
    # pasaría en verde sin demostrar nada).
    pre = {
        tbl: _scalar(
            factory, f"SELECT count(*) FROM {tbl} WHERE profile_id = :p", p=pid
        )
        for tbl in ("profile_revisions", "profile_revision_activations",
                    "profile_embeddings", "match_evaluations",
                    "profile_vacancy_state")
    }
    assert all(n >= 1 for n in pre.values()), pre
    assert len(event_ids) >= 1

    _seed(factory, [("users", "D", str(u3), {})])
    totals = _project()
    assert totals["erased"] == 1

    # ERASE verificado tabla a tabla (revisiones, activaciones, vectores,
    # evaluaciones, estado, outbox+deliveries y el propio perfil).
    for tbl in pre:
        assert _scalar(
            factory, f"SELECT count(*) FROM {tbl} WHERE profile_id = :p", p=pid
        ) == 0, tbl
    assert _scalar(
        factory, "SELECT count(*) FROM profiles WHERE id = :p", p=pid
    ) == 0
    assert _scalar(
        factory,
        "SELECT count(*) FROM integration_outbox WHERE subject_profile_id = :p",
        p=pid,
    ) == 0
    assert _scalar(
        factory,
        "SELECT count(*) FROM integration_outbox_deliveries "
        "WHERE event_id = ANY(:e)", e=event_ids,
    ) == 0

    # Idempotencia del erase: un segundo op=D no encuentra nada y no falla.
    _seed(factory, [("users", "D", str(u3), {})])
    assert _project()["erased"] == 0


# ------------------------------------------- (g) re-proyección idempotente


def _corpus_snapshot(factory):
    """Estado observable del corpus/perfiles (sin last_seen ni timestamps de
    lote): idéntico ⇔ la re-proyección no duplicó ni movió nada."""
    def rows(sql):
        return [tuple(r) for r in _rows(factory, sql)]

    return {
        "slots": rows(
            "SELECT s.name, l.external_id FROM source_listings l "
            "JOIN sources s ON s.id = l.source_id ORDER BY 1, 2"
        ),
        "incs": rows(
            "SELECT l.external_id, i.seq, i.vacancy_id::text, i.ended_at "
            "FROM source_listing_incarnations i "
            "JOIN source_listings l ON l.id = i.source_listing_id "
            "ORDER BY 1, 2"
        ),
        "revs": rows(
            "SELECT l.external_id, r.content_hash "
            "FROM source_listing_revisions r "
            "JOIN source_listing_incarnations i ON i.id = r.incarnation_id "
            "JOIN source_listings l ON l.id = i.source_listing_id "
            "ORDER BY 1, 2"
        ),
        "canon": rows(
            "SELECT v.id::text, v.current_offer_revision_id::text, "
            "v.primary_incarnation_id::text FROM vacancies v ORDER BY 1"
        ),
        "activations": rows(
            "SELECT p.external_ref, a.seq, a.revision_id::text "
            "FROM profile_revision_activations a "
            "JOIN profiles p ON p.id = a.profile_id ORDER BY 1, 2"
        ),
        "evals": _scalar(factory, "SELECT count(*) FROM match_evaluations"),
        "outbox": _scalar(factory, "SELECT count(*) FROM integration_outbox"),
    }


def test_reprojection_after_crash_is_idempotent(db):
    factory = db
    src = f"fx{uuid.uuid4().hex[:6]}"
    u = uuid.uuid4()
    _seed(factory, [
        ("jobs", "I", "job-r1", _job("job-r1", src)),
        ("jobs", "I", "job-r2", _job("job-r2", src)),
    ])
    _project()
    _seed(factory, [
        ("jobs", "U", "job-r2", _job("job-r2", src, description="v2",
                                     chash="ch-r2-2")),
        ("jobs", "D", "job-r1", {}),
        ("users", "I", str(u), {"id": str(u), "is_active": True}),
        ("user_profiles", "I", "prof-r", _profile(u)),
    ])
    _project()
    snapshot = _corpus_snapshot(factory)
    n_batches = _scalar(
        factory, "SELECT count(*) FROM shadow_projection_batches"
    )

    # Crash simulado a mitad: applied_at NULL ⇒ TODO el staging se re-proyecta.
    _exec(factory, "UPDATE shadow_change_log SET applied_at = NULL")
    totals = _project()
    assert totals["changes"] == 6
    assert totals["revisions_new"] == 0  # segunda pasada SIN cambios
    assert _corpus_snapshot(factory) == snapshot  # nada duplicado ni movido
    # El lote re-proyectado se registra como marca nueva (traza temporal),
    # pero el corpus no se mueve.
    assert _scalar(
        factory, "SELECT count(*) FROM shadow_projection_batches"
    ) == n_batches + 1
    assert _scalar(
        factory,
        "SELECT count(*) FROM shadow_change_log WHERE applied_at IS NULL",
    ) == 0


# ------------------- single-flight y transacción por fuente (P2 del 1er B-02)


def test_second_concurrent_invocation_exits_clean(db, proj_db):
    """Single-flight: con el advisory lock de SESIÓN tomado por otra conexión
    (primera invocación en curso), la segunda hace pg_try_advisory_lock y
    sale limpia "ya en curso" SIN drenar nada ni bloquear al worker."""
    factory = db
    src = f"fx{uuid.uuid4().hex[:6]}"
    _seed(factory, [("jobs", "I", "job-sf", _job("job-sf", src))])

    async def with_lock_held():
        engine = create_async_engine(proj_db, poolclass=sa.pool.NullPool)
        try:
            async with engine.connect() as conn:
                await conn.execute(
                    sa.text("SELECT pg_advisory_lock(hashtextextended(:k, 0))"),
                    {"k": projector._PROJECTOR_LOCK},
                )
                await conn.commit()  # el lock de SESIÓN sobrevive al commit
                return await projector.project_pending()
        finally:
            await engine.dispose()

    totals = _run(with_lock_held())
    assert totals["status"] == "already_running"
    assert (totals["batches"], totals["changes"]) == (0, 0)
    assert _scalar(
        factory,
        "SELECT count(*) FROM shadow_change_log WHERE applied_at IS NULL",
    ) == 1  # el staging quedó intacto
    # Lock liberado al cerrar la conexión: la siguiente invocación drena.
    totals2 = _project()
    assert totals2["status"] == "ok" and totals2["changes"] == 1


def test_crash_between_sources_seals_partially_and_retry_completes(db, monkeypatch):
    """UNA transacción POR FUENTE con el applied_at sellado en ELLA: el crash
    tras la primera fuente deja sus cambios aplicados Y sellados, el resto en
    NULL y SIN marca de lote; el retry aplica SOLO lo restante y su marca
    cubre el conjunto realmente aplicado."""
    factory = db
    h = uuid.uuid4().hex[:6]
    src_a, src_b = f"fxa{h}", f"fxb{h}"  # orden alfabético determinista
    _seed(factory, [
        ("jobs", "I", "job-fa", _job("job-fa", src_a)),
        ("jobs", "I", "job-fb", _job("job-fb", src_b)),
    ])
    orig = projector._apply_source_upserts
    boom = {"armed": True}

    async def failing(session, sink, source_id, scope_id, source_name, entries):
        if boom["armed"] and source_name == f"legacy:{src_b}":
            raise RuntimeError("crash simulado entre fuentes")
        return await orig(session, sink, source_id, scope_id, source_name, entries)

    monkeypatch.setattr(projector, "_apply_source_upserts", failing)
    with pytest.raises(RuntimeError, match="crash simulado"):
        _project()

    # La fuente A quedó aplicada y sellada en SU tx; la B entera en rollback.
    sealed = {
        r.pk: r.applied_at
        for r in _rows(factory, "SELECT pk, applied_at FROM shadow_change_log")
    }
    assert sealed["job-fa"] is not None and sealed["job-fb"] is None
    assert _active_exts(factory, _source_id(factory, src_a)) == {"job-fa"}
    assert _source_id(factory, src_b) is None
    # Crash ANTES de la fila de lote: se pierde solo la MARCA, no datos.
    assert _scalar(factory, "SELECT count(*) FROM shadow_projection_batches") == 0

    # El retry (el lock quedó liberado pese al error) completa SOLO el resto.
    boom["armed"] = False
    totals = _project()
    assert (totals["changes"], totals["upserts"]) == (1, 1)
    assert _active_exts(factory, _source_id(factory, src_b)) == {"job-fb"}
    marks = _rows(factory, "SELECT changes FROM shadow_projection_batches")
    assert [m.changes for m in marks] == [1]
    assert _scalar(
        factory,
        "SELECT count(*) FROM shadow_change_log WHERE applied_at IS NULL",
    ) == 0


def test_replay_recovers_after_batch_crash(db, monkeypatch):
    """Recuperación de salida: un crash DESPUÉS del commit del lote pero EN
    _after_batch deja el staging sellado y sin rastro de lo no disparado —
    la siguiente invocación (staging vacío) drena embeddings y detecta por
    SEÑAL (revisión vigente sin match_evaluation para el modelo+política
    activos) que el perfil necesita evaluación, sin depender del change_log."""
    factory = db
    src = f"fx{uuid.uuid4().hex[:6]}"
    u = uuid.uuid4()
    _seed_model_policy(factory, f"modelo-{uuid.uuid4().hex[:6]}")
    embeddings.set_backend_factory(lambda name, version: DirectionalBackend())
    try:
        state = {"boom": True}
        orig = projector._after_batch

        async def maybe_boom(session_factory, result):
            if state["boom"]:
                raise RuntimeError("crash simulado en _after_batch")
            return await orig(session_factory, result)

        monkeypatch.setattr(projector, "_after_batch", maybe_boom)
        _seed(factory, [
            ("jobs", "I", "job-rb", _job("job-rb", src, title="Python Developer")),
            ("users", "I", str(u), {"id": str(u), "is_active": True}),
            ("user_profiles", "I", "prof-rb", _profile(u)),
        ])
        with pytest.raises(RuntimeError, match="_after_batch"):
            _project()
        # Lote commiteado y SELLADO; el disparo post-lote se perdió entero.
        assert _scalar(
            factory,
            "SELECT count(*) FROM shadow_change_log WHERE applied_at IS NULL",
        ) == 0
        assert _scalar(factory, "SELECT count(*) FROM match_evaluations") == 0

        state["boom"] = False  # staging vacío: _after_batch ya ni se alcanza
        totals = _project()
        assert totals["batches"] == 0
        assert totals["recovery_evaluated"] == 1
        assert _scalar(factory, "SELECT count(*) FROM match_evaluations") >= 1
    finally:
        embeddings.set_backend_factory(None)


def test_no_crash_no_recovery_and_marks_exclude_replay_time(db, monkeypatch):
    """Sin crash previo la señal de recuperación está APAGADA: la
    recuperación de salida no re-evalúa nada (cero scans extra —
    _run_profile_impl no se invoca con staging vacío) y las marcas de lote
    quedan escritas ANTES de empezar el replay (finished_at ≤ inicio del
    replay, ambos con el reloj del servidor): su tiempo no entra en
    finished_at − min_received_at de ningún lote (latencia_p95 intacta)."""
    factory = db
    src = f"fx{uuid.uuid4().hex[:6]}"
    u = uuid.uuid4()
    _seed_model_policy(factory, f"modelo-{uuid.uuid4().hex[:6]}")
    embeddings.set_backend_factory(lambda name, version: DirectionalBackend())
    try:
        replay_started = {}
        orig_replay = projector._replay_after_batch

        async def spying_replay(session_factory, evaluated):
            async with session_factory() as s:
                replay_started["at"] = (
                    await s.execute(sa.text("SELECT clock_timestamp()"))
                ).scalar_one()
            return await orig_replay(session_factory, evaluated)

        monkeypatch.setattr(projector, "_replay_after_batch", spying_replay)
        _seed(factory, [
            ("jobs", "I", "job-nr", _job("job-nr", src, title="Python Developer")),
            ("users", "I", str(u), {"id": str(u), "is_active": True}),
            ("user_profiles", "I", "prof-nr", _profile(u)),
        ])
        t1 = _project()
        # _after_batch ya evaluó al perfil en ESTA invocación ⇒ el replay no
        # lo repite aunque su señal siguiera encendida.
        assert t1["profiles_evaluated"] == 1
        assert t1["recovery_evaluated"] == 0

        # ORDEN TEMPORAL: todas las marcas de lote se escribieron ANTES del
        # inicio del replay — el tiempo del replay no entra en ninguna marca.
        finished = [
            r.finished_at
            for r in _rows(
                factory, "SELECT finished_at FROM shadow_projection_batches"
            )
        ]
        assert finished and all(f <= replay_started["at"] for f in finished)

        # Segunda invocación SIN crash y con staging vacío: señal apagada ⇒
        # recovery 0 y CERO scans extra (ninguna evaluación disparada).
        n_evals = _scalar(factory, "SELECT count(*) FROM match_evaluations")
        calls = {"n": 0}
        orig_run = projector._run_profile_impl

        async def counting_run(profile_id, limit, session_factory=None):
            calls["n"] += 1
            return await orig_run(
                profile_id, limit, session_factory=session_factory
            )

        monkeypatch.setattr(projector, "_run_profile_impl", counting_run)
        t2 = _project()
        assert (t2["batches"], t2["recovery_evaluated"]) == (0, 0)
        assert calls["n"] == 0  # cero scans extra
        assert _scalar(factory, "SELECT count(*) FROM match_evaluations") == n_evals
    finally:
        embeddings.set_backend_factory(None)


class _CountingSession:
    """Proxy mínimo que cuenta los execute de una sesión real (mide que la
    detección de recuperación sea un número CONSTANTE de queries)."""

    def __init__(self, session):
        self._session = session
        self.executed = 0

    async def execute(self, *args, **kwargs):
        self.executed += 1
        return await self._session.execute(*args, **kwargs)


def test_recovery_detection_is_one_query_not_one_per_profile(db, monkeypatch):
    """La detección de recuperación cuesta un número CONSTANTE de queries por
    invocación (consumer + perfiles + exclusión de inactivos + detección = 4)
    con N perfiles candidatos — jamás una consulta por perfil."""
    factory = db
    _seed_model_policy(factory, f"modelo-{uuid.uuid4().hex[:6]}")
    users = [uuid.uuid4() for _ in range(3)]

    async def no_after_batch(session_factory, result):
        return []  # simula el crash post-lote: nada evaluado ni drenado

    async def no_replay(session_factory, evaluated):
        return 0  # la recuperación se mide aparte, sobre sesión contada

    monkeypatch.setattr(projector, "_after_batch", no_after_batch)
    monkeypatch.setattr(projector, "_replay_after_batch", no_replay)
    _seed(factory, [
        row
        for u in users
        for row in [
            ("users", "I", str(u), {"id": str(u), "is_active": True}),
            ("user_profiles", "I", f"prof-{u}", _profile(u)),
        ]
    ])
    _project()

    async def measure():
        async with factory() as s:
            counting = _CountingSession(s)
            targets = await projector._recovery_targets(counting, set())
            return counting.executed, targets

    executed, targets = _run(measure())
    # Los 3 perfiles necesitan recuperación (revisión vigente sin evaluación).
    assert len(targets) == 3
    assert executed == 4  # constante: NO escala con el número de perfiles


def test_batches_recorded_with_coherent_marks(db):
    factory = db
    src = f"fx{uuid.uuid4().hex[:6]}"
    _seed(factory, [
        ("jobs", "I", f"job-m{i}", _job(f"job-m{i}", src)) for i in range(5)
    ])
    totals = _project(batch_size=2)
    assert (totals["batches"], totals["changes"]) == (3, 5)

    batches = _rows(
        factory,
        "SELECT first_lsn, last_lsn, min_received_at, started_at, finished_at, "
        "changes, revisions_new FROM shadow_projection_batches "
        "ORDER BY first_lsn",
    )
    assert [b.changes for b in batches] == [2, 2, 1]
    assert sum(b.revisions_new for b in batches) == 5
    for b in batches:
        assert b.first_lsn <= b.last_lsn
        assert b.min_received_at <= b.started_at <= b.finished_at
    # Lotes en orden LSN sin solaparse: la fuente de latencia_p95 es fiable.
    for prev, nxt in zip(batches, batches[1:]):
        assert prev.last_lsn < nxt.first_lsn
    assert _scalar(
        factory,
        "SELECT count(*) FROM shadow_change_log WHERE applied_at IS NULL",
    ) == 0


def test_max_batches_limits_the_drain(db):
    factory = db
    src = f"fx{uuid.uuid4().hex[:6]}"
    _seed(factory, [
        ("jobs", "I", f"job-l{i}", _job(f"job-l{i}", src)) for i in range(3)
    ])
    totals = _project(batch_size=1, max_batches=2)
    assert (totals["batches"], totals["changes"]) == (2, 2)
    assert _scalar(
        factory,
        "SELECT count(*) FROM shadow_change_log WHERE applied_at IS NULL",
    ) == 1  # el resto queda para la siguiente invocación


# ------------------------------------------------ (i) tarea Celery registrada


def test_task_registered_routed_and_runs(db):
    from jobhunt_core.celery_app import celery_app
    from jobhunt_core.tasks.shadow import project_task

    assert "jobhunt.shadow.project" in celery_app.tasks
    assert celery_app.conf.task_routes["jobhunt.shadow.project"] == {
        "queue": "core.harvest"
    }
    # SIN beat (contrato): la tarea solo se registra — la dispara el runner
    # de ciclos (B-05) o el operador.
    assert not celery_app.conf.beat_schedule

    result = project_task.apply()  # staging vacío en la BD desechable
    assert result.successful()
    assert result.result["batches"] == 0 and result.result["changes"] == 0
