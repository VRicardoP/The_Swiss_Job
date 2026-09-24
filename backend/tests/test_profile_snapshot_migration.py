"""Real incremental migration and backfill; not merely ORM-created tables."""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
            # INSERT explícito, NO el modelo ORM: esta base está clavada en
            # `b46e1230a901` y el ORM describe el esquema de HOY. Con el modelo,
            # cada columna que se añada a `users` en el futuro rompe esta prueba
            # con un `UndefinedColumnError` que no tiene nada que ver con lo que
            # se está probando (pasó con `users.token_version`, T9). Nombrar las
            # columnas de la época es además lo honesto: lo que se mide es una
            # migración sobre el esquema que había, no sobre el de ahora.
            await db.execute(
                text(
                    "INSERT INTO users "
                    "(id, email, hashed_password, is_active, plan, gdpr_consent) "
                    "VALUES (:id, :email, :pwd, true, 'free', true)"
                ),
                {
                    "id": uid,
                    "email": "snapshot@example.invalid",
                    "pwd": "synthetic-not-authenticatable",
                },
            )
            # Las columnas NOT NULL sin defecto de servidor van explícitas: el
            # valor por omisión lo ponía el modelo ORM, que aquí no interviene.
            await db.execute(
                text(
                    "INSERT INTO user_profiles "
                    "(id, user_id, title, cv_text, skills, languages, locations, "
                    " remote_pref) "
                    "VALUES (:pid, :uid, :title, :cv, '[]'::jsonb, '[]'::jsonb, "
                    " '[]'::jsonb, 'any')"
                ),
                {
                    "pid": uuid.uuid4(),
                    "uid": uid,
                    "title": "Existing CV",
                    "cv": "Persisted content",
                },
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
