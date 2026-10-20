"""PostgreSQL proofs for the native matching recovery task."""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import embeddings, profiles
from jobhunt_core.config import settings
from jobhunt_core.tasks import matching as matching_tasks
from jobhunt_core.tests import dbcleanup
from jobhunt_core.tests.test_integration_matching import (
    DirectionalBackend,
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
