"""The five watched boards are provisioned together, disabled, idempotently."""

import asyncio

import sqlalchemy as sa

from jobhunt_core.harvest.providers.native_swiss_schools import BOARDS
from jobhunt_core.provision_swiss_schools import provision
from jobhunt_core.tests.test_integration_harvest import db, pytestmark  # noqa: F401


def test_provision_every_board_disabled_then_explicitly_enabled(db):  # noqa: F811
    factory, made = db

    async def run():
        async with factory() as session:
            first = await provision(session)
            await session.commit()
            assert len(first) == len(BOARDS) == 5
            for board in BOARDS:
                source_id = await session.scalar(
                    sa.text("SELECT id FROM sources WHERE name=:name"),
                    {"name": board.source},
                )
                made["extra_sources"].append(source_id)
                row = (
                    await session.execute(
                        sa.text(
                            "SELECT id, params, enabled FROM harvest_scopes "
                            "WHERE source_id=:source_id"
                        ),
                        {"source_id": source_id},
                    )
                ).one()
                assert row.params == {} and not row.enabled
            made["scopes"].extend(first)

            second = await provision(session, enabled=True)
            await session.commit()
            assert second == first
            enabled = await session.scalar(
                sa.text(
                    "SELECT count(*) FROM harvest_scopes WHERE id = ANY(:ids) AND enabled"
                ),
                {"ids": first},
            )
            assert enabled == len(BOARDS)

    asyncio.run(run())
