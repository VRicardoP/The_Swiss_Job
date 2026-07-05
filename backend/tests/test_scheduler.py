"""Tests for the APScheduler integration."""

from unittest.mock import patch

from services.scheduler import scheduler, setup_schedules


def _base_settings(mock, *, daily_harvest: bool) -> None:
    """Rellena el mock de settings con los valores que lee setup_schedules."""
    mock.SCHEDULER_ENABLED = True
    mock.SCHEDULER_DAILY_HARVEST_ENABLED = daily_harvest
    mock.SCHEDULER_DAILY_HARVEST_HOUR = 12
    mock.SCHEDULER_DAILY_HARVEST_JITTER_HOURS = 4
    mock.SCHEDULER_FETCH_INTERVAL_MINUTES = 30
    mock.SCHEDULER_SEARCH_INTERVAL_MINUTES = 60
    mock.SCHEDULER_SCRAPER_INTERVAL_HOURS = 6
    mock.SCHEDULER_TEACHER_ALERT_INTERVAL_HOURS = 6


class TestScheduler:
    def test_daily_harvest_mode_registers_harvest_not_interval_fetch(self):
        """Con la cosecha diaria activa se registra daily_harvest y NO el fetch
        por intervalos; los trabajos siempre-activos siguen presentes."""
        with patch("services.scheduler.settings") as mock_settings:
            _base_settings(mock_settings, daily_harvest=True)

            scheduler.remove_all_jobs()
            setup_schedules()

            job_ids = [j.id for j in scheduler.get_jobs()]
            assert "daily_harvest" in job_ids
            assert "fetch_providers" not in job_ids
            assert "fetch_scrapers" not in job_ids
            # Siempre activos, independientes de la cosecha
            assert "dedup_semantic" in job_ids
            assert "check_job_urls" in job_ids
            assert "run_saved_searches" in job_ids
            assert "teacher_alert" in job_ids

            scheduler.remove_all_jobs()

    def test_daily_harvest_trigger_has_jitter(self):
        """El trigger de la cosecha lleva jitter = N horas en segundos (hora variable)."""
        with patch("services.scheduler.settings") as mock_settings:
            _base_settings(mock_settings, daily_harvest=True)

            scheduler.remove_all_jobs()
            setup_schedules()

            job = scheduler.get_job("daily_harvest")
            assert job is not None
            assert job.trigger.jitter == 4 * 3600

            scheduler.remove_all_jobs()

    def test_legacy_interval_mode_registers_fetch_jobs(self):
        """Con la cosecha desactivada vuelve el fetch clásico por intervalos."""
        with patch("services.scheduler.settings") as mock_settings:
            _base_settings(mock_settings, daily_harvest=False)
            mock_settings.SCHEDULER_FETCH_INTERVAL_MINUTES = 45

            scheduler.remove_all_jobs()
            setup_schedules()

            job_ids = [j.id for j in scheduler.get_jobs()]
            assert "daily_harvest" not in job_ids
            assert "fetch_providers" in job_ids
            assert "fetch_scrapers" in job_ids

            job = scheduler.get_job("fetch_providers")
            assert job.trigger.interval.total_seconds() == 45 * 60

            scheduler.remove_all_jobs()

    def test_scheduler_disabled(self):
        """When SCHEDULER_ENABLED=False, no jobs are registered."""
        with patch("services.scheduler.settings") as mock_settings:
            mock_settings.SCHEDULER_ENABLED = False

            scheduler.remove_all_jobs()
            setup_schedules()

            assert len(scheduler.get_jobs()) == 0

            scheduler.remove_all_jobs()
