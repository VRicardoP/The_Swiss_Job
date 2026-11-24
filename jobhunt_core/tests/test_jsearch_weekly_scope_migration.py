"""core0058 and core0059 move the JSearch scope to its weekly Swiss contract and back, keeping its history."""

import asyncio
import json
import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from jobhunt_core.config import settings
from jobhunt_core.provision_jsearch import SCOPE_PARAMS, SOURCE
from jobhunt_core.provision_scopes import scope_id
from jobhunt_core.tests.alembic_runner import run_alembic

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"), reason="requires PG"
)

# Frozen like the migrations: SCOPE_PARAMS[0] is only the final contract.
WEEKLY_US = {**SCOPE_PARAMS[0], "country": "us"}
MONTHLY = {**WEEKLY_US, "date_posted": "month", "num_pages": 10}
STATE = (
    "SELECT h.id, h.params, h.enabled, "
    " (SELECT count(*) FROM source_scope_state s WHERE s.scope_id = h.id), "
    " (SELECT count(*) FROM source_harvest_runs r WHERE r.scope_id = h.id) "
    "FROM harvest_scopes h"
)


def test_the_scope_moves_with_its_state_and_runs():
    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    name = "jobhunt_jsearch_weekly_" + uuid.uuid4().hex[:12]
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

    async def sql(statement, params=None):
        async with engine.begin() as c:
            result = await c.execute(sa.text(statement), params or {})
            return result.all() if result.returns_rows else None

    def execute(statement, params=None):
        return asyncio.run(sql(statement, params))

    old, weekly, new = (
        scope_id(SOURCE, params) for params in (MONTHLY, WEEKLY_US, SCOPE_PARAMS[0])
    )
    asyncio.run(admin_sql(f'CREATE DATABASE "{name}"'))
    try:
        execute("CREATE EXTENSION vector")
        execute("CREATE EXTENSION pg_trgm")
        execute(f'CREATE SCHEMA "{settings.CORE_DB_SCHEMA}"')
        run_alembic(url, "upgrade", "core0057")
        execute(
            "INSERT INTO sources (id, name, tier) VALUES (gen_random_uuid(), 'jsearch', 0)"
        )
        execute(
            "INSERT INTO harvest_scopes (id, source_id, params, tier, enabled) "
            "SELECT :id, id, CAST(:params AS jsonb), 0, true FROM sources",
            {"id": old, "params": json.dumps(MONTHLY)},
        )
        execute(
            "INSERT INTO source_scope_state (scope_id, consecutive_failures) VALUES (:id, 2)",
            {"id": old},
        )
        execute("INSERT INTO harvest_runs DEFAULT VALUES")
        execute(
            "INSERT INTO source_harvest_runs (run_id, scope_id, started_at, status) "
            "SELECT id, :id, clock_timestamp(), 'ok' FROM harvest_runs",
            {"id": old},
        )

        run_alembic(url, "upgrade", "core0058")
        assert execute(STATE) == [(weekly, WEEKLY_US, True, 1, 1)]

        run_alembic(url, "upgrade", "core0059")
        assert execute(STATE) == [(new, SCOPE_PARAMS[0], True, 1, 1)]

        run_alembic(url, "downgrade", "core0057")
        assert execute(STATE) == [(old, MONTHLY, True, 1, 1)]
    finally:
        asyncio.run(engine.dispose())
        asyncio.run(admin_sql(f'DROP DATABASE "{name}" WITH (FORCE)'))
        asyncio.run(admin.dispose())
