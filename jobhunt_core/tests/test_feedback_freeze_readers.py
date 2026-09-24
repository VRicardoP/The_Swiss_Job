"""Freezing source mutations must not block the BFF's shared auth row lock."""

import asyncio
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core.school_source import lock_source
from jobhunt_core.tests.test_integration_api import db, pytestmark  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
from jobhunt_core.tests.test_integration_document_source import create_test_schema


def test_source_freeze_allows_auth_read_but_blocks_owner_mutation(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, _ = db
    schema = "doc_source_" + uuid.uuid4().hex
    uid, pid = uuid.uuid4(), uuid.uuid4()
    meta = sa.MetaData(schema=schema)
    users = sa.Table("users", meta, sa.Column("id", sa.Uuid, primary_key=True))
    sa.Table(
        "jobhunt_profile_map",
        meta,
        sa.Column("user_id", sa.Uuid, sa.ForeignKey(users.c.id), primary_key=True),
        sa.Column("core_profile_id", sa.Uuid, nullable=False),
    )
    jobs = sa.Table("jobs", meta, sa.Column("hash", sa.Text, primary_key=True))
    sa.Table(
        "match_results",
        meta,
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey(users.c.id)),
        sa.Column("job_hash", sa.Text, sa.ForeignKey(jobs.c.hash)),
    )
    sa.Table(
        "user_profiles",
        meta,
        sa.Column("user_id", sa.Uuid, sa.ForeignKey(users.c.id), primary_key=True),
    )
    sa.Table(
        "jobhunt_routing",
        meta,
        sa.Column("consumer_id", sa.Text, primary_key=True),
        sa.Column("profile_id", sa.Uuid, primary_key=True),
        sa.Column("capability", sa.Text, primary_key=True),
        sa.Column("mode", sa.Text),
    )

    async def check():
        create_test_schema(schema)
        try:
            async with factory() as setup:
                await (await setup.connection()).run_sync(meta.create_all)
                await setup.execute(users.insert().values(id=uid))
                await setup.execute(
                    meta.tables[schema + ".jobhunt_profile_map"]
                    .insert()
                    .values(user_id=uid, core_profile_id=pid)
                )
                await setup.commit()
            async with factory() as migration, factory() as reader:
                await lock_source(
                    migration,
                    "swissjob",
                    {str(uid): str(pid)},
                    authority="local",
                    schema=schema,
                )
                await reader.execute(sa.text("SET LOCAL lock_timeout='200ms'"))
                assert (
                    await reader.scalar(
                        sa.select(users.c.id)
                        .where(users.c.id == uid)
                        .with_for_update(read=True)
                    )
                    == uid
                )
                await reader.rollback()
                for statement in (
                    users.update().where(users.c.id == uid).values(id=uid),
                    users.delete().where(users.c.id == uid),
                ):
                    await reader.execute(sa.text("SET LOCAL lock_timeout='200ms'"))
                    with pytest.raises(sa.exc.DBAPIError, match="lock timeout"):
                        await reader.execute(statement)
                    await reader.rollback()
        finally:
            async with factory() as cleanup:
                await cleanup.execute(sa.schema.DropSchema(schema, cascade=True))
                await cleanup.commit()

    asyncio.run(check())
