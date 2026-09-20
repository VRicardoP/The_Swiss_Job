"""Upgrade the published predecessor; refuse downgrade after authority transfer."""

import asyncio
import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from jobhunt_core.config import settings
from jobhunt_core.tests.alembic_runner import run_alembic

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"), reason="requires PG"
)


def test_core0048_upgrade_and_guarded_downgrade():
    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    name = "jobhunt_profile_snapshot_" + uuid.uuid4().hex[:12]
    parts = urlsplit(admin_url)
    url = urlunsplit((parts.scheme, parts.netloc, "/" + name, "", ""))
    admin = create_async_engine(
        admin_url, poolclass=sa.pool.NullPool, isolation_level="AUTOCOMMIT"
    )
    engine = create_async_engine(
        url,
        poolclass=sa.pool.NullPool,
        connect_args={
            "server_settings": {"search_path": f"{settings.CORE_DB_SCHEMA},public"}
        },
    )

    async def admin_sql(statement):
        async with admin.connect() as c:
            await c.execute(sa.text(statement))

    async def sql(statement):
        async with engine.begin() as c:
            result = await c.execute(sa.text(statement))
            return result.all() if result.returns_rows else None

    def execute(statement):
        return asyncio.run(sql(statement))

    asyncio.run(admin_sql(f'CREATE DATABASE "{name}"'))
    try:
        execute("CREATE EXTENSION vector")
        execute("CREATE EXTENSION pg_trgm")
        execute(f'CREATE SCHEMA "{settings.CORE_DB_SCHEMA}"')
        run_alembic(url, "upgrade", "core0048")
        execute("INSERT INTO consumers(name) VALUES ('snapshot-migration-test')")
        execute(
            "INSERT INTO profiles(consumer_id,external_ref) SELECT id,'owner' FROM consumers"
        )
        before = execute("SELECT id,consumer_id,external_ref FROM profiles")
        run_alembic(url, "upgrade", "core0049")
        assert execute("SELECT id,consumer_id,external_ref FROM profiles") == before
        assert execute(
            "SELECT projection_version,projection_hash,projection_active FROM profiles"
        ) == [(0, None, True)]
        run_alembic(url, "downgrade", "core0048")
        assert execute("SELECT id,consumer_id,external_ref FROM profiles") == before
        run_alembic(url, "upgrade", "core0049")
        execute(
            "UPDATE profiles SET projection_version=1,projection_hash=repeat('a',64),projection_active=false"
        )
        snapshot = execute("SELECT to_jsonb(p) FROM profiles p")
        failure = run_alembic(url, "downgrade", "core0048", check=False)
        assert (
            failure.returncode != 0
            and b"profile projection authority" in failure.stderr
        )
        assert execute("SELECT to_jsonb(p) FROM profiles p") == snapshot
        assert execute("SELECT version_num FROM alembic_version") == [("core0049",)]
    finally:
        asyncio.run(engine.dispose())
        asyncio.run(admin_sql(f'DROP DATABASE "{name}" WITH (FORCE)'))
        asyncio.run(admin.dispose())
