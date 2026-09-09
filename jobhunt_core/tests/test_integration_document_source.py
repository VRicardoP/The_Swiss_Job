"""Exercise real source constraints, freeze drift and whole-collection reverse sync."""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from jobhunt_core.config import settings
from jobhunt_core import documents
from jobhunt_core.document_source import (
    lock_source,
    reverse_sync,
    source_rows,
    verify_source,
)
from jobhunt_core.import_documents import (
    DocumentMigrationError,
    digest,
    import_batch,
    prepare_batch,
)
from jobhunt_core.tests.test_integration_api_saved_searches import db, _seed_profile
from jobhunt_core.tests.test_integration_import_documents import source


def create_test_schema(schema):
    database = sa.engine.make_url(settings.CORE_DATABASE_URL).database
    assert database.startswith("jobhunt_suite_") and schema.startswith("doc_source_")
    admin = sa.create_engine(
        sa.engine.make_url(os.environ["CORE_ADMIN_DATABASE_URL"]).set(
            database=database
        ),
        poolclass=sa.pool.NullPool,
    )
    try:
        with admin.begin() as connection:
            connection.execute(
                sa.text(f'CREATE SCHEMA "{schema}" AUTHORIZATION jobhunt_core')
            )
    finally:
        admin.dispose()


def source_tables(schema, origin, *, constraint=True):
    meta = sa.MetaData(schema=schema)
    owner_type = UUID(as_uuid=True) if origin == "swissjob" else sa.Integer
    users = sa.Table("users", meta, sa.Column("id", owner_type, primary_key=True))
    owner = sa.Column(
        "user_id",
        owner_type,
        *([sa.ForeignKey(users.c.id)] if constraint else []),
        nullable=False,
    )
    columns = [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        owner,
        sa.Column("doc_type", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("language", sa.String(5)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]
    if origin == "swissjob":
        columns += [
            sa.Column("job_hash", sa.String(32), nullable=False),
            sa.Column("job_title", sa.String(500)),
            sa.Column("job_company", sa.String(300)),
        ]
    else:
        columns += [
            sa.Column("application_id", UUID(as_uuid=True), nullable=False),
            sa.Column("application_snapshot", JSONB, nullable=False),
            sa.Column("model_used", sa.String(100), nullable=False),
            sa.Column("generation_time_ms", sa.Integer),
        ]
    if origin == "swissjob":
        sa.Table(
            "jobhunt_profile_map",
            meta,
            sa.Column(
                "user_id", owner_type, sa.ForeignKey(users.c.id), primary_key=True
            ),
            sa.Column(
                "core_profile_id", UUID(as_uuid=True), nullable=False, unique=True
            ),
        )
    sa.Table(
        "jobhunt_routing",
        meta,
        sa.Column("consumer_id", sa.String(64), primary_key=True),
        sa.Column("profile_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("capability", sa.String(32), primary_key=True),
        sa.Column("mode", sa.String(20), nullable=False),
    )
    docs = sa.Table("generated_documents", meta, *columns)
    journal = sa.Table(
        "document_deliveries",
        meta,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", owner_type),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
    )
    return meta, users, docs, journal


@pytest.mark.parametrize("origin", ["swissjob", "portfolio"])
def test_source_drift_pending_and_reverse_whole_collection(db, origin):
    factory, created = db
    _, consumer, pid = _seed_profile(factory, created)
    owner, foreign = (uuid.uuid4(), uuid.uuid4()) if origin == "swissjob" else (11, 12)
    bindings = {str(owner): pid}
    schema = "doc_source_" + uuid.uuid4().hex
    meta, users, table, journal = source_tables(schema, origin)
    old, other = source(origin, owner), source(origin, foreign)

    async def run():
        async with factory() as s:
            create_test_schema(schema)
            await (await s.connection()).run_sync(meta.create_all)
            await s.execute(users.insert(), [{"id": owner}, {"id": foreign}])
            if origin == "swissjob":
                await s.execute(
                    meta.tables[schema + ".jobhunt_profile_map"].insert(),
                    {"user_id": owner, "core_profile_id": pid},
                )
            await s.execute(table.insert(), [old, other])
            await s.commit()
            try:
                _, owners = await lock_source(
                    s, origin=origin, bindings=bindings, schema=schema
                )
                batch = prepare_batch(
                    batch_id=uuid.uuid4(),
                    origin=origin,
                    consumer=consumer,
                    bindings=bindings,
                    rows=await source_rows(s, table, owners),
                )
                await s.rollback()
                await s.execute(
                    table.update()
                    .where(table.c.id == old["id"])
                    .values(content="drift")
                )
                await s.commit()
                with pytest.raises(DocumentMigrationError, match="changed"):
                    await verify_source(s, batch, schema=schema)
                await s.rollback()
                await s.execute(
                    table.update()
                    .where(table.c.id == old["id"])
                    .values(content=old["content"])
                )
                await s.execute(journal.insert().values(id=1, user_id=owner))
                await s.commit()
                with pytest.raises(DocumentMigrationError, match="pending"):
                    await verify_source(s, batch, schema=schema)
                await s.rollback()
                await s.execute(journal.delete())
                await s.commit()
                await verify_source(s, batch, schema=schema)
                await import_batch(s, batch)
                await s.commit()
                routing = meta.tables[schema + ".jobhunt_routing"]
                await s.execute(
                    routing.insert().values(
                        consumer_id=origin,
                        profile_id=owner if origin == "swissjob" else pid,
                        capability="documents",
                        mode="core_primary",
                    )
                )
                await s.commit()
                with pytest.raises(DocumentMigrationError, match="direction"):
                    await verify_source(s, batch, schema=schema)
                await s.rollback()
                # New authoritative write plus deletion after cutover.
                await documents.delete(s, pid, old["id"], consumer)
                values = dict(batch["documents"][0]["target"])
                for key in ("id", "profile_id", "created_at", "output_hash"):
                    values.pop(key)
                values["context"].pop("_migration")
                values["content"] = "new document after cutover"
                new_id = await documents.create(s, pid, values, consumer)
                await s.commit()
                current = (
                    (
                        await s.execute(
                            sa.text(
                                "SELECT * FROM generated_documents WHERE profile_id=:pid"
                            ),
                            {"pid": pid},
                        )
                    )
                    .mappings()
                    .all()
                )
                result = await reverse_sync(
                    s, current, origin=origin, bindings=bindings, schema=schema
                )
                await s.commit()
                assert result["documents"] == 1 and result["removed_local"] == 1
                local = await source_rows(s, table, [owner])
                assert (
                    local[0]["id"] == new_id
                    and local[0]["content"] == values["content"]
                )
                assert digest(await source_rows(s, table, [foreign])) == digest([other])
                replay = await reverse_sync(
                    s, current, origin=origin, bindings=bindings, schema=schema
                )
                assert (
                    replay["sha256"] == result["sha256"]
                    and replay["removed_local"] == 0
                )
                await s.commit()
                # UUID collision with another owner must not erase the current collection.
                altered = [dict(current[0], id=other["id"])]
                with pytest.raises(
                    DocumentMigrationError, match="another source owner"
                ):
                    await reverse_sync(
                        s, altered, origin=origin, bindings=bindings, schema=schema
                    )
                await s.commit()
                assert digest(await source_rows(s, table, [owner])) == digest(local)
                assert digest(await source_rows(s, table, [foreign])) == digest([other])
            finally:
                await s.rollback()
                await s.execute(sa.schema.DropSchema(schema, cascade=True))
                await s.commit()

    asyncio.run(run())


def test_empty_restore_without_constraints_is_rejected(db):
    factory, _ = db
    schema = "doc_source_" + uuid.uuid4().hex
    meta, users, _, _ = source_tables(schema, "portfolio", constraint=False)

    async def run():
        async with factory() as s:
            create_test_schema(schema)
            await (await s.connection()).run_sync(meta.create_all)
            await s.execute(users.insert().values(id=11))
            await s.commit()
            try:
                with pytest.raises(DocumentMigrationError, match="constraints"):
                    await lock_source(
                        s,
                        origin="portfolio",
                        bindings={"11": uuid.uuid4()},
                        schema=schema,
                    )
            finally:
                await s.rollback()
                await s.execute(sa.schema.DropSchema(schema, cascade=True))
                await s.commit()

    asyncio.run(run())
