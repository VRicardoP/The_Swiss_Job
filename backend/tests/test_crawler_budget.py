"""Tests for CrawlerBudgetService — presupuesto explícito de crawling.

Decisiones puras a partir del historial del cursor: páginas por run
(max_pages_this_run) y backoff de frecuencia (should_run).
"""

from datetime import datetime, timedelta, timezone

from config import settings
from models.source_cursor import SourceCursor
from services.crawler_budget import CrawlerBudgetService


def _cursor(**overrides) -> SourceCursor:
    """SourceCursor en memoria con historial explícito.

    Sin DB los defaults de columna no se aplican (van en flush), así que se
    fijan a mano los campos que lee el servicio.
    """
    cursor = SourceCursor(source_key="test", scope_key="default")
    cursor.bootstrap_complete = True
    cursor.avg_new_jobs_per_run = 0.0
    cursor.avg_pages_per_run = 0.0
    cursor.consecutive_empty_runs = 0
    cursor.last_run_at = None
    for key, value in overrides.items():
        setattr(cursor, key, value)
    return cursor


class TestMaxPagesThisRun:
    def test_bootstrap_pending_uses_full_cap(self):
        # Sin bootstrap no hay historial fiable: ventana de arranque completa.
        cursor = _cursor(bootstrap_complete=False, avg_new_jobs_per_run=100.0)
        assert CrawlerBudgetService.max_pages_this_run(cursor, 20, 10) == 10

    def test_few_new_jobs_needs_one_page_plus_safety(self):
        cursor = _cursor(avg_new_jobs_per_run=3.0)
        # ceil(3/20) = 1 página esperada + margen de seguridad.
        expected = 1 + settings.CRAWLER_BUDGET_SAFETY_PAGES
        assert CrawlerBudgetService.max_pages_this_run(cursor, 20, 10) == expected

    def test_zero_history_still_requests_minimum(self):
        # avg 0 se trata como 1 oferta esperada: nunca 0 páginas.
        cursor = _cursor(avg_new_jobs_per_run=0.0)
        expected = 1 + settings.CRAWLER_BUDGET_SAFETY_PAGES
        assert CrawlerBudgetService.max_pages_this_run(cursor, 20, 10) == expected

    def test_many_new_jobs_clamped_to_source_cap(self):
        cursor = _cursor(avg_new_jobs_per_run=500.0)
        assert CrawlerBudgetService.max_pages_this_run(cursor, 20, 10) == 10

    def test_scales_with_page_size(self):
        cursor = _cursor(avg_new_jobs_per_run=45.0)
        # ceil(45/20) = 3 páginas esperadas + margen.
        expected = 3 + settings.CRAWLER_BUDGET_SAFETY_PAGES
        assert CrawlerBudgetService.max_pages_this_run(cursor, 20, 10) == expected

    def test_source_cap_below_one_sanitized(self):
        cursor = _cursor(bootstrap_complete=False)
        assert CrawlerBudgetService.max_pages_this_run(cursor, 20, 0) == 1


class TestShouldRun:
    BASE_HOURS = 6.0

    def test_runs_without_history(self):
        assert CrawlerBudgetService.should_run(_cursor(), self.BASE_HOURS) is True

    def test_runs_below_empty_threshold(self):
        now = datetime.now(timezone.utc)
        cursor = _cursor(
            consecutive_empty_runs=settings.CRAWLER_BUDGET_EMPTY_RUNS_THRESHOLD - 1,
            last_run_at=now,
        )
        assert CrawlerBudgetService.should_run(cursor, self.BASE_HOURS, now=now) is True

    def test_skips_recent_run_at_threshold(self):
        # En el umbral el multiplicador es 2: con solo el intervalo base
        # transcurrido aún no toca.
        now = datetime.now(timezone.utc)
        cursor = _cursor(
            consecutive_empty_runs=settings.CRAWLER_BUDGET_EMPTY_RUNS_THRESHOLD,
            last_run_at=now - timedelta(hours=self.BASE_HOURS),
        )
        assert (
            CrawlerBudgetService.should_run(cursor, self.BASE_HOURS, now=now) is False
        )

    def test_runs_when_expanded_interval_elapsed(self):
        now = datetime.now(timezone.utc)
        cursor = _cursor(
            consecutive_empty_runs=settings.CRAWLER_BUDGET_EMPTY_RUNS_THRESHOLD,
            last_run_at=now - timedelta(hours=self.BASE_HOURS * 2),
        )
        assert CrawlerBudgetService.should_run(cursor, self.BASE_HOURS, now=now) is True

    def test_backoff_multiplier_is_capped(self):
        # Racha enorme: el gap exigido queda en base x MAX_MULTIPLIER, no crece más.
        now = datetime.now(timezone.utc)
        capped_gap = self.BASE_HOURS * settings.CRAWLER_BUDGET_BACKOFF_MAX_MULTIPLIER
        ready = _cursor(
            consecutive_empty_runs=50,
            last_run_at=now - timedelta(hours=capped_gap),
        )
        not_ready = _cursor(
            consecutive_empty_runs=50,
            last_run_at=now - timedelta(hours=capped_gap - 1),
        )
        assert CrawlerBudgetService.should_run(ready, self.BASE_HOURS, now=now) is True
        assert (
            CrawlerBudgetService.should_run(not_ready, self.BASE_HOURS, now=now)
            is False
        )
