"""Base abstractions for job caching and job source providers."""

import hashlib
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from services.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


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
            k for k, ts in self._timestamps.items() if now - ts > self.ttl_seconds
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

    # --- Class-level defaults (override in subclasses as needed) ---
    SOURCE_NAME: str = ""
    SNIPPET_LENGTH: int = 200
    MAX_TAGS: int = 15
    USER_AGENT: str = "SwissJobHunter/1.0"
    DEFAULT_HEADERS: dict[str, str] = {"User-Agent": "SwissJobHunter/1.0"}
    CB_FAILURE_THRESHOLD: int = 5
    CB_RECOVERY_TIMEOUT: int = 60

    def __init__(self):
        self._cache = BaseJobCache()
        self._stats: dict[str, Any] = {
            "total_fetched": 0,
            "last_fetch_at": None,
            "errors": 0,
        }
        self._circuit = CircuitBreaker(
            name=self.SOURCE_NAME,
            failure_threshold=self.CB_FAILURE_THRESHOLD,
            recovery_timeout=self.CB_RECOVERY_TIMEOUT,
        )

    def get_source_name(self) -> str:
        """Return the unique source identifier. Uses SOURCE_NAME by default."""
        return self.SOURCE_NAME

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

    def _process_raw_jobs(self, raw_jobs: list) -> list[dict]:
        """Normalize a list of raw items, cache valid ones."""
        results: list[dict] = []
        for raw in raw_jobs:
            try:
                job = self.normalize_job(raw)
                self._cache.set(job["hash"], job)
                results.append(job)
            except (KeyError, ValueError, TypeError, AttributeError, IndexError) as e:
                self._stats["errors"] += 1
                logger.error("Error normalizing %s job: %s", self.SOURCE_NAME, e)
        return results

    def _finalize_fetch(self, results: list[dict]) -> list[dict]:
        """Update stats after a fetch cycle and return results."""
        self._stats["total_fetched"] += len(results)
        self._stats["last_fetch_at"] = datetime.now(timezone.utc).isoformat()
        return results

    def _snippet(self, text: str | None) -> str | None:
        """Truncate text to SNIPPET_LENGTH for description_snippet field."""
        return text[: self.SNIPPET_LENGTH] if text else None
