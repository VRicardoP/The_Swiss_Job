"""Real upgrade/backfill/downgrade, isolated schema and rollback on exit."""

import importlib.util
from pathlib import Path
import uuid

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import test_engine


def _migrate(connection, direction):
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/d5e9f3071b28_document_preservation.py"
    )
    spec = importlib.util.spec_from_file_location("preservation", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(connection)):
        getattr(migration, direction)()


@pytest.mark.anyio
@pytest.mark.parametrize("changed", [None, "job_deleted", "job_edited"])
async def test_preservation_upgrade_and_safe_downgrade(changed):
    async with test_engine.connect() as conn:
        async with conn.begin() as transaction:
            schema = "doc_migration_" + uuid.uuid4().hex
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await conn.execute(
                text(
                    "CREATE TABLE jobs (hash varchar(32) PRIMARY KEY, title varchar(500), company varchar(300))"
                )
            )
            await conn.execute(
                text("""CREATE TABLE generated_documents (
                id integer PRIMARY KEY, job_hash varchar(32) NOT NULL REFERENCES jobs(hash) ON DELETE CASCADE,
                content text NOT NULL, created_at timestamptz NOT NULL
            )""")
            )
            await conn.execute(
                text("INSERT INTO jobs VALUES ('source', 'Original', 'Company')")
            )
            await conn.execute(
                text(
                    "INSERT INTO generated_documents VALUES (1, 'source', 'CV exact', '2026-01-01Z')"
                )
            )
            await conn.run_sync(_migrate, "upgrade")
            row = (
                await conn.execute(
                    text(
                        "SELECT job_title, job_company, content, created_at FROM generated_documents"
                    )
                )
            ).one()
            assert row[:3] == ("Original", "Company", "CV exact")
            assert row.created_at.year == 2026
            if changed == "job_deleted":
                await conn.execute(text("DELETE FROM jobs"))
            elif changed == "job_edited":
                await conn.execute(text("UPDATE jobs SET title='Changed'"))
            if changed:
                with pytest.raises(
                    DBAPIError, match="snapshots require reconciliation"
                ):
                    async with conn.begin_nested():
                        await conn.run_sync(_migrate, "downgrade")
                assert (
                    await conn.scalar(text("SELECT count(*) FROM generated_documents"))
                    == 1
                )
                assert (
                    await conn.scalar(text("SELECT job_title FROM generated_documents"))
                    == "Original"
                )
            else:
                await conn.run_sync(_migrate, "downgrade")
                assert (
                    await conn.scalar(text("SELECT content FROM generated_documents"))
                    == "CV exact"
                )
                await conn.execute(text("DELETE FROM jobs"))
                assert (
                    await conn.scalar(text("SELECT count(*) FROM generated_documents"))
                    == 0
                )
            await transaction.rollback()
