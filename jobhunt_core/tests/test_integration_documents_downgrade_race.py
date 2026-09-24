"""An INSERT between the emptiness check and DROP must not be lost."""

import asyncio
import importlib
import uuid
from concurrent.futures import ThreadPoolExecutor

import sqlalchemy as sa
from alembic import op
from alembic.migration import MigrationContext
from alembic.operations import Operations

from jobhunt_core.tests.test_integration_api_documents import _seed
from jobhunt_core.tests.test_integration_api_saved_searches import db, pytestmark  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)


def test_documents_downgrade_locks_before_empty_check(db, monkeypatch):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    _, _, pid = _seed(factory, made)
    migration = importlib.import_module(
        "jobhunt_core.alembic.versions.core0043_generated_documents"
    )
    original_execute = op.execute
    observed = []

    def concurrent_insert():
        async def insert():
            async with factory() as s:
                await s.execute(sa.text("SET LOCAL lock_timeout='250ms'"))
                try:
                    await s.execute(
                        sa.text(
                            "INSERT INTO generated_documents(id,profile_id,doc_type,content,output_hash) "
                            "VALUES (:d,:p,'cv','late durable',:h)"
                        ),
                        {"d": uuid.uuid4(), "p": pid, "h": "b" * 64},
                    )
                    await s.commit()
                    return "committed"
                except sa.exc.DBAPIError as exc:
                    if getattr(exc.orig, "sqlstate", None) != "55P03":
                        raise
                    return "blocked"

        return asyncio.run(insert())

    def probe(statement, *args, **kwargs):
        result = original_execute(statement, *args, **kwargs)
        if str(statement).lstrip().startswith("DO $$"):
            # Guard has returned: the original downgrade now believes the table empty.
            with ThreadPoolExecutor(max_workers=1) as pool:
                observed.append(pool.submit(concurrent_insert).result(timeout=5))
        return result

    monkeypatch.setattr(op, "execute", probe)

    async def downgrade_then_restore_schema():
        async with factory() as s:
            connection = await s.connection()

            def run(sync_connection):
                with Operations.context(MigrationContext.configure(sync_connection)):
                    migration.downgrade()

            try:
                await connection.run_sync(run)
            finally:
                # Schema restored even for the intentionally failing pre-fix test.
                await s.rollback()

    asyncio.run(downgrade_then_restore_schema())
    assert observed == ["blocked"], (
        "a committed INSERT would be dropped after the empty check"
    )
