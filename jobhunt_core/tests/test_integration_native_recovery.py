"""PostgreSQL proofs for the native matching recovery task."""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import embeddings, profiles
from jobhunt_core.config import settings
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.tasks import matching as matching_tasks
from jobhunt_core.tests import dbcleanup
from jobhunt_core.tests.test_integration_matching import (
    DirectionalBackend,
    _listing,
    _setup,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requires the core-migrate database",
)


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {
        "sources": [],
        "scopes": [],
        "models": [],
        "consumers": [],
        "policies": [],
    }
    yield factory, created

    async def cleanup():
        async with factory() as session:
            await dbcleanup.purge_consumer_graph(session, created["consumers"])
            await dbcleanup.purge_source_graph(
                session, created["sources"], created["scopes"]
            )
            await dbcleanup.purge_policies(session, created["policies"])
            for model_id in created["models"]:
                await dbcleanup.purge_model(session, model_id)
            await session.commit()
        await engine.dispose()

    asyncio.run(cleanup())


async def _targets(factory):
    async with factory() as session:
        return await matching_tasks._recovery_targets(session)


def test_revision_corpus_generation_and_attempt_watermark(db):
    factory, created = db
    profile_id, _model_id, _policy_id, vacancies = _setup(
        factory, created, ["backend python", "data engineering"]
    )
    assert asyncio.run(_targets(factory)) == [profile_id]

    asyncio.run(matching_tasks._evaluate_and_record(factory, profile_id))
    assert asyncio.run(_targets(factory)) == []

    async def change_revision_without_vector():
        async with factory() as session:
            await profiles.save_profile_revision(
                session, profile_id, {"title": "python v2", "skills": ["python"]}
            )
            await session.commit()

    asyncio.run(change_revision_without_vector())
    assert asyncio.run(_targets(factory)) == []

    from jobhunt_core.tasks.embedding import run_pending_task

    embeddings.set_backend_factory(lambda _name, _version: DirectionalBackend())
    try:
        assert run_pending_task.apply(kwargs={"limit": 100}).successful()
    finally:
        embeddings.set_backend_factory(None)
    assert asyncio.run(_targets(factory)) == [profile_id]
    asyncio.run(matching_tasks._evaluate_and_record(factory, profile_id))
    assert asyncio.run(_targets(factory)) == []

    async def change_corpus_generation():
        async with factory() as session:
            vacancy_id = next(iter(vacancies.values()))
            await session.execute(
                sa.text("UPDATE vacancies SET archived_at=now() WHERE id=:id"),
                {"id": vacancy_id},
            )
            await session.commit()

    asyncio.run(change_corpus_generation())
    # A corpus change alone waits for the cooldown (one harvest must not
    # re-evaluate every profile); once it elapses the profile is due again.
    assert asyncio.run(_targets(factory)) == []

    async def age_last_attempt(seconds):
        async with factory() as session:
            await session.execute(
                sa.text(
                    "UPDATE profile_recovery_state SET attempted_at = now() - "
                    "make_interval(secs => :s) WHERE profile_id = :pid"
                ),
                {"s": seconds, "pid": profile_id},
            )
            await session.commit()

    asyncio.run(age_last_attempt(matching_tasks.RECOVERY_CORPUS_COOLDOWN_S - 60))
    assert asyncio.run(_targets(factory)) == []
    asyncio.run(age_last_attempt(matching_tasks.RECOVERY_CORPUS_COOLDOWN_S + 60))
    assert asyncio.run(_targets(factory)) == [profile_id]


def test_all_consumers_share_deterministic_fair_recovery(db):
    factory, created = db
    first, _model_id, _policy_id, _vacancies = _setup(
        factory, created, ["backend python"]
    )

    async def add_other_consumer():
        async with factory() as session:
            consumer_id = await profiles.ensure_consumer(session, "another-tenant")
            created["consumers"].append(consumer_id)
            profile_id = await profiles.upsert_profile(
                session, consumer_id, f"user-{uuid.uuid4()}"
            )
            await profiles.save_profile_revision(
                session, profile_id, {"title": "data dev", "skills": ["sql"]}
            )
            await session.commit()
            return profile_id

    second = asyncio.run(add_other_consumer())
    from jobhunt_core.tasks.embedding import run_pending_task

    embeddings.set_backend_factory(lambda _name, _version: DirectionalBackend())
    try:
        assert run_pending_task.apply(kwargs={"limit": 100}).successful()
    finally:
        embeddings.set_backend_factory(None)

    expected_first = min((first, second), key=str)
    assert asyncio.run(_targets(factory)) == [expected_first]
    asyncio.run(matching_tasks._evaluate_and_record(factory, expected_first))
    assert asyncio.run(_targets(factory)) == [
        second if expected_first == first else first
    ]


# Tarea 1.3 (R4): el anti-join de ofertas pendientes no se repite mientras la
# generación del corpus siga siendo la misma en la que devolvió cero filas.


@pytest.fixture()
def offer_queries(monkeypatch):
    """Cuenta las ejecuciones REALES de `pending_offer_texts` por modelo."""
    calls = []
    real = embeddings.pending_offer_texts

    async def counting(session, model_id, limit=200):
        calls.append(model_id)
        return await real(session, model_id, limit=limit)

    monkeypatch.setattr(embeddings, "pending_offer_texts", counting)
    return calls


def _drain(factory):
    embeddings.set_backend_factory(lambda _name, _version: DirectionalBackend())
    try:
        asyncio.run(matching_tasks._drain_embeddings(factory))
    finally:
        embeddings.set_backend_factory(None)


def _bump_generation(factory):
    async def go():
        async with factory() as session:
            await session.execute(
                sa.text(
                    "UPDATE corpus_generation SET generation = generation + 1 "
                    "WHERE id = 1"
                )
            )
            await session.commit()

    asyncio.run(go())


def test_drain_skips_offer_query_while_generation_is_unchanged(db, offer_queries):
    factory, created = db
    _pid, model_id, _policy_id, _vacancies = _setup(
        factory, created, ["backend python"]
    )
    offer_queries.clear()

    _drain(factory)  # la generación cambió al guardar los vectores: consulta
    _drain(factory)  # nada cambió desde la respuesta vacía: no consulta

    assert offer_queries.count(model_id) == 1


def test_drain_queries_offers_again_after_generation_bump(db, offer_queries):
    factory, created = db
    _pid, model_id, _policy_id, _vacancies = _setup(
        factory, created, ["backend python"]
    )
    _drain(factory)
    offer_queries.clear()

    _bump_generation(factory)
    _drain(factory)

    assert offer_queries.count(model_id) == 1


def test_new_offer_after_generation_bump_gets_embedded(db, offer_queries):
    factory, created = db
    _pid, model_id, _policy_id, _vacancies = _setup(
        factory, created, ["backend python"]
    )
    _drain(factory)
    _drain(factory)  # marca puesta: la siguiente consulta depende de la generación

    async def harvest_new_offer():
        # El alta de la vacante sube la generación por su trigger (core0022).
        async with factory() as session:
            await RawListingSink().handle(
                session,
                str(created["scopes"][0]),
                (_listing("new-1", "kubernetes engineer"),),
            )
            await session.commit()

    async def vectors_missing():
        async with factory() as session:
            return (
                await session.execute(
                    sa.text(
                        "SELECT count(*) FROM vacancies v "
                        "JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
                        "JOIN source_listing_incarnations i ON i.vacancy_id = v.id "
                        "JOIN source_listings l ON l.id = i.source_listing_id "
                        "LEFT JOIN offer_embeddings e "
                        "  ON e.text_hash = o.text_hash AND e.model_id = :m "
                        "WHERE l.source_id = :src AND e.text_hash IS NULL"
                    ),
                    {"m": model_id, "src": created["sources"][0]},
                )
            ).scalar_one()

    asyncio.run(harvest_new_offer())
    assert asyncio.run(vectors_missing()) == 1

    _drain(factory)

    assert asyncio.run(vectors_missing()) == 0
