"""NAS disposable copies only: profile delivery, ordering and safe recovery.

Run with the network namespace of the isolated rehearsal PostgreSQL container.
No live URL is accepted. Personal content stays inside that container namespace;
stdout contains only aggregates. This exercises real HTTP handlers via ASGI,
not production routing, and does not certify the deployment by itself.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from jobhunt_core import credentials, profiles
from jobhunt_core.api import deps
from jobhunt_core.api.main import app
from jobhunt_core.api.v1_profile_snapshot import ProfileSnapshotWrite
from jobhunt_core.database import create_core_engine

SOURCE_URL = "postgresql+asyncpg://swissjob@127.0.0.1:5432/source_copy"
CORE_URL = "postgresql+asyncpg://jobhunt_core@127.0.0.1:5432/core_copy"


async def main():
    if os.environ.get("CORE_DATABASE_URL") != CORE_URL:
        raise ValueError("disposable core_copy URL required")
    source_engine = create_async_engine(SOURCE_URL, poolclass=NullPool)
    core_engine = create_core_engine(poolclass=NullPool)
    source_factory = async_sessionmaker(source_engine)
    core_factory = async_sessionmaker(core_engine)

    async def session_override():
        async with core_factory() as db:
            yield db

    async def source_sql(sql, params=None):
        async with source_factory() as db:
            result = await db.execute(sa.text(sql), params or {})
            rows = result.mappings().all() if result.returns_rows else []
            await db.commit()
            return rows

    async def snapshot(uid):
        rows = await source_sql(
            "SELECT version,active,content FROM profile_sync_state WHERE user_id=:u",
            {"u": uid},
        )
        assert len(rows) == 1
        body = dict(rows[0])
        ProfileSnapshotWrite.model_validate(body)
        return body

    async def retained_state(pid):
        async with core_factory() as db:
            # Only feed pointers/timestamp may change on withdrawal.
            return (
                await db.execute(
                    sa.text(
                        "SELECT vacancy_id,to_jsonb(s)-'current_eval_id'-'updated_at' AS state "
                        "FROM profile_vacancy_state s WHERE profile_id=:p ORDER BY vacancy_id"
                    ),
                    {"p": pid},
                )
            ).all()

    async def check_content(pid, body, roles):
        async with core_factory() as db:
            current = await profiles.current_revision(db, pid)
            expected = profiles.normalize_profile(
                {**body["content"], "target_roles": roles}, allow_empty=True
            )
            assert current.content == expected, "content mismatch"
            row = (
                await db.execute(
                    sa.text(
                        "SELECT projection_version,projection_active FROM profiles WHERE id=:p"
                    ),
                    {"p": pid},
                )
            ).one()
            assert tuple(row) == (body["version"], body["active"])

    app.dependency_overrides[deps.get_session] = session_override
    try:
        bindings = await source_sql(
            "SELECT m.user_id,m.core_profile_id FROM jobhunt_profile_map m "
            "JOIN users u ON u.id=m.user_id JOIN user_profiles p ON p.user_id=u.id "
            "ORDER BY m.user_id"
        )
        assert bindings, "no profiles to rehearse"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://isolated-rehearsal"
        ) as client:
            for binding in bindings:
                uid, pid = binding["user_id"], binding["core_profile_id"]
                initial = await snapshot(uid)
                retained = await retained_state(pid)
                async with core_factory() as db:
                    cid = await db.scalar(
                        sa.text("SELECT consumer_id FROM profiles WHERE id=:p"),
                        {"p": pid},
                    )
                    assert cid is not None, "missing enrollment"
                    current = await profiles.current_revision(db, pid)
                    roles = current.content.get("target_roles", []) if current else []
                    key, secret = await credentials.create_credential(
                        db,
                        cid,
                        ["profiles:write"],
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    )
                    await db.commit()

                async def deliver(body, expected_version=None, expected_status=200):
                    response = await client.put(
                        f"/v1/profiles/{pid}/source-snapshot",
                        json=body,
                        headers={"Authorization": f"Bearer {key}.{secret}"},
                    )
                    assert response.status_code == expected_status, (
                        "delivery status mismatch"
                    )
                    if expected_status == 200:
                        assert response.json()["version"] == (
                            body["version"]
                            if expected_version is None
                            else expected_version
                        )

                try:
                    await deliver(initial)
                    await deliver(
                        initial
                    )  # lost acknowledgement, same immutable version
                    await check_content(pid, initial, roles)
                    await source_sql(
                        "UPDATE user_profiles SET title='Synthetic rehearsal edit' WHERE user_id=:u",
                        {"u": uid},
                    )
                    edited = await snapshot(uid)
                    assert edited["version"] > initial["version"]
                    await deliver(edited)
                    await deliver(initial, edited["version"])
                    await check_content(pid, edited, roles)
                    await deliver(
                        {**edited, "active": not edited["active"]}, expected_status=409
                    )
                    await source_sql(
                        "UPDATE user_profiles SET title=NULL,cv_text=NULL,skills='[]'::jsonb WHERE user_id=:u",
                        {"u": uid},
                    )
                    empty = await snapshot(uid)
                    await deliver(empty)
                    async with core_factory() as db:
                        assert (
                            await db.scalar(
                                sa.text(
                                    "SELECT count(*) FROM profile_vacancy_state "
                                    "WHERE profile_id=:p AND current_eval_id IS NOT NULL"
                                ),
                                {"p": pid},
                            )
                            == 0
                        )
                    assert await retained_state(pid) == retained
                finally:
                    # Forward recovery: restore original input through the retained
                    # writer and a NEW version, never rewind projection counters.
                    original = initial["content"]
                    await source_sql(
                        "UPDATE user_profiles SET title=:t,cv_text=:cv,skills=CAST(:s AS jsonb) WHERE user_id=:u",
                        {
                            "u": uid,
                            "t": original["title"],
                            "cv": original["cv_text"],
                            "s": json.dumps(original["skills"]),
                        },
                    )
                    restored = await snapshot(uid)
                    await deliver(restored)
                    await check_content(pid, restored, roles)
                    assert restored["content"] == original
                    assert await retained_state(pid) == retained
                    async with core_factory() as db:
                        assert (
                            await db.scalar(
                                sa.text(
                                    "SELECT count(*) FROM profile_recovery_state WHERE profile_id=:p"
                                ),
                                {"p": pid},
                            )
                            == 0
                        ), "restored revision must be rearmed"
                        await credentials.revoke_credential(db, key)
                        await db.commit()
        print(
            json.dumps(
                {
                    "verdict": "verified_on_isolated_copy",
                    "profiles": len(bindings),
                    "retry_ordering_conflict": True,
                    "empty_withdraws_feed": True,
                    "feedback_bookmarks_preserved": True,
                    "forward_recovery": True,
                    "recovery_rearmed": True,
                    "live_deployment_verified": False,
                }
            )
        )
    finally:
        app.dependency_overrides.clear()
        await source_engine.dispose()
        await core_engine.dispose()


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(main())
    except Exception as exc:
        # Do not expose SQL parameters, tokens, profiles or response bodies.
        print(json.dumps({"verdict": "failed", "type": type(exc).__name__}))
        raise SystemExit(1) from None
