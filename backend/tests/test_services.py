"""Tests for base services: job_service, circuit_breaker, sse_manager, job_matcher."""

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


# --- SSEManager ---


class TestSSEManager:
    async def test_subscribe_and_broadcast(self):
        mgr = SSEManager()
        user_id = uuid.uuid4()
        queue = await mgr.subscribe(user_id)

        sent = await mgr.broadcast_to_user(user_id, "new_matches", {"count": 5})
        assert sent == 1

        msg = queue.get_nowait()
        assert msg["event"] == "new_matches"
        assert msg["data"]["count"] == 5

    async def test_unsubscribe(self):
        mgr = SSEManager()
        user_id = uuid.uuid4()
        queue = await mgr.subscribe(user_id)
        mgr.unsubscribe(user_id, queue)

        sent = await mgr.broadcast_to_user(user_id, "test", {})
        assert sent == 0

    async def test_multiple_connections(self):
        mgr = SSEManager()
        user_id = uuid.uuid4()
        q1 = await mgr.subscribe(user_id)
        q2 = await mgr.subscribe(user_id)

        sent = await mgr.broadcast_to_user(user_id, "update", {"x": 1})
        assert sent == 2

        assert q1.get_nowait()["event"] == "update"
        assert q2.get_nowait()["event"] == "update"

    async def test_broadcast_to_nonexistent_user(self):
        mgr = SSEManager()
        sent = await mgr.broadcast_to_user(uuid.uuid4(), "test", {})
        assert sent == 0

    async def test_broadcast_to_all(self):
        mgr = SSEManager()
        u1, u2 = uuid.uuid4(), uuid.uuid4()
        await mgr.subscribe(u1)
        await mgr.subscribe(u2)

        total = await mgr.broadcast_to_all("global", {"msg": "hello"})
        assert total == 2

    def test_format_sse(self):
        result = SSEManager.format_sse("test_event", {"key": "value"})
        assert "event: test_event" in result
        assert '"key": "value"' in result

    async def test_get_active_connections(self):
        mgr = SSEManager()
        user_id = uuid.uuid4()
        await mgr.subscribe(user_id)
        await mgr.subscribe(user_id)

        active = mgr.get_active_connections()
        assert active[str(user_id)] == 2


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
