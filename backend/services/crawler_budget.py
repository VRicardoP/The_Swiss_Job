"""CrawlerBudgetService — presupuesto explícito de peticiones por fuente.

Materializa la sección 7 del plan incremental (docs/PLAN_STEALTH_SCRAPER_JOBUP.md):
"pocas peticiones al día" como política explícita, no como efecto colateral del
early-stop. Decisiones puras (sin I/O) a partir del historial del cursor:

- `max_pages_this_run`: cuántas páginas puede pedir el run según la media de
  novedades — expected_pages = ceil(avg_new / page_size) + margen, con clamp.
- `should_run`: backoff de frecuencia — tras N runs seguidos sin novedades la
  fuente se salta runs hasta cumplir un intervalo ampliado (x2 por run vacío
  extra, con tope).

El pipeline (tasks/scraping_tasks.py) inyecta el resultado en el scraper antes
de `fetch_jobs`; el scraper solo respeta el tope (ver BaseScraper._pages_budget).
"""

import math
from datetime import datetime, timedelta, timezone

from config import settings
from models.source_cursor import SourceCursor


class CrawlerBudgetService:
    """Decisiones de presupuesto de crawling. Sin estado ni I/O: solo cálculo."""

    @staticmethod
    def max_pages_this_run(
        cursor: SourceCursor, page_size: int, source_cap: int
    ) -> int:
        """Páginas que este run puede pedir, según las novedades medias.

        Bootstrap pendiente → ventana de arranque completa (el tope de la
        fuente). Con historial: páginas esperadas para absorber la media de
        ofertas nuevas + margen de seguridad, acotado a [1, source_cap].
        """
        cap = max(1, source_cap)
        if not cursor.bootstrap_complete:
            return cap
        expected_new = max(cursor.avg_new_jobs_per_run or 0.0, 1.0)
        expected_pages = (
            math.ceil(expected_new / max(page_size, 1))
            + settings.CRAWLER_BUDGET_SAFETY_PAGES
        )
        return min(max(expected_pages, 1), cap)

    @staticmethod
    def should_run(
        cursor: SourceCursor,
        base_interval_hours: float,
        now: datetime | None = None,
    ) -> bool:
        """Indica si toca consultar la fuente o el backoff manda saltar el run.

        Con menos de CRAWLER_BUDGET_EMPTY_RUNS_THRESHOLD runs vacíos seguidos
        (o sin historial) siempre se ejecuta. A partir del umbral, el intervalo
        exigido se duplica por cada run vacío extra, con tope en
        CRAWLER_BUDGET_BACKOFF_MAX_MULTIPLIER x el intervalo base.
        """
        streak = cursor.consecutive_empty_runs or 0
        threshold = settings.CRAWLER_BUDGET_EMPTY_RUNS_THRESHOLD
        if streak < threshold or cursor.last_run_at is None:
            return True
        # Acotamos el exponente antes de elevar: una fuente muerta acumula rachas
        # de docenas y `2**streak` sería un entero enorme reducido luego a tope.
        cap = settings.CRAWLER_BUDGET_BACKOFF_MAX_MULTIPLIER
        max_exp = cap.bit_length()  # 2**max_exp >= cap → suficiente para saturar
        exponent = min(streak - threshold + 1, max_exp)
        multiplier = min(2**exponent, cap)
        current_time = now or datetime.now(timezone.utc)
        required_gap = timedelta(hours=base_interval_hours * multiplier)
        return (current_time - cursor.last_run_at) >= required_gap
