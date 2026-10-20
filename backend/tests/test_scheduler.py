"""Tests for the APScheduler integration."""

from unittest.mock import patch

from services.scheduler import scheduler, setup_schedules


def _base_settings(mock) -> None:
    """Rellena el mock de settings con los valores que lee setup_schedules."""
    mock.SCHEDULER_ENABLED = True
    mock.SCHEDULER_TEACHER_ALERT_INTERVAL_HOURS = 6


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
            # DT-148: las dos tareas sobre la tabla legacy `jobs` se retiraron.
            assert "check_job_urls" not in job_ids
            assert "cleanup_stale_jobs" not in job_ids
            assert "daily_digest" not in job_ids
            assert "resolve_pending_languages" in job_ids
            assert "watchlist_health" in job_ids
            assert "watchlist_digest" in job_ids
            assert "teacher_alert" in job_ids
            # DT-148 A3: `purgar_caducados` existía desde T9 y no la llamaba
            # nadie — `refresh_tokens` crecía una fila por rotación.
            assert "purge_refresh_tokens" in job_ids

            scheduler.remove_all_jobs()

    def test_cada_nombre_despachado_existe_en_el_registro(self):
        """El scheduler despacha por NOMBRE: una errata no falla al registrar,
        falla en silencio en el broker. Se comprueban los ocho de una vez."""
        import re
        from pathlib import Path

        import tasks.alert_tasks  # noqa: F401
        import tasks.language_tasks  # noqa: F401
        import tasks.maintenance_tasks  # noqa: F401
        import tasks.watchlist_tasks  # noqa: F401
        from celery_app import celery_app

        fuente = Path(__file__).resolve().parents[1] / "services" / "scheduler.py"
        nombres = set(re.findall(r'send_task\("([^"]+)"', fuente.read_text()))
        assert nombres, "no se encontró ningún send_task en el scheduler"
        assert nombres <= set(celery_app.tasks), sorted(nombres - set(celery_app.tasks))

    def test_scheduler_disabled(self):
        """When SCHEDULER_ENABLED=False, no jobs are registered."""
        with patch("services.scheduler.settings") as mock_settings:
            mock_settings.SCHEDULER_ENABLED = False

            scheduler.remove_all_jobs()
            setup_schedules()

            assert len(scheduler.get_jobs()) == 0

            scheduler.remove_all_jobs()
