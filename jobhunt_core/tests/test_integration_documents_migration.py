"""Upgrade from published core0042; populated downgrades must refuse data loss."""

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


def test_documents_incremental_upgrade_and_guarded_downgrade():
    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    name = "jobhunt_documents_" + uuid.uuid4().hex[:12]
    parts = urlsplit(admin_url)
    url = urlunsplit((parts.scheme, parts.netloc, "/" + name, "", ""))
    admin = create_async_engine(
        admin_url, poolclass=sa.pool.NullPool, isolation_level="AUTOCOMMIT"
    )
    engine = create_async_engine(
        url,
        poolclass=sa.pool.NullPool,
        connect_args={
            "server_settings": {"search_path": f"{settings.CORE_DB_SCHEMA}, public"},
        },
    )

    async def admin_sql(sql):
        async with admin.connect() as c:
            await c.execute(sa.text(sql))

    async def sql(statement, **params):
        async with engine.begin() as c:
            result = await c.execute(sa.text(statement), params)
            return result.all() if result.returns_rows else None

    asyncio.run(admin_sql(f'CREATE DATABASE "{name}"'))
    try:
        asyncio.run(sql("CREATE EXTENSION vector"))
        asyncio.run(sql("CREATE EXTENSION pg_trgm"))
        asyncio.run(sql(f'CREATE SCHEMA IF NOT EXISTS "{settings.CORE_DB_SCHEMA}"'))
        run_alembic(url, "upgrade", "core0042")
        assert asyncio.run(sql("SELECT to_regclass('generated_documents')")) == [
            (None,)
        ]
        run_alembic(url, "upgrade", "core0043")
        cid, pid, did = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        asyncio.run(
            sql(
                "INSERT INTO consumers(id,name) VALUES (:c,'documents-migration')",
                c=cid,
            )
        )
        asyncio.run(
            sql(
                "INSERT INTO profiles(id,consumer_id,external_ref) VALUES (:p,:c,'1')",
                p=pid,
                c=cid,
            )
        )
        asyncio.run(
            sql(
                "INSERT INTO generated_documents(id,profile_id,doc_type,content,output_hash) "
                "VALUES (:d,:p,'cv','original',:h)",
                d=did,
                p=pid,
                h="a" * 64,
            )
        )
        failed = run_alembic(url, "downgrade", "core0042", check=False)
        assert failed.returncode != 0 and b"documents remain" in failed.stderr
        assert asyncio.run(
            sql("SELECT content FROM generated_documents WHERE id=:d", d=did)
        ) == [("original",)]
        assert asyncio.run(sql("SELECT version_num FROM alembic_version")) == [
            ("core0043",)
        ]
        asyncio.run(sql("DELETE FROM generated_documents WHERE id=:d", d=did))
        run_alembic(url, "downgrade", "core0042")
        assert asyncio.run(sql("SELECT to_regclass('generated_documents')")) == [
            (None,)
        ]
        run_alembic(url, "upgrade", "core0043")
        assert asyncio.run(sql("SELECT count(*) FROM generated_documents")) == [(0,)]
    finally:
        asyncio.run(engine.dispose())
        asyncio.run(admin_sql(f'DROP DATABASE "{name}" WITH (FORCE)'))
        asyncio.run(admin.dispose())
