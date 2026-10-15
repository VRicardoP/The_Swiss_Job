"""Gastrojob's one scope is exact, idempotent and opt-in."""

import asyncio

import sqlalchemy as sa

from jobhunt_core.provision_gastrojob import SCOPE_PARAMS, provision
from jobhunt_core.tests.test_integration_harvest import db, pytestmark  # noqa: F401


def test_provision_gastrojob_scope_disabled_then_explicitly_enabled(db):  # noqa: F811
    factory, made = db

    async def run():
        async with factory() as session:
            first = await provision(session)
            await session.commit()
            source_id = await session.scalar(
                sa.text("SELECT id FROM sources WHERE name='gastrojob'")
            )
            made["extra_sources"].append(source_id)
            made["scopes"].extend(first)
            row = (
                await session.execute(
                    sa.text(
                        "SELECT id,params,enabled FROM harvest_scopes "
                        "WHERE source_id=:source_id"
                    ),
                    {"source_id": source_id},
                )
            ).one()
            assert len(first) == len(SCOPE_PARAMS) == 1
            assert row.id == first[0] and row.params == {} and not row.enabled

            second = await provision(session, enabled=True)
            await session.commit()
            assert second == first
            assert await session.scalar(
                sa.text("SELECT enabled FROM harvest_scopes WHERE id=:id"),
                {"id": first[0]},
            )

    asyncio.run(run())
