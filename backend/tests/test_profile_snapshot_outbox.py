"""Source edits and their latest delivery snapshot must commit atomically."""

import asyncio
import time

import pytest
from sqlalchemy import text

from tests.conftest import TestSessionLocal
from tests.test_analytics_router import _auth


async def _row(db, uid):
    exists = await db.scalar(text("SELECT to_regclass('profile_sync_state')"))
    assert exists is not None, "profile mutations still have no durable delivery outbox"
    return (
        await db.execute(
            text(
                "SELECT version,delivered_version,content,active FROM profile_sync_state WHERE user_id=:u"
            ),
            {"u": uid},
        )
    ).one()


async def test_profile_snapshot_shares_mutation_commit_and_rollback(client):
    _, uid = await _auth(client)
    async with TestSessionLocal() as db:
        before = await _row(db, uid)
        await db.execute(
            text("UPDATE user_profiles SET title='Uncommitted' WHERE user_id=:u"),
            {"u": uid},
        )
        assert (await _row(db, uid)).content["title"] == "Uncommitted"
        await db.rollback()
        assert await _row(db, uid) == before
        await db.execute(
            text("UPDATE user_profiles SET title='Committed' WHERE user_id=:u"),
            {"u": uid},
        )
        await db.commit()
    async with TestSessionLocal() as db:
        after = await _row(db, uid)
        assert after.version == before.version + 1
        assert after.content["title"] == "Committed"
        assert after.delivered_version == before.delivered_version


async def test_unrelated_profile_writes_do_not_enqueue_another_snapshot(client):
    _, uid = await _auth(client)
    async with TestSessionLocal() as db:
        before = await _row(db, uid)
        await db.execute(
            text(
                "UPDATE user_profiles SET cv_embedding=NULL,score_weights='{}' WHERE user_id=:u"
            ),
            {"u": uid},
        )
        await db.commit()
        assert await _row(db, uid) == before


@pytest.mark.parametrize("first", ["profile", "activity"])
async def test_concurrent_activity_and_profile_edits_preserve_both(client, first):
    _, uid = await _auth(client)
    statements = {
        "profile": "UPDATE user_profiles SET title='Concurrent CV' WHERE user_id=:u",
        "activity": "UPDATE users SET is_active=false WHERE id=:u",
    }
    second = "activity" if first == "profile" else "profile"
    async with TestSessionLocal() as a, TestSessionLocal() as b:
        before = await _row(a, uid)
        apid = await a.scalar(text("SELECT pg_backend_pid()"))
        bpid = await b.scalar(text("SELECT pg_backend_pid()"))
        await a.execute(text(statements[first]), {"u": uid})

        async def second_edit():
            await b.execute(text(statements[second]), {"u": uid})
            await b.commit()

        task = asyncio.create_task(second_edit())
        try:
            # Prove real lock contention, not an arbitrary sleep ordering.
            deadline = time.monotonic() + 5
            while True:
                async with TestSessionLocal() as observer:
                    blocked = await observer.scalar(
                        text("SELECT :a = ANY(pg_blocking_pids(:b))"),
                        {"a": apid, "b": bpid},
                    )
                if blocked:
                    break
                assert not task.done(), (
                    "writers did not serialize on their durable snapshot"
                )
                assert time.monotonic() < deadline, (
                    "expected DB lock was never observed"
                )
                await asyncio.sleep(0.01)
            await a.commit()
            await asyncio.wait_for(task, 5)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        after = await _row(a, uid)
        assert after.version == before.version + 2
        assert after.content["title"] == "Concurrent CV" and after.active is False


async def test_profile_removal_clears_snapshot_and_account_removal_erases_it(client):
    _, uid = await _auth(client)
    async with TestSessionLocal() as db:
        await _row(db, uid)
        await db.execute(text("DELETE FROM user_profiles WHERE user_id=:u"), {"u": uid})
        await db.commit()
        cleared = await _row(db, uid)
        assert cleared.content["cv_text"] is None
        assert cleared.content["title"] is None and cleared.content["skills"] == []
        await db.execute(text("DELETE FROM users WHERE id=:u"), {"u": uid})
        await db.commit()
        assert (
            await db.scalar(
                text("SELECT count(*) FROM profile_sync_state WHERE user_id=:u"),
                {"u": uid},
            )
            == 0
        )
