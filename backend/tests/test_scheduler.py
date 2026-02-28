"""Tests for the APScheduler integration."""

from unittest.mock import patch

from services.scheduler import scheduler, setup_schedules


class TestScheduler:
    def test_setup_schedules_registers_jobs(self):
        """Verify all expected jobs are registered after setup."""
        with patch("services.scheduler.settings") as mock_settings:
            mock_settings.SCHEDULER_ENABLED = True
            mock_settings.SCHEDULER_FETCH_INTERVAL_MINUTES = 30
            mock_settings.SCHEDULER_SEARCH_INTERVAL_MINUTES = 60
            mock_settings.SCHEDULER_SCRAPER_INTERVAL_HOURS = 6

            # Clear any existing jobs
            scheduler.remove_all_jobs()
            setup_schedules()

            job_ids = [j.id for j in scheduler.get_jobs()]
            assert "fetch_providers" in job_ids
            assert "dedup_semantic" in job_ids
            assert "check_job_urls" in job_ids
            assert "run_saved_searches" in job_ids
            assert "fetch_scrapers" in job_ids

            scheduler.remove_all_jobs()

    def test_fetch_interval_matches_config(self):
        """Verify fetch_providers uses the configured interval."""
        with patch("services.scheduler.settings") as mock_settings:
            mock_settings.SCHEDULER_ENABLED = True
            mock_settings.SCHEDULER_FETCH_INTERVAL_MINUTES = 45
            mock_settings.SCHEDULER_SEARCH_INTERVAL_MINUTES = 60
            mock_settings.SCHEDULER_SCRAPER_INTERVAL_HOURS = 6

            scheduler.remove_all_jobs()
            setup_schedules()

            job = scheduler.get_job("fetch_providers")
            assert job is not None
            # IntervalTrigger stores interval as timedelta
            assert job.trigger.interval.total_seconds() == 45 * 60

            scheduler.remove_all_jobs()

    def test_scheduler_disabled(self):
        """When SCHEDULER_ENABLED=False, no jobs are registered."""
        with patch("services.scheduler.settings") as mock_settings:
            mock_settings.SCHEDULER_ENABLED = False

            scheduler.remove_all_jobs()
            setup_schedules()

            assert len(scheduler.get_jobs()) == 0
