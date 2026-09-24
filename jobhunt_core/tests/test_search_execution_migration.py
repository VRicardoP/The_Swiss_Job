"""Incremental upgrade preserves searches; downgrade cannot erase execution state."""

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


def test_core0050_incremental_upgrade_and_guarded_downgrade():
    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    name = "jobhunt_search_execution_" + uuid.uuid4().hex[:12]
    parts = urlsplit(admin_url)
    url = urlunsplit((parts.scheme, parts.netloc, "/" + name, "", ""))
    admin = create_async_engine(
        admin_url, poolclass=sa.pool.NullPool, isolation_level="AUTOCOMMIT"
    )
    engine = create_async_engine(
        url,
        poolclass=sa.pool.NullPool,
        connect_args={
            "server_settings": {"search_path": f"{settings.CORE_DB_SCHEMA},public"},
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
        run_alembic(url, "upgrade", "core0049")
        execute("INSERT INTO consumers(name) VALUES ('search-migration-test')")
        execute(
            "INSERT INTO profiles(consumer_id,external_ref) SELECT id,'owner' FROM consumers"
        )
        execute(
            "INSERT INTO saved_searches(profile_id,name) SELECT id,'existing' FROM profiles"
        )
        before = execute("SELECT to_jsonb(s) FROM saved_searches s")
        run_alembic(url, "upgrade", "core0050")
        assert execute("SELECT to_jsonb(s) FROM saved_searches s") == before
        assert execute("SELECT count(*) FROM saved_search_execution") == [(0,)]
        run_alembic(url, "downgrade", "core0049")
        assert execute("SELECT to_jsonb(s) FROM saved_searches s") == before
        run_alembic(url, "upgrade", "core0050")
        execute(
            "INSERT INTO saved_search_execution(saved_search_id,contract,notify_since) SELECT id,'swissjob-v1',clock_timestamp() FROM saved_searches"
        )
        config = execute("SELECT to_jsonb(e) FROM saved_search_execution e")
        failure = run_alembic(url, "downgrade", "core0049", check=False)
        assert failure.returncode != 0 and b"explicit reconciliation" in failure.stderr
        assert execute("SELECT to_jsonb(e) FROM saved_search_execution e") == config
        assert execute("SELECT to_jsonb(s) FROM saved_searches s") == before
        assert execute("SELECT version_num FROM alembic_version") == [("core0050",)]
    finally:
        asyncio.run(engine.dispose())
        asyncio.run(admin_sql(f'DROP DATABASE "{name}" WITH (FORCE)'))
        asyncio.run(admin.dispose())
