"""Real incremental migration and backfill; not merely ORM-created tables."""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.user import User
from models.user_profile import UserProfile
from tests.test_migration_smoke import _recreate_smoke_db, _SMOKE_URL, run_alembic


async def test_profile_snapshot_upgrade_seeds_existing_users_and_guards_downgrade():
    await _recreate_smoke_db()
    result = run_alembic(_SMOKE_URL, "upgrade", "b46e1230a901")
    assert result.returncode == 0, result.stderr.decode()
    engine = create_async_engine(_SMOKE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = uuid.uuid4()
    try:
        async with factory() as db:
            db.add(
                User(
                    id=uid,
                    email="snapshot@example.invalid",
                    hashed_password="synthetic-not-authenticatable",
                )
            )
            await db.flush()
            db.add(
                UserProfile(
                    user_id=uid, title="Existing CV", cv_text="Persisted content"
                )
            )
            await db.commit()
        result = run_alembic(_SMOKE_URL, "upgrade", "c57f2341b012")
        assert result.returncode == 0, result.stderr.decode()
        async with factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT version,delivered_version,content,active FROM profile_sync_state WHERE user_id=:u"
                    ),
                    {"u": uid},
                )
            ).one()
            assert (
                row.version == 1 and row.delivered_version == 0 and row.active is True
            )
            assert row.content["cv_text"] == "Persisted content"
            assert row.content["title"] == "Existing CV"
            await db.execute(
                text("UPDATE user_profiles SET title='After upgrade' WHERE user_id=:u"),
                {"u": uid},
            )
            await db.commit()
            snapshot = (
                await db.execute(
                    text(
                        "SELECT to_jsonb(s) FROM profile_sync_state s WHERE user_id=:u"
                    ),
                    {"u": uid},
                )
            ).scalar_one()
            assert snapshot["version"] == 2
        result = run_alembic(_SMOKE_URL, "downgrade", "b46e1230a901")
        assert result.returncode != 0 and b"profile delivery requires" in result.stderr
        async with factory() as db:
            assert (
                await db.scalar(
                    text(
                        "SELECT to_jsonb(s) FROM profile_sync_state s WHERE user_id=:u"
                    ),
                    {"u": uid},
                )
                == snapshot
            )
            await db.execute(text("DELETE FROM users WHERE id=:u"), {"u": uid})
            await db.commit()
        result = run_alembic(_SMOKE_URL, "downgrade", "b46e1230a901")
        assert result.returncode == 0, result.stderr.decode()
        result = run_alembic(_SMOKE_URL, "upgrade", "head")
        assert result.returncode == 0, result.stderr.decode()
    finally:
        await engine.dispose()
