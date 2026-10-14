"""The Jobicy catalog is exact, idempotent and opt-in."""

import asyncio

import sqlalchemy as sa

from jobhunt_core.provision_jobicy import SCOPE_PARAMS, provision
from jobhunt_core.tests.test_integration_harvest import db, pytestmark  # noqa: F401


def test_provision_jobicy_scopes_disabled_then_explicitly_enabled(db):  # noqa: F811
    factory, made = db

    async def run():
        async with factory() as session:
            first = await provision(session)
            await session.commit()
            source_id = await session.scalar(
                sa.text("SELECT id FROM sources WHERE name='jobicy'")
            )
            made["extra_sources"].append(source_id)
            made["scopes"].extend(first)
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT id,params,enabled FROM harvest_scopes "
                        "WHERE source_id=:source_id ORDER BY id"
                    ),
                    {"source_id": source_id},
                )
            ).all()
            assert len(rows) == len(SCOPE_PARAMS) == 8
            assert {row.id for row in rows} == set(first)
            assert {tuple(sorted(row.params.items())) for row in rows} == {
                tuple(sorted(params.items())) for params in SCOPE_PARAMS
            }
            assert not any(row.enabled for row in rows)

            second = await provision(session, enabled=True)
            await session.commit()
            assert second == first
            assert await session.scalar(
                sa.text(
                    "SELECT count(*) FROM harvest_scopes "
                    "WHERE source_id=:source_id AND enabled"
                ),
                {"source_id": source_id},
            ) == len(SCOPE_PARAMS)

    asyncio.run(run())
