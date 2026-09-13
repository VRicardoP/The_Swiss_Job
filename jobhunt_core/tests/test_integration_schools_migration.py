"""Incremental core0046 -> core0047 and non-destructive downgrade."""

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
    not os.getenv("CORE_ADMIN_DATABASE_URL"), reason="requires test PG"
)


def test_schools_upgrade_from_published_head_and_guarded_downgrade():
    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    name = "jobhunt_schools_" + uuid.uuid4().hex[:12]
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

    async def sql(statement, **params):
        async with engine.begin() as c:
            result = await c.execute(sa.text(statement), params)
            return result.all() if result.returns_rows else None

    asyncio.run(admin_sql(f'CREATE DATABASE "{name}"'))
    try:
        asyncio.run(sql("CREATE EXTENSION vector"))
        asyncio.run(sql("CREATE EXTENSION pg_trgm"))
        asyncio.run(sql(f'CREATE SCHEMA "{settings.CORE_DB_SCHEMA}"'))
        run_alembic(url, "upgrade", "core0046")
        assert asyncio.run(sql("SELECT to_regclass('school_monitors')")) == [(None,)]
        run_alembic(url, "upgrade", "core0047")
        sid = uuid.uuid4()
        asyncio.run(
            sql(
                "INSERT INTO schools(id,school_key,name,country) VALUES (:id,'CH:example','Example','CH')",
                id=sid,
            )
        )
        failure = run_alembic(url, "downgrade", "core0046", check=False)
        assert failure.returncode != 0 and b"school state remains" in failure.stderr
        assert asyncio.run(sql("SELECT name FROM schools WHERE id=:id", id=sid)) == [
            ("Example",)
        ]
        assert asyncio.run(sql("SELECT version_num FROM alembic_version")) == [
            ("core0047",)
        ]
        asyncio.run(sql("DELETE FROM schools WHERE id=:id", id=sid))
        run_alembic(url, "downgrade", "core0046")
        assert asyncio.run(sql("SELECT to_regclass('school_monitors')")) == [(None,)]
        run_alembic(url, "upgrade", "core0047")
    finally:
        asyncio.run(engine.dispose())
        asyncio.run(admin_sql(f'DROP DATABASE "{name}" WITH (FORCE)'))
        asyncio.run(admin.dispose())
