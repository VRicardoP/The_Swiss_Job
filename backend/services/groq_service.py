"""GroqService — LLM re-ranking and chat via Groq API.

Uses llama-3.1-8b-instant for fast re-ranking (Stage 3) and
llama-3.3-70b-versatile for heavier tasks (CV adaptation in Fase 3+).
Groq SDK is synchronous — calls are wrapped in run_in_threadpool.
"""

import asyncio
import hashlib
import json
import logging
from typing import Any

from fastapi.concurrency import run_in_threadpool

from config import settings

logger = logging.getLogger(__name__)

RERANK_SYSTEM_PROMPT = """You are an expert technical recruiter AI. Your job is to evaluate how well job postings match a candidate's profile.

Scoring rules:
- 80-100 = excellent fit (candidate meets most/all requirements)
- 60-79 = good fit (core requirements met, minor gaps)
- 40-59 = partial fit (some relevant skills, significant gaps)
- 0-39 = poor fit (few matching skills or wrong domain)

Evaluation criteria:
1. Technical skills match (languages, frameworks, tools)
2. Seniority and experience level alignment
3. Industry/domain experience relevance
4. Location and remote compatibility
5. Remote jobs get +5 bonus

IMPORTANT: Respond ONLY with a valid JSON array. No markdown fences, no extra text."""


class GroqService:
    """Async wrapper around the synchronous Groq Python SDK."""

    def __init__(self, redis_client=None):
        self.client = None
        self.redis = redis_client
        if settings.GROQ_API_KEY:
            from groq import Groq

            self.client = Groq(api_key=settings.GROQ_API_KEY)

    @property
    def is_available(self) -> bool:
        return self.client is not None

    async def get_chat_response(
        self,
        user_message: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Send a chat completion request to Groq via threadpool."""
        if not self.is_available:
            raise RuntimeError("Groq client not configured (GROQ_API_KEY missing)")

        selected_model = model or settings.GROQ_RERANK_MODEL
        effective_temp = temperature if temperature is not None else 0.2
        effective_max_tokens = max_tokens or 2048

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})

        def _sync_call() -> str:
            completion = self.client.chat.completions.create(
                messages=messages,
                model=selected_model,
                temperature=effective_temp,
                max_tokens=effective_max_tokens,
            )
            return completion.choices[0].message.content

        return await run_in_threadpool(_sync_call)

    async def rerank_jobs(
        self,
        profile_text: str,
        profile_skills: list[str],
        candidates: list[dict],
    ) -> list[dict]:
        """Re-rank job candidates using LLM evaluation.

        Args:
            profile_text: User CV text or profile summary.
            profile_skills: User skills list.
            candidates: List of dicts with job info (from Stage 2).

        Returns:
            List of dicts with LLM scores and explanations, keyed by index.
        """
        if not self.is_available:
            return []

        batch_size = settings.GROQ_RERANK_BATCH_SIZE
        batches = [
            candidates[i : i + batch_size]
            for i in range(0, len(candidates), batch_size)
        ]

        skills_text = ", ".join(profile_skills) if profile_skills else "Not specified"
        sem = asyncio.Semaphore(settings.GROQ_CONCURRENCY)

        async def _process_batch(batch_idx: int, batch: list[dict]) -> list[dict]:
            jobs_for_prompt = [
                {
                    "index": i,
                    "title": c.get("title", ""),
                    "company": c.get("company", ""),
                    "description": (c.get("description", "") or "")[:300],
                    "tags": (c.get("tags") or [])[:10],
                    "location": c.get("location", ""),
                    "remote": c.get("remote", False),
                }
                for i, c in enumerate(batch)
            ]

            jobs_json = json.dumps(jobs_for_prompt, ensure_ascii=False)
            user_prompt = (
                f"## Candidate Profile\n"
                f"Skills: {skills_text}\n"
                f"{profile_text[:2000]}\n\n"
                f"## Jobs to Evaluate (batch {batch_idx + 1}/{len(batches)})\n"
                f"{jobs_json}\n\n"
                "Evaluate each job. Return a JSON array where each element has:\n"
                '- "index": the job index from above\n'
                '- "score": 0-100\n'
                '- "matching_skills": list of matching skills\n'
                '- "missing_skills": list of missing skills\n'
                '- "reason": one-sentence explanation\n\n'
                "Respond ONLY with the JSON array."
            )

            cache_key = self._cache_key(user_prompt)
            cached = await self._get_cached(cache_key)
            if cached is not None:
                logger.debug("Groq cache hit for batch %d", batch_idx + 1)
                batch_results = cached
            else:
                async with sem:
                    try:
                        response = await self.get_chat_response(
                            user_message=user_prompt,
                            system_prompt=RERANK_SYSTEM_PROMPT,
                            model=settings.GROQ_RERANK_MODEL,
                            temperature=settings.GROQ_RERANK_TEMPERATURE,
                            max_tokens=settings.GROQ_RERANK_MAX_TOKENS,
                        )
                        batch_results = self._parse_llm_response(response, len(batch))
                        await self._set_cached(cache_key, batch_results)
                    except Exception:
                        logger.exception(
                            "Groq rerank failed for batch %d/%d",
                            batch_idx + 1,
                            len(batches),
                        )
                        batch_results = self._fallback_results(len(batch))

            for r in batch_results:
                r["global_index"] = r.get("index", 0) + (batch_idx * batch_size)
            return batch_results

        batch_results_list = await asyncio.gather(
            *[_process_batch(i, b) for i, b in enumerate(batches)]
        )

        all_results: list[dict] = []
        for batch_results in batch_results_list:
            all_results.extend(batch_results)

        return all_results

    @staticmethod
    def _parse_llm_response(response: str, batch_len: int) -> list[dict]:
        """Parse LLM JSON response, stripping markdown fences if present."""
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3].strip()

        try:
            results = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse Groq response as JSON")
            return GroqService._fallback_results(batch_len)

        if not isinstance(results, list):
            logger.warning("Groq response is not a list")
            return GroqService._fallback_results(batch_len)

        # Normalize each result
        normalized: list[dict[str, Any]] = []
        for r in results:
            normalized.append(
                {
                    "index": r.get("index", 0),
                    "score": max(0, min(100, r.get("score", 0))),
                    "matching_skills": r.get("matching_skills", []),
                    "missing_skills": r.get("missing_skills", []),
                    "reason": r.get("reason", ""),
                }
            )

        return normalized

    @staticmethod
    def _fallback_results(count: int) -> list[dict]:
        """Generate empty fallback results when LLM fails."""
        return [
            {
                "index": i,
                "score": 0,
                "matching_skills": [],
                "missing_skills": [],
                "reason": "",
            }
            for i in range(count)
        ]

    @staticmethod
    def _cache_key(prompt: str) -> str:
        """Generate a Redis cache key from the prompt hash."""
        h = hashlib.md5(prompt.encode()).hexdigest()
        return f"groq:rerank:{h}"

    async def _get_cached(self, key: str) -> list[dict] | None:
        """Retrieve cached rerank results from Redis."""
        if not self.redis:
            return None
        try:
            data = await self.redis.get(key)
            if data:
                return json.loads(data)
        except Exception:
            logger.debug("Redis cache read failed for %s", key)
        return None

    async def _set_cached(self, key: str, results: list[dict]) -> None:
        """Store rerank results in Redis with TTL."""
        if not self.redis:
            return
        try:
            ttl = settings.GROQ_CACHE_TTL_DAYS * 86400
            await self.redis.set(key, json.dumps(results), ex=ttl)
        except Exception:
            logger.debug("Redis cache write failed for %s", key)
