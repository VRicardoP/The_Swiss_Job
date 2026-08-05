"""Set etiquetado B-03 (shadow/labels + core0008a) contra Postgres real.

DoD (CONTRATOS_FASE_B §4/§7): seed desde feedback legacy (3/2/0/0) trazable;
re-seed JAMÁS pisa curación manual; set congelado = oráculo inmutable (guard
sin TOCTOU); pares dedup canónicos (LEAST/GREATEST) con CHECK a<>b; mapeo
job_ref→vacante por CUALQUIER encarnación (cerrada incluida); ciclo de
migración core0008a sobre BD desechable. El esquema legacy es DESECHABLE
(shadow_fx_<hex>) — estos tests JAMÁS tocan `public`. Ejecutar vía core-migrate.
"""

import asyncio
import os
import re
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import profiles
from jobhunt_core.config import settings
from jobhunt_core.shadow import labels
from jobhunt_core.tests import dbcleanup
from jobhunt_core.tests.alembic_runner import run_alembic

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {
        "consumers": [], "sets": [], "dedup_refs": [], "sources": [], "scopes": [],
    }
    yield factory, created

    async def cleanup():
        async with factory() as s:
            # Sets/pares ANTES que el grafo de consumers (los juicios caen por
            # CASCADE del set; el set caería por CASCADE del perfil, pero el
            # borrado explícito no depende de ese detalle).
            await dbcleanup.purge_shadow(s, created["sets"], created["dedup_refs"])
            await dbcleanup.purge_consumer_graph(s, created["consumers"])
            await dbcleanup.purge_source_graph(s, created["sources"], created["scopes"])
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


@pytest.fixture()
def legacy_fx():
    """Esquema legacy DESECHABLE (shadow_fx_<hex>) con mini-tablas jobs y
    match_results, creado con la conexión ADMIN — JAMÁS toca `public`.

    Al rol del core se le da SELECT (en la sombra real ese GRANT read-only
    enumerado lo instala B-01 sobre `public`; aquí el esquema es de usar y
    tirar). DROP SCHEMA ... CASCADE al final.

    P2-8: el esquema fixture debe vivir en la MISMA BD que usan los tests —
    con el aislamiento de sesión (tests/conftest.py) esa es la BD DESECHABLE
    de la suite, no la de dev a la que apunta el DSN admin: se re-apunta el
    path de la URL admin al dbname de settings.CORE_DATABASE_URL (mismas
    credenciales admin, BD de la suite)."""
    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    parts = urlsplit(admin_url)
    admin_url = urlunsplit(
        (parts.scheme, parts.netloc, urlsplit(settings.CORE_DATABASE_URL).path, "", "")
    )
    schema = f"shadow_fx_{uuid.uuid4().hex[:10]}"
    core_role = urlsplit(settings.CORE_DATABASE_URL).username
    assert re.fullmatch(r"[a-z_][a-z0-9_]*", core_role)  # interpolación segura
    admin_engine = create_async_engine(admin_url, poolclass=sa.pool.NullPool)

    async def create():
        async with admin_engine.begin() as c:
            await c.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            await c.execute(
                sa.text(
                    f"CREATE TABLE {schema}.jobs ("
                    f"hash varchar(32) PRIMARY KEY, "
                    f"duplicate_of varchar(32), "
                    f"is_active boolean NOT NULL DEFAULT true)"
                )
            )
            await c.execute(
                sa.text(
                    f"CREATE TABLE {schema}.match_results ("
                    f"user_id uuid NOT NULL, "
                    f"job_hash varchar(32) NOT NULL, "
                    f"feedback varchar(20))"
                )
            )
            await c.execute(
                sa.text(f'GRANT USAGE ON SCHEMA "{schema}" TO {core_role}')
            )
            await c.execute(
                sa.text(
                    f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO {core_role}'
                )
            )

    asyncio.run(create())
    yield admin_engine, schema

    async def drop():
        async with admin_engine.begin() as c:
            await c.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()

    asyncio.run(drop())


def _run(coro):
    return asyncio.run(coro)


def _rows(factory, sql, **params):
    async def go():
        async with factory() as s:
            return (await s.execute(sa.text(sql), params)).all()

    return asyncio.run(go())


def _legacy_insert(admin_engine, sql, rows):
    async def go():
        async with admin_engine.begin() as c:
            for r in rows:
                await c.execute(sa.text(sql), r)

    asyncio.run(go())


def _mk_profile(factory, created, external_ref: str) -> uuid.UUID:
    async def go():
        async with factory() as s:
            cid = await profiles.ensure_consumer(
                s, f"shadow-tenant-{uuid.uuid4().hex[:8]}"
            )
            pid = await profiles.upsert_profile(s, cid, external_ref)
            await s.commit()
            return cid, pid

    cid, pid = _run(go())
    created["consumers"].append(cid)
    return pid


def _mk_set(factory, created, pid, name="ronda-1") -> uuid.UUID:
    async def go():
        async with factory() as s:
            sid = await labels.create_set(s, pid, name)
            await s.commit()
            return sid

    sid = _run(go())
    if sid not in created["sets"]:
        created["sets"].append(sid)
    return sid


def _seed(factory, sid, schema) -> int:
    async def go():
        async with factory() as s:
            n = await labels.seed_labels(s, sid, legacy_schema=schema)
            await s.commit()
            return n

    return _run(go())


# Feedback legacy → job_hash de prueba (mapeo contractual 3/2/0/0).
def _seed_legacy_feedback(admin_engine, schema, user_id, prefix):
    rows = [
        {"u": user_id, "j": f"{prefix}-up", "f": "thumbs_up"},      # → 2
        {"u": user_id, "j": f"{prefix}-app", "f": "applied"},       # → 3
        {"u": user_id, "j": f"{prefix}-down", "f": "thumbs_down"},  # → 0
        {"u": user_id, "j": f"{prefix}-dis", "f": "dismissed"},     # → 0
        {"u": user_id, "j": f"{prefix}-null", "f": None},           # no siembra
        {"u": user_id, "j": f"{prefix}-raro", "f": "saved"},        # no siembra
    ]
    _legacy_insert(
        admin_engine,
        f"INSERT INTO {schema}.match_results (user_id, job_hash, feedback) "
        f"VALUES (:u, :j, :f)",
        rows,
    )


def test_create_set_idempotent_and_freeze_idempotent(db):
    factory, created = db
    pid = _mk_profile(factory, created, str(uuid.uuid4()))
    sid = _mk_set(factory, created, pid)
    assert _mk_set(factory, created, pid) == sid  # UNIQUE(profile_id, name)
    assert _mk_set(factory, created, pid, name="ronda-2") != sid  # otra ronda

    async def freeze():
        async with factory() as s:
            ts = await labels.freeze_set(s, sid)
            await s.commit()
            return ts

    t1 = _run(freeze())
    assert t1 is not None
    t2 = _run(freeze())
    assert t2 == t1  # idempotente: devuelve el frozen_at EXISTENTE, sin error


def test_seed_maps_legacy_feedback_to_relevance(db, legacy_fx):
    factory, created = db
    admin_engine, schema = legacy_fx
    user_id = uuid.uuid4()
    p = uuid.uuid4().hex[:6]
    _seed_legacy_feedback(admin_engine, schema, user_id, p)
    # external_ref del perfil core = user_id legacy (§3).
    pid = _mk_profile(factory, created, str(user_id))
    sid = _mk_set(factory, created, pid)

    assert _seed(factory, sid, schema) == 4  # solo los 4 feedbacks mapeables
    rows = _rows(
        factory,
        "SELECT job_ref, relevance, source FROM labeled_judgments "
        "WHERE set_id = :s ORDER BY job_ref", s=sid,
    )
    assert {r.job_ref: r.relevance for r in rows} == {
        f"{p}-up": 2, f"{p}-app": 3, f"{p}-down": 0, f"{p}-dis": 0,
    }
    assert all(r.source == "seed_feedback" for r in rows)  # trazable al origen


def test_reseed_does_not_override_manual_curation(db, legacy_fx):
    factory, created = db
    admin_engine, schema = legacy_fx
    user_id = uuid.uuid4()
    p = uuid.uuid4().hex[:6]
    _seed_legacy_feedback(admin_engine, schema, user_id, p)
    pid = _mk_profile(factory, created, str(user_id))
    sid = _mk_set(factory, created, pid)
    assert _seed(factory, sid, schema) == 4

    async def curate():
        # Curación manual: el humano rebaja el thumbs_up a 1 (marginal).
        async with factory() as s:
            await labels.add_judgment(s, sid, f"{p}-up", 1)
            await s.commit()

    _run(curate())
    assert _seed(factory, sid, schema) == 0  # re-seed: DO NOTHING, no pisa
    rows = _rows(
        factory,
        "SELECT relevance, source FROM labeled_judgments "
        "WHERE set_id = :s AND job_ref = :j", s=sid, j=f"{p}-up",
    )
    assert (rows[0].relevance, rows[0].source) == (1, "manual")  # curación intacta
    n = _rows(
        factory,
        "SELECT count(*) AS n FROM labeled_judgments WHERE set_id = :s", s=sid,
    )
    assert n[0].n == 4


def test_frozen_set_rejects_judgments_without_inserting(db, legacy_fx):
    factory, created = db
    admin_engine, schema = legacy_fx
    user_id = uuid.uuid4()
    p = uuid.uuid4().hex[:6]
    _seed_legacy_feedback(admin_engine, schema, user_id, p)
    pid = _mk_profile(factory, created, str(user_id))
    sid = _mk_set(factory, created, pid)
    assert _seed(factory, sid, schema) == 4

    async def freeze():
        async with factory() as s:
            await labels.freeze_set(s, sid)
            await s.commit()

    _run(freeze())

    async def add_on_frozen():
        async with factory() as s:
            await labels.add_judgment(s, sid, f"{p}-nuevo", 2)
            await s.commit()

    with pytest.raises(labels.LabeledSetFrozenError):
        _run(add_on_frozen())

    async def reseed_on_frozen():
        async with factory() as s:
            await labels.seed_labels(s, sid, legacy_schema=schema)
            await s.commit()

    with pytest.raises(labels.LabeledSetFrozenError):  # mismo guard en el seed
        _run(reseed_on_frozen())

    rows = _rows(
        factory,
        "SELECT count(*) AS n FROM labeled_judgments WHERE set_id = :s", s=sid,
    )
    assert rows[0].n == 4  # NADA se insertó sobre el set congelado
    rows = _rows(
        factory,
        "SELECT count(*) AS n FROM labeled_judgments "
        "WHERE set_id = :s AND job_ref = :j", s=sid, j=f"{p}-nuevo",
    )
    assert rows[0].n == 0

    async def add_on_missing():
        async with factory() as s:
            await labels.add_judgment(s, uuid.uuid4(), f"{p}-x", 2)

    with pytest.raises(labels.LabeledSetNotFoundError):
        _run(add_on_missing())

    async def freeze_missing():
        async with factory() as s:
            await labels.freeze_set(s, uuid.uuid4())

    with pytest.raises(labels.LabeledSetNotFoundError):
        _run(freeze_missing())


def test_judgment_checks_relevance_and_source(db):
    factory, created = db
    pid = _mk_profile(factory, created, str(uuid.uuid4()))
    sid = _mk_set(factory, created, pid)

    async def bad_relevance_python():
        async with factory() as s:
            await labels.add_judgment(s, sid, "r5", 5)

    with pytest.raises(ValueError):  # guard explícito antes de llegar a BD
        _run(bad_relevance_python())

    async def bad_relevance_db():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO labeled_judgments (set_id, job_ref, relevance, source) "
                    "VALUES (:s, 'r4', 4, 'manual')"
                ),
                {"s": sid},
            )
            await s.commit()

    with pytest.raises(IntegrityError):  # CHECK relevance 0..3
        _run(bad_relevance_db())

    async def bad_source_db():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO labeled_judgments (set_id, job_ref, relevance, source) "
                    "VALUES (:s, 'src', 2, 'oracle')"
                ),
                {"s": sid},
            )
            await s.commit()

    with pytest.raises(IntegrityError):  # CHECK source IN (seed_feedback, manual)
        _run(bad_source_db())


def test_seed_dedup_pairs_canonical_unique_and_checks(db, legacy_fx):
    factory, created = db
    admin_engine, schema = legacy_fx
    p = uuid.uuid4().hex[:6]
    ja, jb, jc, jd, je = (f"{p}-aa", f"{p}-bb", f"{p}-cc", f"{p}-dd", f"{p}-ee")
    created["dedup_refs"] += [ja, jb, jc, jd, je]
    _legacy_insert(
        admin_engine,
        f"INSERT INTO {schema}.jobs (hash, duplicate_of, is_active) "
        f"VALUES (:h, :d, :a)",
        [
            {"h": ja, "d": None, "a": True},   # canónico vivo
            {"h": jd, "d": ja, "a": False},    # INVERTIDO: hash mayor → menor
            {"h": jb, "d": jc, "a": False},    # el MISMO par ...
            {"h": jc, "d": jb, "a": False},    # ... en ambas direcciones
            {"h": je, "d": je, "a": False},    # self-dup: violaría a<>b, se filtra
        ],
    )

    async def seed(limit=None):
        async with factory() as s:
            n = await labels.seed_dedup_pairs(s, legacy_schema=schema, limit=limit)
            await s.commit()
            return n

    # limit determinista (ORDER BY a, b): el primer par es (aa, dd).
    assert _run(seed(limit=1)) == 1
    rows = _rows(
        factory,
        "SELECT job_ref_a, job_ref_b, verdict, source FROM labeled_dedup_pairs "
        "WHERE job_ref_a LIKE :p ORDER BY job_ref_a", p=f"{p}-%",
    )
    assert [(r.job_ref_a, r.job_ref_b) for r in rows] == [(ja, jd)]  # normalizado

    assert _run(seed()) == 1  # el resto: SOLO (bb, cc) — ambas direcciones colapsan
    rows = _rows(
        factory,
        "SELECT job_ref_a, job_ref_b, verdict, source FROM labeled_dedup_pairs "
        "WHERE job_ref_a LIKE :p ORDER BY job_ref_a", p=f"{p}-%",
    )
    assert [(r.job_ref_a, r.job_ref_b) for r in rows] == [(ja, jd), (jb, jc)]
    assert all(r.job_ref_a < r.job_ref_b for r in rows)  # menor SIEMPRE primero
    assert all(
        (r.verdict, r.source) == ("duplicate", "seed_duplicate_of") for r in rows
    )
    assert _run(seed()) == 0  # re-seed: ON CONFLICT DO NOTHING

    async def insert_reversed():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO labeled_dedup_pairs "
                    "(job_ref_a, job_ref_b, verdict, source) "
                    "VALUES (:a, :b, 'duplicate', 'manual')"
                ),
                {"a": jd, "b": ja},  # (b,a) del par ya sembrado
            )
            await s.commit()

    with pytest.raises(IntegrityError):  # (a,b) == (b,a): índice LEAST/GREATEST
        _run(insert_reversed())

    async def insert_self_pair():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO labeled_dedup_pairs "
                    "(job_ref_a, job_ref_b, verdict, source) "
                    "VALUES (:a, :a, 'duplicate', 'manual')"
                ),
                {"a": je},
            )
            await s.commit()

    with pytest.raises(IntegrityError):  # CHECK a<>b
        _run(insert_self_pair())

    async def insert_bad_verdict():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO labeled_dedup_pairs "
                    "(job_ref_a, job_ref_b, verdict, source) "
                    "VALUES (:a, :b, 'maybe', 'manual')"
                ),
                {"a": ja, "b": je},
            )
            await s.commit()

    with pytest.raises(IntegrityError):  # CHECK verdict IN (duplicate, distinct)
        _run(insert_bad_verdict())


def test_map_job_refs_resolves_closed_incarnations_deterministically(db):
    """§4: la vacante persiste aunque el job legacy muera — el mapeo resuelve
    por encarnación CERRADA también, y con varias gana la de mayor seq."""
    factory, created = db
    p = uuid.uuid4().hex[:8]
    ref_closed, ref_multi, ref_ajena = f"{p}-closed", f"{p}-multi", f"{p}-ajena"
    src_legacy, src_ajena = uuid.uuid4(), uuid.uuid4()
    created["sources"] += [src_legacy, src_ajena]
    v1, v2a, v2b, v3 = (uuid.uuid4() for _ in range(4))

    async def build():
        async with factory() as s:
            for sid_, name in (
                (src_legacy, f"legacy:fx{p}"), (src_ajena, f"plainfx{p}"),
            ):
                await s.execute(
                    sa.text("INSERT INTO sources (id, name, tier) VALUES (:i, :n, 0)"),
                    {"i": sid_, "n": name},
                )
            for vid in (v1, v2a, v2b, v3):
                await s.execute(
                    sa.text("INSERT INTO vacancies (id) VALUES (:i)"), {"i": vid}
                )
            slots = (
                # (listing, source, external_id, [(vacancy, seq, cerrada)])
                (uuid.uuid4(), src_legacy, ref_closed, [(v1, 1, True)]),
                # Slot reciclado: DOS encarnaciones CERRADAS → mayor seq (v2b).
                (uuid.uuid4(), src_legacy, ref_multi, [(v2a, 1, True), (v2b, 2, True)]),
                # Fuente NO legacy: JAMÁS resuelve, ni con encarnación activa.
                (uuid.uuid4(), src_ajena, ref_ajena, [(v3, 1, False)]),
            )
            for lid, sid_, ext, incs in slots:
                await s.execute(
                    sa.text(
                        "INSERT INTO source_listings "
                        "(id, source_id, external_id, url_normalized) "
                        "VALUES (:i, :s, :e, :u)"
                    ),
                    {"i": lid, "s": sid_, "e": ext, "u": f"https://fx/{ext}"},
                )
                for vid, seq, closed in incs:
                    await s.execute(
                        sa.text(
                            "INSERT INTO source_listing_incarnations "
                            "(id, source_listing_id, vacancy_id, seq, url, ended_at) "
                            "VALUES (:i, :l, :v, :q, :u, "
                            "CASE WHEN :c THEN now() END)"
                        ),
                        {
                            "i": uuid.uuid4(), "l": lid, "v": vid, "q": seq,
                            "u": f"https://fx/{ext}/{seq}", "c": closed,
                        },
                    )
            await s.commit()

    _run(build())

    async def resolve():
        async with factory() as s:
            return await labels.map_job_refs_to_vacancies(
                s, [ref_closed, ref_multi, ref_ajena, f"{p}-fantasma"]
            )

    mapping = _run(resolve())
    assert mapping == {ref_closed: v1, ref_multi: v2b}

    async def resolve_empty():
        async with factory() as s:
            return await labels.map_job_refs_to_vacancies(s, [])

    assert _run(resolve_empty()) == {}


def test_core0008a_downgrade_upgrade_cycle_on_disposable_db():
    """Ciclo core0008a: head → core0007 → head sobre BD DESECHABLE con datos
    en las 4 tablas delante del downgrade (versión reducida del rehearsal —
    jamás la BD compartida de la suite)."""
    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    dbname = f"jobhunt_b03mig_{uuid.uuid4().hex[:12]}"
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
                await c.execute(
                    sa.text(f'CREATE SCHEMA IF NOT EXISTS "{settings.CORE_DB_SCHEMA}"')
                )

        asyncio.run(bootstrap())
        run_alembic(temp_url, "upgrade", "head")

        async def seed_and_version():
            async with factory() as s:
                cid = await profiles.ensure_consumer(s, "b03-tenant")
                pid = await profiles.upsert_profile(s, cid, "user-b03")
                sid = await labels.create_set(s, pid, "ronda-1")
                await labels.add_judgment(s, sid, "j-1", 3)
                await s.execute(
                    sa.text(
                        "INSERT INTO labeled_dedup_pairs "
                        "(job_ref_a, job_ref_b, verdict, source) "
                        "VALUES ('a-1', 'b-1', 'duplicate', 'manual')"
                    )
                )
                await s.execute(
                    sa.text(
                        "INSERT INTO shadow_cycle_metrics "
                        "(cycle_id, metric, scope, value) "
                        "VALUES (DATE '2026-07-24', 'ndcg@10', 'global', 0.61)"
                    )
                )
                await s.commit()
                return (
                    await s.execute(
                        sa.text("SELECT version_num FROM alembic_version")
                    )
                ).scalar_one()

        assert asyncio.run(seed_and_version()) == "core0018"

        run_alembic(temp_url, "downgrade", "core0007")

        async def verify_down():
            async with factory() as s:
                version = (
                    await s.execute(
                        sa.text("SELECT version_num FROM alembic_version")
                    )
                ).scalar_one()
                assert version == "core0007"
                remaining = (
                    await s.execute(
                        sa.text(
                            "SELECT count(*) FROM pg_tables WHERE schemaname = :s "
                            "AND tablename IN ('labeled_sets', 'labeled_judgments', "
                            "'labeled_dedup_pairs', 'shadow_cycle_metrics')"
                        ),
                        {"s": settings.CORE_DB_SCHEMA},
                    )
                ).scalar_one()
                assert remaining == 0  # downgrade limpio: las 4 fuera

        asyncio.run(verify_down())
        run_alembic(temp_url, "upgrade", "head")

        async def verify_up():
            async with factory() as s:
                version = (
                    await s.execute(
                        sa.text("SELECT version_num FROM alembic_version")
                    )
                ).scalar_one()
                assert version == "core0018"
                # El esquema re-creado FUNCIONA y con sus guardas: smoke real.
                cid = await profiles.ensure_consumer(s, "b03-post")
                pid = await profiles.upsert_profile(s, cid, "user-post")
                sid = await labels.create_set(s, pid, "ronda-post")
                await labels.add_judgment(s, sid, "j-post", 2)
                await s.commit()
                idx = (
                    await s.execute(
                        sa.text(
                            "SELECT count(*) FROM pg_indexes WHERE schemaname = :s "
                            "AND indexname = 'uq_labeled_dedup_pair'"
                        ),
                        {"s": settings.CORE_DB_SCHEMA},
                    )
                ).scalar_one()
                assert idx == 1  # índice de expresión re-creado

        asyncio.run(verify_up())
        asyncio.run(temp_engine.dispose())
    finally:

        async def drop_db():
            async with admin_engine.connect() as c:
                await c.execute(
                    sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
                )
            await admin_engine.dispose()

        asyncio.run(drop_db())
