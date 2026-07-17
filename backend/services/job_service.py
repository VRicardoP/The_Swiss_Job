"""Base abstractions for job caching and job source providers."""

import hashlib
import logging
from abc import ABC, abstractmethod
from typing import Any

from services.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


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
    # Techo de paginación por run. Default 1 (providers de API son O(1)); los
    # scrapers lo suben. Lo usa `_pages_budget()` como cota del presupuesto.
    MAX_PAGES: int = 1

    def __init__(self):
        self._circuit = CircuitBreaker(
            name=self.SOURCE_NAME,
            failure_threshold=self.CB_FAILURE_THRESHOLD,
            recovery_timeout=self.CB_RECOVERY_TIMEOUT,
        )
        # Crawler incremental: identidades (URLs) ya vistas, inyectadas por el
        # pipeline ANTES de fetch_jobs para el early-stop. Vacío = sin early-stop
        # (comportamiento legacy). `_stop_reason` es observabilidad del run.
        self._known_urls: set[str] = set()
        self._stop_reason: str | None = None
        # Presupuesto dinámico de páginas para ESTE run, inyectado por el
        # pipeline (CrawlerBudgetService) antes de fetch_jobs. None = sin
        # presupuesto → se usa MAX_PAGES (comportamiento legacy).
        self._max_pages_this_run: int | None = None

    def _pages_budget(self) -> int:
        """Tope de páginas del run: el presupuesto inyectado, acotado por MAX_PAGES."""
        if self._max_pages_this_run is None:
            return self.MAX_PAGES
        return max(1, min(self.MAX_PAGES, self._max_pages_this_run))

    def get_source_name(self) -> str:
        """Return the unique source identifier. Uses SOURCE_NAME by default."""
        return self.SOURCE_NAME

    @abstractmethod
    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from the source. Returns list of normalized job dicts."""
        ...

    @abstractmethod
    def normalize_job(self, raw: Any) -> dict:
        """Transform a raw API/scraper response into the unified job schema."""
        ...

    @staticmethod
    def compute_hash(title: str, company: str, url: str) -> str:
        """Compute a unique hash for deduplication."""
        raw = f"{title.strip().lower()}|{company.strip().lower()}|{url.strip()}"
        return hashlib.md5(raw.encode()).hexdigest()

    @staticmethod
    def job_identity(job: dict) -> str:
        """Identidad estable de una oferta para cursor/early-stop.

        Usa la `url` (única en la tabla jobs). En stubs de scraper que aún no la
        exponen, cae a `detail_url`/`source_id`/`hash`. Cadena vacía = sin identidad
        (nunca coincidirá con el cursor → no fuerza un early-stop erróneo).
        """
        return (
            job.get("url")
            or job.get("detail_url")
            or job.get("source_id")
            or job.get("hash")
            or ""
        ).strip()

    def _page_all_known(self, page_jobs: list[dict]) -> bool:
        """True si TODA la página ya se había visto (ninguna oferta nueva) según el
        cursor inyectado en `_known_urls`.

        Es la señal del crawler incremental: hemos alcanzado el contenido ya
        sincronizado → dejar de paginar. Con `_known_urls` vacío nunca corta.
        """
        if not self._known_urls or not page_jobs:
            return False
        return all(self.job_identity(j) in self._known_urls for j in page_jobs)

    # Required fields in every normalized job dict
    _REQUIRED_FIELDS = {"hash", "source", "title", "company", "url"}

    def _process_raw_jobs(self, raw_jobs: list) -> list[dict]:
        """Normalize a list of raw items and validate schema. Devuelve los válidos."""
        results: list[dict] = []
        for raw in raw_jobs:
            try:
                job = self.normalize_job(raw)
                missing = self._REQUIRED_FIELDS - job.keys()
                if missing:
                    raise ValueError(f"missing required fields: {missing}")
                if not job["title"] or not job["url"]:
                    raise ValueError("title and url must be non-empty")
                results.append(job)
            except (KeyError, ValueError, TypeError, AttributeError, IndexError) as e:
                logger.error("Error normalizing %s job: %s", self.SOURCE_NAME, e)
        return results

    def _finalize_fetch(self, results: list[dict]) -> list[dict]:
        """Hook post-fetch (punto de extensión). Devuelve los resultados sin cambios."""
        return results

    def _snippet(self, text: str | None) -> str | None:
        """Truncate text to SNIPPET_LENGTH for description_snippet field."""
        return text[: self.SNIPPET_LENGTH] if text else None
