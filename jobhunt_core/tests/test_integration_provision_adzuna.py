"""Adzuna's three exact country scopes are idempotent and opt-in."""

import asyncio

import sqlalchemy as sa

from jobhunt_core.provision_adzuna import SCOPE_PARAMS, provision
from jobhunt_core.tests.test_integration_harvest import db, pytestmark  # noqa: F401


def test_provision_adzuna_scopes_disabled_then_explicitly_enabled(db):  # noqa: F811
    factory, made = db

    async def run():
        async with factory() as session:
            first = await provision(session)
            await session.commit()
            source_id = await session.scalar(
                sa.text("SELECT id FROM sources WHERE name='adzuna'")
            )
            made["extra_sources"].append(source_id)
            made["scopes"].extend(first)
            actual = (
                await session.execute(
                    sa.text(
                        "SELECT id,params,enabled FROM harvest_scopes "
                        "WHERE source_id=:source_id ORDER BY params->>'country'"
                    ),
                    {"source_id": source_id},
                )
            ).all()
            assert len(first) == len(SCOPE_PARAMS) == len(actual) == 3
            assert {row.params["country"] for row in actual} == {"gb", "us", "de"}
            assert not any(row.enabled for row in actual)

            second = await provision(session, enabled=True)
            await session.commit()
            assert second == first
            assert (
                await session.scalar(
                    sa.text(
                        "SELECT count(*) FROM harvest_scopes "
                        "WHERE source_id=:source_id AND enabled"
                    ),
                    {"source_id": source_id},
                )
                == 3
            )

    asyncio.run(run())
