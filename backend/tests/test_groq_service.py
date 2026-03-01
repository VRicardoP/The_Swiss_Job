"""Tests for GroqService — LLM re-ranking with mocked Groq client."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.groq_service import GroqService


@pytest.mark.anyio
class TestGroqServiceAvailability:
    def test_not_available_without_api_key(self):
        with patch("services.groq_service.settings") as mock_settings:
            mock_settings.GROQ_API_KEY = ""
            service = GroqService()
            assert service.is_available is False

    def test_available_with_client_set(self):
        svc = GroqService.__new__(GroqService)
        svc.client = MagicMock()
        svc.redis = None
        assert svc.is_available is True

    def test_not_available_with_no_client(self):
        svc = GroqService.__new__(GroqService)
        svc.client = None
        svc.redis = None
        assert svc.is_available is False


@pytest.mark.anyio
class TestGroqServiceParsing:
    def test_parse_valid_json(self):
        response = json.dumps(
            [
                {
                    "index": 0,
                    "score": 85,
                    "matching_skills": ["python", "fastapi"],
                    "missing_skills": ["kubernetes"],
                    "reason": "Strong backend match.",
                },
                {
                    "index": 1,
                    "score": 42,
                    "matching_skills": ["sql"],
                    "missing_skills": ["java", "spring"],
                    "reason": "Partial overlap.",
                },
            ]
        )
        results = GroqService._parse_llm_response(response, 2)
        assert len(results) == 2
        assert results[0]["score"] == 85
        assert results[0]["matching_skills"] == ["python", "fastapi"]
        assert results[1]["reason"] == "Partial overlap."

    def test_parse_markdown_fenced_json(self):
        response = '```json\n[{"index": 0, "score": 70, "reason": "OK"}]\n```'
        results = GroqService._parse_llm_response(response, 1)
        assert len(results) == 1
        assert results[0]["score"] == 70

    def test_parse_invalid_json_returns_fallback(self):
        results = GroqService._parse_llm_response("not valid json at all", 3)
        assert len(results) == 3
        assert all(r["score"] == 0 for r in results)

    def test_parse_clamps_score_range(self):
        response = json.dumps([{"index": 0, "score": 150}, {"index": 1, "score": -20}])
        results = GroqService._parse_llm_response(response, 2)
        assert results[0]["score"] == 100
        assert results[1]["score"] == 0

    def test_parse_non_list_returns_fallback(self):
        response = json.dumps({"index": 0, "score": 80})
        results = GroqService._parse_llm_response(response, 1)
        assert len(results) == 1
        assert results[0]["score"] == 0


@pytest.mark.anyio
class TestGroqServiceFallback:
    def test_fallback_results(self):
        results = GroqService._fallback_results(5)
        assert len(results) == 5
        for i, r in enumerate(results):
            assert r["index"] == i
            assert r["score"] == 0
            assert r["reason"] == ""


@pytest.mark.anyio
class TestGroqServiceCacheKey:
    def test_cache_key_deterministic(self):
        key1 = GroqService._cache_key("hello world")
        key2 = GroqService._cache_key("hello world")
        assert key1 == key2
        assert key1.startswith("groq:rerank:")

    def test_cache_key_differs_for_different_input(self):
        key1 = GroqService._cache_key("prompt A")
        key2 = GroqService._cache_key("prompt B")
        assert key1 != key2


@pytest.mark.anyio
class TestGroqServiceRerank:
    async def test_rerank_returns_empty_when_unavailable(self):
        svc = GroqService.__new__(GroqService)
        svc.client = None
        svc.redis = None
        results = await svc.rerank_jobs(
            profile_text="Python developer",
            profile_skills=["python"],
            candidates=[{"title": "Dev", "company": "Co"}],
        )
        assert results == []

    async def test_rerank_calls_llm_and_returns_results(self):
        svc = GroqService.__new__(GroqService)
        svc.client = MagicMock()  # Make is_available True
        svc.redis = None

        llm_response = json.dumps(
            [
                {
                    "index": 0,
                    "score": 90,
                    "matching_skills": ["python"],
                    "missing_skills": [],
                    "reason": "Excellent match.",
                }
            ]
        )

        svc.get_chat_response = AsyncMock(return_value=llm_response)

        results = await svc.rerank_jobs(
            profile_text="Senior Python developer with FastAPI experience",
            profile_skills=["python", "fastapi"],
            candidates=[
                {
                    "title": "Python Engineer",
                    "company": "TechCo",
                    "description": "Build APIs",
                    "tags": ["python", "fastapi"],
                    "location": "Zurich",
                    "remote": False,
                }
            ],
        )

        assert len(results) == 1
        assert results[0]["score"] == 90
        assert results[0]["global_index"] == 0
        svc.get_chat_response.assert_called_once()

    async def test_rerank_handles_llm_error_gracefully(self):
        svc = GroqService.__new__(GroqService)
        svc.client = MagicMock()
        svc.redis = None

        svc.get_chat_response = AsyncMock(side_effect=RuntimeError("API error"))

        results = await svc.rerank_jobs(
            profile_text="Developer",
            profile_skills=["python"],
            candidates=[{"title": "Dev"}, {"title": "Dev2"}],
        )

        # Should return fallback results with score=0
        assert len(results) == 2
        assert all(r["score"] == 0 for r in results)

    async def test_rerank_batching(self):
        svc = GroqService.__new__(GroqService)
        svc.client = MagicMock()
        svc.redis = None

        # Create 15 candidates with batch_size=10 => 2 batches
        batch1_response = json.dumps(
            [{"index": i, "score": 80 - i, "reason": f"Job {i}"} for i in range(10)]
        )
        batch2_response = json.dumps(
            [{"index": i, "score": 70 - i, "reason": f"Job {i}"} for i in range(5)]
        )
        svc.get_chat_response = AsyncMock(
            side_effect=[batch1_response, batch2_response]
        )

        candidates = [{"title": f"Job {i}"} for i in range(15)]

        with patch("services.groq_service.settings") as mock_settings:
            mock_settings.GROQ_RERANK_BATCH_SIZE = 10
            mock_settings.GROQ_RERANK_MODEL = "test-model"
            mock_settings.GROQ_RERANK_TEMPERATURE = 0.2
            mock_settings.GROQ_RERANK_MAX_TOKENS = 2048
            mock_settings.GROQ_CACHE_TTL_DAYS = 7
            mock_settings.GROQ_CONCURRENCY = 2

            results = await svc.rerank_jobs(
                profile_text="Developer",
                profile_skills=["python"],
                candidates=candidates,
            )

        assert len(results) == 15
        assert svc.get_chat_response.call_count == 2
        # First batch global_index 0-9, second batch 10-14
        global_indices = sorted(r["global_index"] for r in results)
        assert global_indices == list(range(15))


@pytest.mark.anyio
class TestGroqServiceCache:
    async def test_cache_hit_skips_llm_call(self):
        svc = GroqService.__new__(GroqService)
        svc.client = MagicMock()

        mock_redis = AsyncMock()
        cached_data = json.dumps([{"index": 0, "score": 95, "reason": "Cached result"}])
        mock_redis.get = AsyncMock(return_value=cached_data.encode())
        svc.redis = mock_redis

        svc.get_chat_response = AsyncMock()

        with patch("services.groq_service.settings") as mock_settings:
            mock_settings.GROQ_RERANK_BATCH_SIZE = 10
            mock_settings.GROQ_RERANK_MODEL = "test-model"
            mock_settings.GROQ_RERANK_TEMPERATURE = 0.2
            mock_settings.GROQ_RERANK_MAX_TOKENS = 2048
            mock_settings.GROQ_CACHE_TTL_DAYS = 7
            mock_settings.GROQ_CONCURRENCY = 2

            results = await svc.rerank_jobs(
                profile_text="Developer",
                profile_skills=["python"],
                candidates=[{"title": "Job"}],
            )

        assert len(results) == 1
        assert results[0]["score"] == 95
        svc.get_chat_response.assert_not_called()

    async def test_cache_miss_calls_llm_and_stores(self):
        svc = GroqService.__new__(GroqService)
        svc.client = MagicMock()

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock()
        svc.redis = mock_redis

        llm_response = json.dumps([{"index": 0, "score": 75, "reason": "Good"}])
        svc.get_chat_response = AsyncMock(return_value=llm_response)

        with patch("services.groq_service.settings") as mock_settings:
            mock_settings.GROQ_RERANK_BATCH_SIZE = 10
            mock_settings.GROQ_RERANK_MODEL = "test-model"
            mock_settings.GROQ_RERANK_TEMPERATURE = 0.2
            mock_settings.GROQ_RERANK_MAX_TOKENS = 2048
            mock_settings.GROQ_CACHE_TTL_DAYS = 7
            mock_settings.GROQ_CONCURRENCY = 2

            results = await svc.rerank_jobs(
                profile_text="Developer",
                profile_skills=["python"],
                candidates=[{"title": "Job"}],
            )

        assert len(results) == 1
        svc.get_chat_response.assert_called_once()
        mock_redis.set.assert_called_once()
