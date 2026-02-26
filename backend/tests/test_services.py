"""Tests for base services: job_service, circuit_breaker, sse_manager, job_matcher."""

import asyncio
import time
import uuid

import pytest

from services.circuit_breaker import CircuitBreaker, CircuitBreakerOpen, CircuitState
from services.job_matcher import JobMatcher
from services.job_service import BaseJobCache, BaseJobProvider
from services.sse_manager import SSEManager


# --- BaseJobCache ---


class TestBaseJobCache:
    def test_set_and_get(self):
        cache = BaseJobCache(ttl_seconds=60)
        cache.set("abc", {"title": "Dev"})
        assert cache.get("abc") == {"title": "Dev"}

    def test_get_missing(self):
        cache = BaseJobCache()
        assert cache.get("missing") is None

    def test_expired_entry(self):
        cache = BaseJobCache(ttl_seconds=0)
        cache.set("key", {"title": "Old"})
        time.sleep(0.01)
        assert cache.get("key") is None

    def test_get_all(self):
        cache = BaseJobCache()
        cache.set("a", {"id": "1"})
        cache.set("b", {"id": "2"})
        assert len(cache.get_all()) == 2

    def test_clear(self):
        cache = BaseJobCache()
        cache.set("a", {"id": "1"})
        cache.clear()
        assert cache.size == 0

    def test_size(self):
        cache = BaseJobCache()
        assert cache.size == 0
        cache.set("a", {"id": "1"})
        assert cache.size == 1


# --- BaseJobProvider ---


class TestBaseJobProvider:
    def test_compute_hash(self):
        h1 = BaseJobProvider.compute_hash("Dev", "ACME", "http://example.com/1")
        h2 = BaseJobProvider.compute_hash("Dev", "ACME", "http://example.com/1")
        h3 = BaseJobProvider.compute_hash("QA", "ACME", "http://example.com/2")
        assert h1 == h2
        assert h1 != h3

    def test_compute_hash_case_insensitive(self):
        h1 = BaseJobProvider.compute_hash("Developer", "Acme Corp", "http://x.com")
        h2 = BaseJobProvider.compute_hash("DEVELOPER", "ACME CORP", "http://x.com")
        assert h1 == h2


# --- CircuitBreaker ---


class TestCircuitBreaker:
    async def test_closed_state_allows_calls(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)

        async def succeed():
            return "ok"

        result = await cb.call(succeed)
        assert result == "ok"

    async def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout=60)

        async def fail():
            raise ValueError("boom")

        for _ in range(2):
            with pytest.raises(ValueError):
                await cb.call(fail)

        assert cb.state == CircuitState.OPEN

    async def test_open_state_rejects_calls(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=60)

        async def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await cb.call(fail)

        with pytest.raises(CircuitBreakerOpen) as exc_info:
            await cb.call(fail)
        assert "test" in str(exc_info.value)

    async def test_half_open_after_timeout(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0)

        async def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await cb.call(fail)

        # With recovery_timeout=0, it should transition to HALF_OPEN immediately
        assert cb.state == CircuitState.HALF_OPEN

    async def test_closes_after_success_in_half_open(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0)

        async def fail():
            raise ValueError("boom")

        async def succeed():
            return "ok"

        with pytest.raises(ValueError):
            await cb.call(fail)

        result = await cb.call(succeed)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    def test_reset(self):
        cb = CircuitBreaker(name="test")
        cb._failure_count = 10
        cb._state = CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0

    def test_get_status(self):
        cb = CircuitBreaker(name="my_source", failure_threshold=5)
        status = cb.get_status()
        assert status["name"] == "my_source"
        assert status["state"] == "closed"

    async def test_half_open_allows_only_one_probe(self):
        """HALF_OPEN allows one probe; failure re-opens the circuit."""
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0)

        async def fail():
            raise ValueError("boom")

        # Trip the breaker
        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.HALF_OPEN

        # Probe call is allowed (fails → re-opens)
        # Use long timeout so circuit stays OPEN after re-trip
        cb.recovery_timeout = 300
        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN

    async def test_half_open_rejects_concurrent_probes(self):
        """While a probe is pending in HALF_OPEN, additional calls are rejected."""
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0)

        async def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.HALF_OPEN

        # Simulate a probe already in-flight
        cb._half_open_pending = True

        with pytest.raises(CircuitBreakerOpen):

            async def succeed():
                return "ok"

            await cb.call(succeed)

    async def test_half_open_success_clears_pending(self):
        """A successful probe closes the circuit and clears pending flag."""
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0)

        async def fail():
            raise ValueError("boom")

        async def succeed():
            return "ok"

        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.HALF_OPEN

        result = await cb.call(succeed)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED
        assert cb._half_open_pending is False


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

    async def test_broadcast_to_all(self, sse_manager):
        u1, u2 = uuid.uuid4(), uuid.uuid4()
        q1 = await sse_manager.subscribe(u1)
        q2 = await sse_manager.subscribe(u2)

        await sse_manager.broadcast_to_all("global", {"msg": "hello"})

        msg1 = await asyncio.wait_for(q1.get(), timeout=2.0)
        msg2 = await asyncio.wait_for(q2.get(), timeout=2.0)
        assert msg1["event"] == "global"
        assert msg2["event"] == "global"

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

    async def test_get_active_connections(self, sse_manager):
        user_id = uuid.uuid4()
        await sse_manager.subscribe(user_id)
        await sse_manager.subscribe(user_id)

        active = sse_manager.get_active_connections()
        assert active[str(user_id)] == 2

    async def test_start_stop_lifecycle(self, redis_client):
        """Verify clean start/stop without errors."""
        mgr = SSEManager(redis_client, queue_maxsize=5)
        await mgr.start()
        assert mgr._listener_task is not None
        assert not mgr._listener_task.done()
        await mgr.stop()
        assert mgr._listener_task.done()


# --- JobMatcher (without model loading) ---


class TestJobMatcher:
    def test_build_job_text(self):
        job = {
            "title": "Python Developer",
            "company": "ACME",
            "description": "Build APIs",
            "tags": ["python", "fastapi"],
        }
        text = JobMatcher.build_job_text(job)
        assert "Python Developer" in text
        assert "ACME" in text
        assert "python fastapi" in text

    def test_salary_match_no_preference(self):
        score = JobMatcher.compute_salary_match(None, None, 80000, 100000)
        assert score == 0.5

    def test_salary_match_no_data(self):
        score = JobMatcher.compute_salary_match(80000, 100000, None, None)
        assert score == 0.5

    def test_salary_match_perfect(self):
        score = JobMatcher.compute_salary_match(80000, 100000, 90000, 120000)
        assert score == 1.0  # Job pays more than midpoint

    def test_salary_match_low(self):
        score = JobMatcher.compute_salary_match(100000, 120000, 50000, 60000)
        assert score < 0.5

    def test_location_match_exact(self):
        score = JobMatcher.compute_location_match(["Zurich"], "Zurich")
        assert score == 1.0

    def test_location_match_partial(self):
        score = JobMatcher.compute_location_match(["Zurich"], "Zurich, Switzerland")
        assert score == 1.0

    def test_location_match_no_preference(self):
        score = JobMatcher.compute_location_match([], "Bern")
        assert score == 0.5

    def test_location_match_no_data(self):
        score = JobMatcher.compute_location_match(["Zurich"], None)
        assert score == 0.3

    def test_location_match_mismatch(self):
        score = JobMatcher.compute_location_match(["Zurich"], "Geneva")
        assert score == 0.0

    def test_recency_score_today(self):
        assert JobMatcher.compute_recency_score(0) == 1.0

    def test_recency_score_old(self):
        assert JobMatcher.compute_recency_score(60) == 0.1

    def test_final_score_range(self):
        matcher = JobMatcher()
        score = matcher.compute_final_score(
            embedding_score=0.8,
            salary_score=0.7,
            location_score=1.0,
            recency_score=0.9,
            llm_score=0.0,
        )
        assert 0 <= score <= 100

    def test_final_score_custom_weights(self):
        matcher = JobMatcher()
        weights = {
            "embedding": 1.0,
            "salary": 0.0,
            "location": 0.0,
            "recency": 0.0,
            "llm": 0.0,
        }
        score = matcher.compute_final_score(
            embedding_score=0.5,
            salary_score=0.0,
            location_score=0.0,
            recency_score=0.0,
            weights=weights,
        )
        assert score == 50.0
