"""Base abstractions for job caching and job source providers."""

import hashlib
import time
from abc import ABC, abstractmethod
from typing import Any


class BaseJobCache:
    """In-memory job cache with TTL. Will be extended with PostgreSQL persistence in Fase 1."""

    def __init__(self, ttl_seconds: int = 1800):
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, dict[str, Any]] = {}
        self._timestamps: dict[str, float] = {}

    def get(self, key: str) -> dict | None:
        """Get a cached job by key, or None if expired/missing."""
        if key not in self._cache:
            return None
        if time.monotonic() - self._timestamps[key] > self.ttl_seconds:
            self._cache.pop(key, None)
            self._timestamps.pop(key, None)
            return None
        return self._cache[key]

    def set(self, key: str, job: dict) -> None:
        """Cache a job with current timestamp."""
        self._cache[key] = job
        self._timestamps[key] = time.monotonic()

    def get_all(self) -> list[dict]:
        """Return all non-expired cached jobs."""
        now = time.monotonic()
        expired = [
            k
            for k, ts in self._timestamps.items()
            if now - ts > self.ttl_seconds
        ]
        for k in expired:
            self._cache.pop(k, None)
            self._timestamps.pop(k, None)
        return list(self._cache.values())

    def clear(self) -> None:
        """Clear all cached data."""
        self._cache.clear()
        self._timestamps.clear()

    @property
    def size(self) -> int:
        return len(self._cache)


class BaseJobProvider(ABC):
    """Abstract base for all job source providers (API + scrapers)."""

    def __init__(self):
        self._cache = BaseJobCache()
        self._stats = {"total_fetched": 0, "last_fetch_at": None, "errors": 0}

    @abstractmethod
    def get_source_name(self) -> str:
        """Return the unique source identifier (e.g., 'jooble', 'careerjet')."""
        ...

    @abstractmethod
    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from the source. Returns list of normalized job dicts."""
        ...

    @abstractmethod
    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw API/scraper response into the unified job schema."""
        ...

    def get_cache(self) -> BaseJobCache:
        return self._cache

    def get_all_jobs(self) -> list[dict]:
        """Return all cached jobs for this source."""
        return self._cache.get_all()

    def get_stats(self) -> dict:
        """Return fetch statistics for monitoring."""
        return {
            "source": self.get_source_name(),
            "cached_jobs": self._cache.size,
            **self._stats,
        }

    @staticmethod
    def compute_hash(title: str, company: str, url: str) -> str:
        """Compute a unique hash for deduplication."""
        raw = f"{title.strip().lower()}|{company.strip().lower()}|{url.strip()}"
        return hashlib.md5(raw.encode()).hexdigest()
