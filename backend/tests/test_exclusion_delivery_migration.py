"""Actual migration DDL and seed on synthetic PostgreSQL data; rolled back."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from models.job_filter import JobFilter
from tests.conftest import TestSessionLocal
from tests.test_analytics_router import _auth


@pytest.mark.asyncio
async def test_migration_seeds_active_rules_and_deletions_and_guards_downgrade(client):
    _, active_user = await _auth(client)
    _, deleted_user = await _auth(client)
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/c4d8e2f60a17_exclusion_delivery.py'
    spec = importlib.util.spec_from_file_location('delivery_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def migrate(connection, action):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(migration, action)()

    async with TestSessionLocal() as session:
        session.add_all([
            JobFilter(user_id=active_user, filter_type='title_contains', pattern='director'),
            JobFilter(user_id=active_user, filter_type='tag_contains', pattern='obsolete', is_active=False),
            JobFilter(user_id=deleted_user, filter_type='title_contains', pattern='removed', is_active=False),
        ])
        await session.commit()
        connection = await session.connection()
        try:
            # This DROP and all migration DDL are inside an uncommitted test tx.
            # Rollback restores the original fixture schema before teardown.
            await connection.execute(text('DROP TABLE exclusion_sync_state'))
            await connection.run_sync(migrate, 'upgrade')
            rows = (await session.execute(text(
                'SELECT user_id, version, delivered_version, exclusions FROM exclusion_sync_state'
            ))).all()
            states = {row.user_id: row for row in rows}
            assert set(states) == {active_user, deleted_user}
            assert states[active_user].exclusions == [{'kind': 'title_contains', 'pattern': 'director'}]
            assert states[deleted_user].exclusions == []
            assert all(row.version == 1 and row.delivered_version == 0 for row in rows)
            with pytest.raises(DBAPIError, match='pending exclusions'):
                async with connection.begin_nested():
                    await connection.run_sync(migrate, 'downgrade')
            assert (await session.execute(text('SELECT count(*) FROM exclusion_sync_state'))).scalar_one() == 2
            await session.execute(text('UPDATE exclusion_sync_state SET delivered_version = version'))
            await connection.run_sync(migrate, 'downgrade')
            assert (await session.execute(text("SELECT to_regclass('exclusion_sync_state')"))).scalar_one() is None
        finally:
            await session.rollback()
