"""Perfiles + profile_revisions/profile_embeddings (A-07) contra Postgres real.

DoD: 2 perfiles; embebido por model_id (última revisión de cada perfil, mismo
backend por (name, version) que las ofertas); FK COMPUESTA (revision, profile)
impide mezclar perfiles. Ejecutar vía core-migrate.
"""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import embeddings, profiles
from jobhunt_core.config import settings

SHA_A = "a" * 40

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {"consumers": [], "models": []}
    yield factory, created

    async def cleanup():
        async with factory() as s:
            cons = created["consumers"]
            if cons:
                await s.execute(
                    sa.text(
                        "DELETE FROM profile_embeddings WHERE profile_id IN "
                        "(SELECT id FROM profiles WHERE consumer_id = ANY(:c))"
                    ),
                    {"c": cons},
                )
                await s.execute(
                    sa.text(
                        "DELETE FROM profile_revision_activations WHERE profile_id IN "
                        "(SELECT id FROM profiles WHERE consumer_id = ANY(:c))"
                    ),
                    {"c": cons},
                )
                await s.execute(
                    sa.text(
                        "DELETE FROM profile_revisions WHERE profile_id IN "
                        "(SELECT id FROM profiles WHERE consumer_id = ANY(:c))"
                    ),
                    {"c": cons},
                )
                await s.execute(
                    sa.text("DELETE FROM profiles WHERE consumer_id = ANY(:c)"),
                    {"c": cons},
                )
                await s.execute(
                    sa.text("DELETE FROM consumers WHERE id = ANY(:c)"), {"c": cons}
                )
            for mid in created["models"]:
                await s.execute(
                    sa.text("DELETE FROM offer_embeddings WHERE model_id = :m"), {"m": mid}
                )
                await s.execute(
                    sa.text(
                        f"DROP TABLE IF EXISTS {settings.CORE_DB_SCHEMA}."
                        f"offer_embeddings_{mid.hex[:16]}"
                    )
                )
                await s.execute(
                    sa.text("DELETE FROM embedding_models WHERE id = :m"), {"m": mid}
                )
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


def _run(coro):
    return asyncio.run(coro)


def _setup_consumer(factory, created, name="tenant-test"):
    async def go():
        async with factory() as s:
            cid = await profiles.ensure_consumer(s, name)
            await s.commit()
            return cid

    cid = _run(go())
    if cid not in created["consumers"]:
        created["consumers"].append(cid)
    return cid


def _register(factory, created, name="modelo-test", version=SHA_A):
    async def go():
        async with factory() as s:
            mid = await embeddings.register_model(s, name, version)
            await s.commit()
            return mid

    mid = _run(go())
    if mid not in created["models"]:
        created["models"].append(mid)
    return mid


CONTENT_1 = {"title": "Backend Dev", "cv_text": "10 años Python", "skills": ["python", "sql"]}
CONTENT_2 = {"title": "Data Engineer", "cv_text": "ETL y pipelines", "skills": ["spark"]}


def _save(factory, profile_id, content):
    async def go():
        async with factory() as s:
            rid = await profiles.save_profile_revision(s, profile_id, content)
            await s.commit()
            return rid

    return _run(go())


def test_consumer_and_profile_idempotent(db):
    factory, created = db
    cid = _setup_consumer(factory, created)
    assert _setup_consumer(factory, created) == cid  # mismo consumer

    async def two_upserts():
        async with factory() as s:
            p1 = await profiles.upsert_profile(s, cid, "user-1")
            p2 = await profiles.upsert_profile(s, cid, "user-1")
            await s.commit()
            return p1, p2

    p1, p2 = _run(two_upserts())
    assert p1 == p2  # UNIQUE(consumer, external_ref)


def test_revision_idempotent_and_latest_wins(db):
    factory, created = db
    cid = _setup_consumer(factory, created)

    async def mk():
        async with factory() as s:
            pid = await profiles.upsert_profile(s, cid, "user-1")
            await s.commit()
            return pid

    pid = _run(mk())
    r1 = _save(factory, pid, CONTENT_1)
    r1b = _save(factory, pid, dict(CONTENT_1))  # mismo contenido
    assert r1 == r1b  # idempotente: misma revisión, sin duplicar

    r2 = _save(factory, pid, {**CONTENT_1, "cv_text": "12 años Python"})
    assert r2 != r1

    async def latest():
        async with factory() as s:
            return await profiles.current_revision(s, pid)

    row = _run(latest())
    assert row.id == r2  # vigente = la última ACTIVACIÓN
    assert row.content["cv_text"] == "12 años Python"

    assert _save(factory, pid, {"locations": ["Berna"]}) is None  # sin texto


def test_reversion_reactivates_historic_revision(db):
    """Rev. A-07 #1 (repro): A→B→A — el tercer guardado reutiliza la revisión
    INMUTABLE A y la RE-ACTIVA: vigente = A (con 'vigente = última por
    created_at' quedaba B)."""
    factory, created = db
    cid = _setup_consumer(factory, created)

    async def mk():
        async with factory() as s:
            pid = await profiles.upsert_profile(s, cid, "user-1")
            await s.commit()
            return pid

    pid = _run(mk())
    ra = _save(factory, pid, CONTENT_1)  # A
    _save(factory, pid, CONTENT_2)  # B
    ra2 = _save(factory, pid, dict(CONTENT_1))  # reversión a A
    assert ra2 == ra  # revisión reutilizada (inmutable, sin duplicar)

    async def cur():
        async with factory() as s:
            return await profiles.current_revision(s, pid)

    row = _run(cur())
    assert row.id == ra  # VIGENTE = A (re-activada)
    acts = _run_rows(
        factory,
        "SELECT seq, revision_id FROM profile_revision_activations "
        "WHERE profile_id = :p ORDER BY seq", p=pid,
    )
    assert len(acts) == 3  # historial append-only completo: A, B, A


def test_two_saves_in_one_transaction_second_wins(db):
    """Rev. A-07 #1 (repro): dos guardados en la MISMA transacción comparten
    created_at (now() constante) — la ACTIVACIÓN monotónica hace vigente al
    SEGUNDO sin depender de timestamps ni UUIDs."""
    factory, created = db
    cid = _setup_consumer(factory, created)

    async def both():
        async with factory() as s:
            pid = await profiles.upsert_profile(s, cid, "user-1")
            await profiles.save_profile_revision(
                s, pid, {**CONTENT_1, "title": "PRIMERA"}
            )
            r2 = await profiles.save_profile_revision(
                s, pid, {**CONTENT_1, "title": "SEGUNDA"}
            )
            await s.commit()
            return pid, r2

    pid, r2 = _run(both())

    async def cur():
        async with factory() as s:
            return await profiles.current_revision(s, pid)

    row = _run(cur())
    assert row.id == r2
    assert row.content["title"] == "SEGUNDA"


def test_salary_change_copies_vector_without_reencoding(db):
    """Rev. A-07 #2 (repro): cambiar solo salary_min crea otra revisión con el
    MISMO text_hash — el vector se COPIA bajo la revisión vigente sin llamar
    al encoder otra vez."""
    from jobhunt_core.tasks.embedding import run_pending_task

    factory, created = db
    cid = _setup_consumer(factory, created)

    async def mk():
        async with factory() as s:
            pid = await profiles.upsert_profile(s, cid, "user-1")
            await s.commit()
            return pid

    pid = _run(mk())
    _save(factory, pid, {**CONTENT_1, "salary_min": 80000})
    _register(factory, created)

    calls: list[list[str]] = []

    class Fake:
        def encode_batch(self, texts):
            calls.append(list(texts))
            return [[0.7] * embeddings.EMBED_DIM for _ in texts]

    embeddings.set_backend_factory(lambda name, version: Fake())
    try:
        r1 = run_pending_task.apply(kwargs={"limit": 50})
        assert r1.result["profiles_embedded"][f"modelo-test/{SHA_A}"] == 1
        assert len(calls) == 1  # un encode

        _save(factory, pid, {**CONTENT_1, "salary_min": 95000})  # mismo texto
        r2 = run_pending_task.apply(kwargs={"limit": 50})
        assert r2.result["profiles_embedded"][f"modelo-test/{SHA_A}"] == 1  # copiada
        assert len(calls) == 1  # el encoder NO se volvió a llamar
    finally:
        embeddings.set_backend_factory(None)


def test_batch_dedupes_same_text_across_profiles(db):
    """Rev. A-07 #2: dos perfiles con el MISMO texto embebible en un lote →
    UN encode de UN texto; el vector se distribuye a ambas revisiones."""
    from jobhunt_core.tasks.embedding import run_pending_task

    factory, created = db
    cid = _setup_consumer(factory, created)

    async def mk():
        async with factory() as s:
            p1 = await profiles.upsert_profile(s, cid, "user-1")
            p2 = await profiles.upsert_profile(s, cid, "user-2")
            await s.commit()
            return p1, p2

    p1, p2 = _run(mk())
    _save(factory, p1, dict(CONTENT_1))
    _save(factory, p2, {**CONTENT_1, "salary_min": 70000})  # mismo TEXTO
    mid = _register(factory, created)

    calls: list[list[str]] = []

    class Fake:
        def encode_batch(self, texts):
            calls.append(list(texts))
            return [[0.8] * embeddings.EMBED_DIM for _ in texts]

    embeddings.set_backend_factory(lambda name, version: Fake())
    try:
        r = run_pending_task.apply(kwargs={"limit": 50})
        assert r.result["profiles_embedded"][f"modelo-test/{SHA_A}"] == 2
    finally:
        embeddings.set_backend_factory(None)
    assert calls == [[profiles.build_profile_text(profiles.normalize_profile(CONTENT_1))]]
    rows = _run_rows(
        factory,
        "SELECT count(*) AS n FROM profile_embeddings WHERE model_id = :m", m=mid,
    )
    assert rows[0].n == 2


def test_store_discards_vector_of_superseded_revision(db):
    """Rev. A-07 #3 (repro): snapshot R1 → se guarda R2 → store del vector de
    R1 → DESCARTADO (revalidación de vigencia bajo el lock por perfil):
    jamás se persiste el vector de una revisión sustituida."""
    factory, created = db
    cid = _setup_consumer(factory, created)

    async def mk():
        async with factory() as s:
            pid = await profiles.upsert_profile(s, cid, "user-1")
            await s.commit()
            return pid

    pid = _run(mk())
    r1 = _save(factory, pid, CONTENT_1)
    mid = _register(factory, created)
    _save(factory, pid, CONTENT_2)  # R2 sustituye a R1 ANTES del store

    async def store_r1():
        async with factory() as s:
            n = await embeddings.store_profile_embeddings(
                s, mid,
                [{"revision_id": r1, "profile_id": pid,
                  "vector": [0.9] * embeddings.EMBED_DIM}],
            )
            await s.commit()
            return n

    assert _run(store_r1()) == 0  # descartado: R1 ya no es la vigente
    rows = _run_rows(
        factory,
        "SELECT count(*) AS n FROM profile_embeddings WHERE profile_revision_id = :r",
        r=r1,
    )
    assert rows[0].n == 0


def test_two_profiles_embedded_by_model_end_to_end(db):
    """DoD A-07: 2 perfiles embebidos por model_id con la MISMA resolución de
    backend por (name, version) que las ofertas; re-run → 0 (idempotente)."""
    from jobhunt_core.tasks.embedding import run_pending_task

    factory, created = db
    cid = _setup_consumer(factory, created)

    async def mk_profiles():
        async with factory() as s:
            p1 = await profiles.upsert_profile(s, cid, "user-1")
            p2 = await profiles.upsert_profile(s, cid, "user-2")
            await s.commit()
            return p1, p2

    p1, p2 = _run(mk_profiles())
    _save(factory, p1, CONTENT_1)
    _save(factory, p2, CONTENT_2)
    mid = _register(factory, created)

    seen: list[tuple[str, str]] = []

    class Fake:
        def encode_batch(self, texts):
            return [[0.3] * embeddings.EMBED_DIM for _ in texts]

    def factory_fn(name, version):
        seen.append((name, version))
        return Fake()

    embeddings.set_backend_factory(factory_fn)
    try:
        r1 = run_pending_task.apply(kwargs={"limit": 50})
        assert r1.successful()
        assert r1.result["profiles_embedded"][f"modelo-test/{SHA_A}"] == 2
        assert r1.result["embedded"][f"modelo-test/{SHA_A}"] == 0  # sin ofertas
        r2 = run_pending_task.apply(kwargs={"limit": 50})
        assert r2.result["profiles_embedded"][f"modelo-test/{SHA_A}"] == 0
    finally:
        embeddings.set_backend_factory(None)
    assert seen == [("modelo-test", SHA_A)]  # misma resolución que ofertas

    rows = _run_rows(
        factory,
        "SELECT count(*) AS n FROM profile_embeddings WHERE model_id = :m", m=mid,
    )
    assert rows[0].n == 2


def test_new_revision_embeds_only_latest(db):
    from jobhunt_core.tasks.embedding import run_pending_task

    factory, created = db
    cid = _setup_consumer(factory, created)

    async def mk():
        async with factory() as s:
            pid = await profiles.upsert_profile(s, cid, "user-1")
            await s.commit()
            return pid

    pid = _run(mk())
    _save(factory, pid, CONTENT_1)
    r2 = _save(factory, pid, {**CONTENT_1, "title": "Senior Backend Dev"})
    mid = _register(factory, created)

    embeddings.set_backend_factory(
        lambda name, version: type(
            "F", (), {"encode_batch": lambda self, ts: [[0.5] * 384 for _ in ts]}
        )()
    )
    try:
        r = run_pending_task.apply(kwargs={"limit": 50})
        assert r.result["profiles_embedded"][f"modelo-test/{SHA_A}"] == 1
    finally:
        embeddings.set_backend_factory(None)
    rows = _run_rows(
        factory,
        "SELECT profile_revision_id FROM profile_embeddings WHERE model_id = :m", m=mid,
    )
    assert [r.profile_revision_id for r in rows] == [r2]  # SOLO la vigente


def test_composite_fk_rejects_cross_profile_embedding(db):
    """Contrato §1: la FK COMPUESTA (revision, profile) impide colgar la
    revisión de un perfil bajo OTRO perfil."""
    factory, created = db
    cid = _setup_consumer(factory, created)

    async def mk():
        async with factory() as s:
            p1 = await profiles.upsert_profile(s, cid, "user-1")
            p2 = await profiles.upsert_profile(s, cid, "user-2")
            await s.commit()
            return p1, p2

    p1, p2 = _run(mk())
    r1 = _save(factory, p1, CONTENT_1)
    mid = _register(factory, created)

    async def cross():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO profile_embeddings "
                    "(profile_revision_id, profile_id, model_id, vector) "
                    "VALUES (:rid, :pid, :mid, CAST(:v AS vector))"
                ),
                {
                    "rid": r1, "pid": p2, "mid": mid,  # revisión de p1 bajo p2
                    "v": "[" + ",".join(["0.1"] * 384) + "]",
                },
            )
            await s.commit()

    with pytest.raises(IntegrityError):
        _run(cross())


def test_dim_guard_skips_profiles_for_rogue_model(db):
    """Auditoría A-07 #2: modelo activo dim=512 colado por otra vía + PERFIL
    pendiente → el guard de dimensión salta también la familia de perfiles:
    cero filas del rogue y su clave AUSENTE de profiles_embedded."""
    from jobhunt_core.tasks.embedding import run_pending_task

    factory, created = db
    cid = _setup_consumer(factory, created)

    async def mk():
        async with factory() as s:
            pid = await profiles.upsert_profile(s, cid, "user-1")
            await s.commit()
            return pid

    pid = _run(mk())
    _save(factory, pid, CONTENT_1)
    _register(factory, created)  # modelo 384 legítimo
    rogue = uuid.uuid4()

    async def insert_rogue():
        async with factory() as s:
            created["models"].append(rogue)
            await s.execute(
                sa.text(
                    "INSERT INTO embedding_models (id, name, version, dim, active) "
                    "VALUES (:id, 'rogue', :v, 512, TRUE)"
                ),
                {"id": rogue, "v": "e" * 40},
            )
            await s.commit()

    _run(insert_rogue())
    embeddings.set_backend_factory(
        lambda name, version: type(
            "F", (), {"encode_batch": lambda self, ts: [[0.6] * 384 for _ in ts]}
        )()
    )
    try:
        r = run_pending_task.apply(kwargs={"limit": 50})
        assert r.successful()
        assert r.result["profiles_embedded"] == {f"modelo-test/{SHA_A}": 1}  # sin rogue
    finally:
        embeddings.set_backend_factory(None)
    rows = _run_rows(
        factory,
        "SELECT count(*) AS n FROM profile_embeddings WHERE model_id = :m", m=rogue,
    )
    assert rows[0].n == 0


def test_store_profile_embeddings_optimistic(db):
    factory, created = db
    cid = _setup_consumer(factory, created)

    async def mk():
        async with factory() as s:
            pid = await profiles.upsert_profile(s, cid, "user-1")
            await s.commit()
            return pid

    pid = _run(mk())
    rid = _save(factory, pid, CONTENT_1)
    mid = _register(factory, created)
    item = {"revision_id": rid, "profile_id": pid, "vector": [0.2] * embeddings.EMBED_DIM}

    async def store():
        async with factory() as s:
            n = await embeddings.store_profile_embeddings(s, mid, [item])
            await s.commit()
            return n

    async def race():
        return await asyncio.gather(store(), store())

    results = _run(race())
    assert sorted(results) in ([0, 1], [1, 1])  # carrera: jamás error ni duplicado
    rows = _run_rows(
        factory,
        "SELECT count(*) AS n FROM profile_embeddings "
        "WHERE profile_revision_id = :r AND model_id = :m", r=rid, m=mid,
    )
    assert rows[0].n == 1
    assert _run(store()) == 0  # re-store: pre-filtro


def _run_rows(factory, sql, **params):
    async def go():
        async with factory() as s:
            return (await s.execute(sa.text(sql), params)).all()

    return asyncio.run(go())


def test_core0004_backfills_preexisting_revisions():
    """Rev. A-07 2ª #1 + 3ª #1: revisiones creadas ANTES de core0004 quedaban
    sin activación → sin vigente y fuera del worker. Test REAL de migración
    sobre una BD DESECHABLE (jamás la compartida de la suite — un downgrade
    allí destruiría reactivaciones históricas ajenas): crear desde vacío →
    core0003 → seed pre-migración → head → verificar vigente y pendiente →
    destruir."""
    import os
    import subprocess
    from urllib.parse import urlsplit, urlunsplit

    # La URL admin es síncrona (psycopg2): normalizar a asyncpg para el test.
    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    dbname = f"jobhunt_migtest_{uuid.uuid4().hex[:12]}"
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
        temp_engine = create_async_engine(temp_url, poolclass=sa.pool.NullPool)
        cid, pid = uuid.uuid4(), uuid.uuid4()
        r1, r2 = uuid.uuid4(), uuid.uuid4()

        async def bootstrap_and_seed_after_core0003():
            async with temp_engine.begin() as c:
                # Bootstrap mínimo (lo hace migrate.py en entornos reales).
                await c.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
                await c.execute(
                    sa.text(f'CREATE SCHEMA IF NOT EXISTS "{settings.CORE_DB_SCHEMA}"')
                )

        asyncio.run(bootstrap_and_seed_after_core0003())
        env = {**os.environ, "CORE_DATABASE_URL": temp_url}
        subprocess.run(
            ["alembic", "-c", "jobhunt_core/alembic.ini", "upgrade", "core0003"],
            check=True, capture_output=True, env=env,
        )

        async def seed():
            async with temp_engine.begin() as c:
                await c.execute(
                    sa.text(f'SET search_path = "{settings.CORE_DB_SCHEMA}", public')
                )
                await c.execute(
                    sa.text("INSERT INTO consumers (id, name) VALUES (:id, 'mig')"),
                    {"id": cid},
                )
                await c.execute(
                    sa.text(
                        "INSERT INTO profiles (id, consumer_id, external_ref) "
                        "VALUES (:id, :cid, 'user-pre')"
                    ),
                    {"id": pid, "cid": cid},
                )
                # Dos revisiones con la ordenación ANTIGUA bien definida.
                for rid, chash, ts in (
                    (r1, "1" * 64, "now() - interval '2 hours'"),
                    (r2, "2" * 64, "now() - interval '1 hour'"),
                ):
                    await c.execute(
                        sa.text(
                            "INSERT INTO profile_revisions "
                            "(id, profile_id, content, content_hash, text_hash, created_at) "
                            f"VALUES (:id, :pid, '{{\"title\": \"pre\"}}'::jsonb, "
                            f":ch, :th, {ts})"
                        ),
                        {"id": rid, "pid": pid, "ch": chash, "th": "t" * 64},
                    )

        asyncio.run(seed())
        subprocess.run(
            ["alembic", "-c", "jobhunt_core/alembic.ini", "upgrade", "head"],
            check=True, capture_output=True, env=env,
        )

        async def verify():
            async with temp_engine.connect() as c:
                await c.execute(
                    sa.text(f'SET search_path = "{settings.CORE_DB_SCHEMA}", public')
                )
                acts = (
                    await c.execute(
                        sa.text(
                            "SELECT seq, revision_id FROM profile_revision_activations "
                            "WHERE profile_id = :p ORDER BY seq"
                        ),
                        {"p": pid},
                    )
                ).all()
                assert [(a.seq, a.revision_id) for a in acts] == [(1, r1), (2, r2)]
                # Pendiente: la MISMA query del worker (vigente sin vector).
                pend = (
                    await c.execute(
                        sa.text(
                            "SELECT pr.id FROM (SELECT DISTINCT ON (profile_id) "
                            "profile_id, revision_id FROM profile_revision_activations "
                            "ORDER BY profile_id, seq DESC) cur "
                            "JOIN profile_revisions pr ON pr.id = cur.revision_id "
                            "LEFT JOIN profile_embeddings pe "
                            "  ON pe.profile_revision_id = pr.id AND pe.model_id = :mid "
                            "WHERE pe.profile_revision_id IS NULL"
                        ),
                        {"mid": uuid.uuid4()},
                    )
                ).scalars().all()
                assert pend == [r2]  # vigente = la más reciente, de vuelta al worker

        asyncio.run(verify())
        asyncio.run(temp_engine.dispose())
    finally:

        async def drop_db():
            async with admin_engine.connect() as c:
                await c.execute(
                    sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
                )
            await admin_engine.dispose()

        asyncio.run(drop_db())
