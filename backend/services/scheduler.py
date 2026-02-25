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

    # Fetch all API providers every N minutes
    scheduler.add_job(
        _dispatch_fetch_providers,
        IntervalTrigger(minutes=settings.SCHEDULER_FETCH_INTERVAL_MINUTES),
        id="fetch_providers",
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

    logger.info(
        "Scheduler configured: fetch every %d min, dedup daily 04:00, "
        "URL check weekly Sun 03:00",
        settings.SCHEDULER_FETCH_INTERVAL_MINUTES,
    )


def _dispatch_fetch_providers() -> None:
    celery_app.send_task("tasks.fetch_providers")
    logger.debug("Dispatched tasks.fetch_providers")


def _dispatch_dedup_semantic() -> None:
    celery_app.send_task("tasks.dedup_semantic_batch")
    logger.debug("Dispatched tasks.dedup_semantic_batch")


def _dispatch_check_urls() -> None:
    celery_app.send_task("tasks.check_job_urls")
    logger.debug("Dispatched tasks.check_job_urls")
