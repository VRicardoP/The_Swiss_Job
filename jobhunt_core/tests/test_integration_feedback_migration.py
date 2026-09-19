"""Published core0047 upgrades without losing state; downgrade refuses new marks."""

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


def test_feedback_incremental_upgrade_and_non_destructive_downgrade():
    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    name = "jobhunt_feedback_" + uuid.uuid4().hex[:12]
    parts = urlsplit(admin_url)
    url = urlunsplit((parts.scheme, parts.netloc, "/" + name, "", ""))
    admin = create_async_engine(
        admin_url, poolclass=sa.pool.NullPool, isolation_level="AUTOCOMMIT"
    )
    engine = create_async_engine(
        url, poolclass=sa.pool.NullPool,
        connect_args={"server_settings": {"search_path": f"{settings.CORE_DB_SCHEMA},public"}},
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
        run_alembic(url, "upgrade", "core0047")
        execute("INSERT INTO consumers(name) VALUES ('feedback-migration-test')")
        execute("INSERT INTO profiles(consumer_id,external_ref) SELECT id,'owner' FROM consumers")
        execute("INSERT INTO schools(id,school_key,name,country) VALUES (gen_random_uuid(),'CH:test','Test','CH')")
        execute("INSERT INTO school_monitors(id,consumer_id,school_id,external_ref,settings) "
                "SELECT gen_random_uuid(),c.id,s.id,'test','{}' FROM consumers c CROSS JOIN schools s")
        execute("INSERT INTO school_applications(id,profile_id,consumer_id,monitor_id,source_ref,status,draft_content) "
                "SELECT gen_random_uuid(),p.id,p.consumer_id,m.id,'old','drafted','preserve draft' "
                "FROM profiles p JOIN school_monitors m ON m.consumer_id=p.consumer_id")
        execute("INSERT INTO vacancies(id) VALUES (gen_random_uuid())")
        execute("INSERT INTO profile_vacancy_state(profile_id,vacancy_id,feedback) "
                "SELECT p.id,v.id,'thumbs_up' FROM profiles p CROSS JOIN vacancies v")
        before = execute("SELECT draft_content,status,version FROM school_applications")
        run_alembic(url, "upgrade", "core0048")
        assert execute("SELECT draft_content,status,version FROM school_applications") == before
        assert execute("SELECT feedback,feedback_recorded_at,feedback_implicit FROM school_applications") == [(None, None, [])]
        assert execute("SELECT feedback,feedback_recorded_at FROM profile_vacancy_state") == [("thumbs_up", None)]

        # Explicit positive, explicit CLEAR, and implicit-only are all durable.
        for assignment in (
            "feedback='thumbs_up'",
            "feedback=NULL,feedback_recorded_at=clock_timestamp()",
            "feedback_recorded_at=NULL,feedback_implicit='[{\"action\":\"opened\"}]'::jsonb",
        ):
            execute("UPDATE school_applications SET " + assignment)
            snapshot = execute("SELECT to_jsonb(a) FROM school_applications a")
            failure = run_alembic(url, "downgrade", "core0047", check=False)
            assert failure.returncode != 0 and b"school feedback remains" in failure.stderr
            assert execute("SELECT to_jsonb(a) FROM school_applications a") == snapshot
            assert execute("SELECT version_num FROM alembic_version") == [("core0048",)]

        execute("UPDATE school_applications SET feedback=NULL,feedback_recorded_at=NULL,feedback_implicit='[]'")
        execute("UPDATE profile_vacancy_state SET feedback_recorded_at=clock_timestamp()")
        failure = run_alembic(url, "downgrade", "core0047", check=False)
        assert failure.returncode != 0 and b"vacancy feedback authority remains" in failure.stderr
        execute("UPDATE profile_vacancy_state SET feedback_recorded_at=NULL")
        run_alembic(url, "downgrade", "core0047")
        assert execute("SELECT draft_content,status,version FROM school_applications") == before
        assert execute("SELECT feedback FROM profile_vacancy_state") == [("thumbs_up",)]
        run_alembic(url, "upgrade", "core0048")
    finally:
        asyncio.run(engine.dispose())
        asyncio.run(admin_sql(f'DROP DATABASE "{name}" WITH (FORCE)'))
        asyncio.run(admin.dispose())
