"""Tests for the Redis-backed SSE manager."""

import asyncio
import uuid

import pytest

from services.sse_manager import SSEManager


# --- SSEManager (Redis pub/sub) ---


class TestSSEManager:
    async def test_subscribe_and_broadcast(self, sse_manager):
        user_id = uuid.uuid4()
        queue = await sse_manager.subscribe(user_id)

        await sse_manager.broadcast_to_user(user_id, "new_matches", {"count": 5})

        # Wait for Redis pub/sub round-trip
        msg = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert msg["event"] == "new_matches"
        assert msg["data"]["count"] == 5

    async def test_unsubscribe(self, sse_manager):
        user_id = uuid.uuid4()
        queue = await sse_manager.subscribe(user_id)
        sse_manager.unsubscribe(user_id, queue)

        await sse_manager.broadcast_to_user(user_id, "test", {})

        # Queue should remain empty since we unsubscribed
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.5)

    async def test_multiple_connections(self, sse_manager):
        user_id = uuid.uuid4()
        q1 = await sse_manager.subscribe(user_id)
        q2 = await sse_manager.subscribe(user_id)

        await sse_manager.broadcast_to_user(user_id, "update", {"x": 1})

        msg1 = await asyncio.wait_for(q1.get(), timeout=2.0)
        msg2 = await asyncio.wait_for(q2.get(), timeout=2.0)
        assert msg1["event"] == "update"
        assert msg2["event"] == "update"

    async def test_queue_overflow_drops_oldest(self, sse_manager):
        """TD-01: Verify bounded queue drops oldest on overflow."""
        user_id = uuid.uuid4()
        queue = await sse_manager.subscribe(user_id)

        # Fill queue to capacity (maxsize=10) via direct local fanout
        for i in range(10):
            sse_manager._fanout_to_local(user_id, {"event": "fill", "data": {"i": i}})

        assert queue.full()

        # Push one more — should drop oldest (i=0) and insert new
        sse_manager._fanout_to_local(user_id, {"event": "overflow", "data": {"i": 10}})

        assert sse_manager.dropped_events == 1

        # First item in queue should now be i=1 (i=0 was dropped)
        msg = queue.get_nowait()
        assert msg["data"]["i"] == 1

    async def test_dropped_events_counter(self, sse_manager):
        """TD-12: Counter increments on each overflow."""
        user_id = uuid.uuid4()
        await sse_manager.subscribe(user_id)

        # Push 15 events into a queue with maxsize=10
        for i in range(15):
            sse_manager._fanout_to_local(user_id, {"event": "test", "data": {"i": i}})

        assert sse_manager.dropped_events == 5

    def test_format_sse(self):
        result = SSEManager.format_sse("test_event", {"key": "value"})
        assert "event: test_event" in result
        assert '"key": "value"' in result

    async def test_start_stop_lifecycle(self, redis_client):
        """Verify clean start/stop without errors."""
        mgr = SSEManager(redis_client, queue_maxsize=5)
        await mgr.start()
        assert mgr._listener_task is not None
        assert not mgr._listener_task.done()
        await mgr.stop()
        assert mgr._listener_task.done()


# --- JobMatcher (without model loading) ---
