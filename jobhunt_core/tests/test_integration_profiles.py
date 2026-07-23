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
            return await profiles.latest_revision(s, pid)

    row = _run(latest())
    assert row.id == r2  # vigente = la ÚLTIMA
    assert row.content["cv_text"] == "12 años Python"

    assert _save(factory, pid, {"locations": ["Berna"]}) is None  # sin texto


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
