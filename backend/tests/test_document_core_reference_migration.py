"""Real widening and fail-closed downgrade; isolated disposable SQL schema."""

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
    path = Path(__file__).parents[1] / "alembic/versions/a91c06e3df72_document_core_reference.py"
    spec = importlib.util.spec_from_file_location("core_reference", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(connection)):
        getattr(migration, direction)()


@pytest.mark.anyio
async def test_document_core_reference_upgrade_and_safe_downgrade():
    async with test_engine.connect() as conn:
        async with conn.begin() as transaction:
            schema = "doc_reference_" + uuid.uuid4().hex
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await conn.execute(text("CREATE TABLE generated_documents (id integer PRIMARY KEY, job_hash varchar(32) NOT NULL)"))
            await conn.execute(text("INSERT INTO generated_documents VALUES (1, :ref)"), {"ref": "a" * 32})
            await conn.run_sync(_migrate, "upgrade")
            ref = str(uuid.uuid4())
            await conn.execute(text("INSERT INTO generated_documents VALUES (2, :ref)"), {"ref": ref})
            with pytest.raises(DBAPIError, match="core document references require reconciliation"):
                async with conn.begin_nested():
                    await conn.run_sync(_migrate, "downgrade")
            assert await conn.scalar(text("SELECT job_hash FROM generated_documents WHERE id=2")) == ref
            await conn.execute(text("DELETE FROM generated_documents WHERE id=2"))
            await conn.run_sync(_migrate, "downgrade")
            assert await conn.scalar(text("SELECT job_hash FROM generated_documents WHERE id=1")) == "a" * 32
            assert await conn.scalar(text("SELECT character_maximum_length FROM information_schema.columns WHERE table_schema=:s AND table_name='generated_documents' AND column_name='job_hash'"), {"s": schema}) == 32
            await transaction.rollback()
