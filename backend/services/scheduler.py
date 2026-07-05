"""APScheduler integration — dispatches Celery tasks on cron schedules.

APScheduler only dispatches tasks; it does NOT execute work inline.
This keeps the FastAPI event loop unblocked.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from celery_app import celery_app
from config import settings

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Zurich")


def setup_schedules() -> None:
    """Register all scheduled jobs. Call once at app startup."""
    if not settings.SCHEDULER_ENABLED:
        logger.info("Scheduler disabled via SCHEDULER_ENABLED=False")
        return

    # Extracción de ofertas: cosecha diaria autónoma a hora VARIABLE (patrón
    # circadiano) o, si está desactivada, el fetch clásico por intervalos.
    if settings.SCHEDULER_DAILY_HARVEST_ENABLED:
        jitter_seconds = settings.SCHEDULER_DAILY_HARVEST_JITTER_HOURS * 3600
        scheduler.add_job(
            _dispatch_daily_harvest,
            CronTrigger(
                hour=settings.SCHEDULER_DAILY_HARVEST_HOUR,
                minute=0,
                timezone="Europe/Zurich",
                jitter=jitter_seconds,
            ),
            id="daily_harvest",
            replace_existing=True,
        )
        logger.info(
            "Cosecha diaria activa: hora base %02d:00 CET ± %dh de jitter",
            settings.SCHEDULER_DAILY_HARVEST_HOUR,
            settings.SCHEDULER_DAILY_HARVEST_JITTER_HOURS,
        )
    else:
        # Fetch clásico: API providers cada N min, scrapers cada N h.
        scheduler.add_job(
            _dispatch_fetch_providers,
            IntervalTrigger(minutes=settings.SCHEDULER_FETCH_INTERVAL_MINUTES),
            id="fetch_providers",
            replace_existing=True,
        )
        scheduler.add_job(
            _dispatch_fetch_scrapers,
            IntervalTrigger(hours=settings.SCHEDULER_SCRAPER_INTERVAL_HOURS),
            id="fetch_scrapers",
            replace_existing=True,
        )

    # Semantic dedup: daily at 04:00 CET
    scheduler.add_job(
        _dispatch_dedup_semantic,
        CronTrigger(hour=4, timezone="Europe/Zurich"),
        id="dedup_semantic",
        replace_existing=True,
    )

    # URL health check: weekly Sunday at 03:00 CET
    scheduler.add_job(
        _dispatch_check_urls,
        CronTrigger(day_of_week="sun", hour=3, timezone="Europe/Zurich"),
        id="check_job_urls",
        replace_existing=True,
    )

    # Run saved searches every N minutes
    scheduler.add_job(
        _dispatch_saved_searches,
        IntervalTrigger(minutes=settings.SCHEDULER_SEARCH_INTERVAL_MINUTES),
        id="run_saved_searches",
        replace_existing=True,
    )

    # Limpieza de ofertas caducadas: diario a las 03:30 CET (antes del dedup de las 04:00)
    scheduler.add_job(
        _dispatch_cleanup_stale,
        CronTrigger(hour=3, minute=30, timezone="Europe/Zurich"),
        id="cleanup_stale_jobs",
        replace_existing=True,
    )

    # Healthcheck de la watchlist: cada 6h
    scheduler.add_job(
        _dispatch_watchlist_health,
        IntervalTrigger(hours=6),
        id="watchlist_health",
        replace_existing=True,
    )

    # Digest diario de la watchlist: 18:00 CET (score 40-69)
    scheduler.add_job(
        _dispatch_watchlist_digest,
        CronTrigger(hour=18, timezone="Europe/Zurich"),
        id="watchlist_digest",
        replace_existing=True,
    )

    # Alerta de ofertas de profesor de primaria (Suiza) → email: cada N horas
    scheduler.add_job(
        _dispatch_teacher_alert,
        IntervalTrigger(hours=settings.SCHEDULER_TEACHER_ALERT_INTERVAL_HOURS),
        id="teacher_alert",
        replace_existing=True,
    )

    logger.info(
        "Scheduler configured: daily harvest=%s, dedup daily 04:00, "
        "URL check weekly Sun 03:00, saved searches every %d min, "
        "cleanup stale jobs daily 03:30",
        settings.SCHEDULER_DAILY_HARVEST_ENABLED,
        settings.SCHEDULER_SEARCH_INTERVAL_MINUTES,
    )


def _dispatch_daily_harvest() -> None:
    celery_app.send_task("tasks.pipeline.daily_harvest")
    logger.info("Dispatched tasks.pipeline.daily_harvest")


def _dispatch_fetch_providers() -> None:
    celery_app.send_task("tasks.fetch_providers")
    logger.debug("Dispatched tasks.fetch_providers")


def _dispatch_dedup_semantic() -> None:
    celery_app.send_task("tasks.dedup_semantic_batch")
    logger.debug("Dispatched tasks.dedup_semantic_batch")


def _dispatch_check_urls() -> None:
    celery_app.send_task("tasks.check_job_urls")
    logger.debug("Dispatched tasks.check_job_urls")


def _dispatch_saved_searches() -> None:
    celery_app.send_task("tasks.search_tasks.run_saved_searches")
    logger.debug("Dispatched tasks.search_tasks.run_saved_searches")


def _dispatch_fetch_scrapers() -> None:
    celery_app.send_task("tasks.scraping.fetch_scrapers")
    logger.debug("Dispatched tasks.scraping.fetch_scrapers")


def _dispatch_cleanup_stale() -> None:
    celery_app.send_task("tasks.cleanup_stale_jobs")
    logger.debug("Dispatched tasks.cleanup_stale_jobs")


def _dispatch_watchlist_health() -> None:
    celery_app.send_task("tasks.watchlist.check_health")
    logger.debug("Dispatched tasks.watchlist.check_health")


def _dispatch_watchlist_digest() -> None:
    celery_app.send_task("tasks.watchlist.send_digest")
    logger.debug("Dispatched tasks.watchlist.send_digest")


def _dispatch_teacher_alert() -> None:
    celery_app.send_task("tasks.alert_tasks.detect_teacher_alerts")
    logger.debug("Dispatched tasks.alert_tasks.detect_teacher_alerts")
