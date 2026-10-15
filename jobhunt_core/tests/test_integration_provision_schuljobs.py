"""schuljobs' one scope declares its page budget, disabled and idempotent."""

import asyncio

import sqlalchemy as sa

from jobhunt_core.provision_schuljobs import SCOPE_PARAMS, provision
from jobhunt_core.tests.test_integration_harvest import db, pytestmark  # noqa: F401


def test_provision_schuljobs_scope_declares_its_budget(db):  # noqa: F811
    factory, made = db

    async def run():
        async with factory() as session:
            first = await provision(session)
            await session.commit()
            source_id = await session.scalar(
                sa.text("SELECT id FROM sources WHERE name='schuljobs'")
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
            # The budget is DECLARED, not implicit: widening the sweep is a
            # params decision with its own validation.
            assert row.params == {"max_pages": 4} and not row.enabled

            second = await provision(session, enabled=True)
            await session.commit()
            assert second == first
            assert await session.scalar(
                sa.text("SELECT enabled FROM harvest_scopes WHERE id=:id"),
                {"id": first[0]},
            )

    asyncio.run(run())
