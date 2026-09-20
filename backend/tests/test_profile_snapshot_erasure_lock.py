"""The retained profile writer cannot deadlock a concurrent account erasure."""

import asyncio
import time

from sqlalchemy import text

from tests.conftest import TestSessionLocal
from tests.test_analytics_router import _auth


async def test_profile_writer_then_account_erasure_has_no_inverse_lock_order(client):
    _, uid = await _auth(client)
    async with TestSessionLocal() as writer, TestSessionLocal() as eraser:
        await writer.execute(text("SET LOCAL lock_timeout='2s'"))
        await writer.execute(
            text("SELECT id FROM user_profiles WHERE user_id=:u FOR UPDATE"), {"u": uid}
        )
        writer_pid = await writer.scalar(text("SELECT pg_backend_pid()"))
        eraser_pid = await eraser.scalar(text("SELECT pg_backend_pid()"))

        async def erase():
            await eraser.execute(text("DELETE FROM users WHERE id=:u"), {"u": uid})
            await eraser.commit()

        task = asyncio.create_task(erase())
        try:
            deadline = time.monotonic() + 5
            while True:
                async with TestSessionLocal() as observer:
                    blocked = await observer.scalar(
                        text("SELECT :w = ANY(pg_blocking_pids(:e))"),
                        {"w": writer_pid, "e": eraser_pid},
                    )
                if blocked:
                    break
                assert not task.done() and time.monotonic() < deadline
                await asyncio.sleep(0.01)
            # Erasure holds the parent and awaits this profile. Queueing must not
            # acquire the parent in the inverse direction or revive the account.
            await writer.execute(
                text("UPDATE user_profiles SET title='Finishing' WHERE user_id=:u"),
                {"u": uid},
            )
            await writer.commit()
            await asyncio.wait_for(task, 5)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert (
            await writer.scalar(
                text("SELECT count(*) FROM profile_sync_state WHERE user_id=:u"),
                {"u": uid},
            )
            == 0
        )
