"""Tests for the APScheduler integration."""

from unittest.mock import patch

from services.scheduler import scheduler, setup_schedules


def _base_settings(mock) -> None:
    """Rellena el mock de settings con los valores que lee setup_schedules."""
    mock.SCHEDULER_ENABLED = True
    mock.SCHEDULER_TEACHER_ALERT_INTERVAL_HOURS = 6
    # Sin esto, el MagicMock truthy activaba el digest y CronTrigger recibia
    # un MagicMock como hour -> ValueError (regresion del commit 07a5c20).
    mock.DAILY_DIGEST_ENABLED = False


class TestScheduler:
    def test_registers_product_jobs_without_local_harvest(self):
        with patch("services.scheduler.settings") as mock_settings:
            _base_settings(mock_settings)

            scheduler.remove_all_jobs()
            setup_schedules()

            job_ids = [j.id for j in scheduler.get_jobs()]
            assert "daily_harvest" not in job_ids
            assert "fetch_providers" not in job_ids
            assert "fetch_scrapers" not in job_ids
            assert "check_job_urls" in job_ids
            assert "cleanup_stale_jobs" in job_ids
            assert "resolve_pending_languages" in job_ids
            assert "watchlist_health" in job_ids
            assert "watchlist_digest" in job_ids
            assert "teacher_alert" in job_ids

            scheduler.remove_all_jobs()

    def test_scheduler_disabled(self):
        """When SCHEDULER_ENABLED=False, no jobs are registered."""
        with patch("services.scheduler.settings") as mock_settings:
            mock_settings.SCHEDULER_ENABLED = False

            scheduler.remove_all_jobs()
            setup_schedules()

            assert len(scheduler.get_jobs()) == 0

            scheduler.remove_all_jobs()
