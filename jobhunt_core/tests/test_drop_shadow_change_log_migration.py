"""core0063 retires the shadow CDC staging and its GDPR redaction, and puts them back."""

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

STAGING = (
    "SELECT to_regclass('shadow_change_log') IS NOT NULL, "
    " EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'redact_erased_shadow_payload'), "
    " EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'redact_erased_shadow_payload')"
)


def test_core0063_drops_the_staging_and_downgrade_restores_it():
    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    name = "jobhunt_drop_staging_" + uuid.uuid4().hex[:12]
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
        run_alembic(url, "upgrade", "core0059")
        assert execute(STAGING) == [(True, True, True)]

        run_alembic(url, "upgrade", "core0063")
        assert execute(STAGING) == [(False, False, False)]

        run_alembic(url, "downgrade", "core0059")
        assert execute(STAGING) == [(True, True, True)]
        # The restored trigger still redacts, as core0046 defined it.
        execute(
            "INSERT INTO consumers (id, name, active) "
            "VALUES (gen_random_uuid(), 'swissjob-shadow', true)"
        )
        execute(
            "INSERT INTO profile_erasure_receipts (profile_id, consumer_id, external_ref) "
            "SELECT gen_random_uuid(), id, 'u-1' FROM consumers"
        )
        execute(
            "INSERT INTO shadow_change_log (lsn, seq_in_tx, src_table, op, pk, payload) "
            "VALUES (1, 0, 'user_profiles', 'I', 'p', '{\"user_id\": \"u-1\"}')"
        )
        assert execute("SELECT payload FROM shadow_change_log") == [({},)]
    finally:
        asyncio.run(engine.dispose())
        asyncio.run(admin_sql(f'DROP DATABASE "{name}" WITH (FORCE)'))
        asyncio.run(admin.dispose())
