"""APScheduler integration — dispatches Celery tasks on cron schedules.

APScheduler only dispatches tasks; it does NOT execute work inline.
This keeps the FastAPI event loop unblocked.
"""

import asyncio
import logging
import os
import socket

import redis.asyncio as aioredis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from celery_app import celery_app
from config import settings

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Zurich")

# --- Leader-lock: el scheduler corre en UN SOLO proceso -----------------------
# Con varios workers de gunicorn, cada uno ejecuta el lifespan → sin gate se
# arrancarían N schedulers y cada job se dispararía N veces (doble cosecha, doble
# matching...). El lock de líder en Redis garantiza que solo un proceso programa.
_LEADER_KEY = "swissjob:scheduler:leader"
_LEADER_TTL = 60  # segundos de vida del lock
_RENEW_EVERY = 20  # cada cuánto renueva/reintenta la elección
_WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


async def _leader_step(r, is_leader: bool) -> bool:
    """Un paso de elección: adquiere o renueva el lock. Devuelve si somos líder.

    Extraído para poder testear la lógica sin el bucle infinito.
    """
    if not is_leader:
        acquired = await r.set(_LEADER_KEY, _WORKER_ID, nx=True, ex=_LEADER_TTL)
        if acquired:
            setup_schedules()
            scheduler.start()
            logger.info("Scheduler LÍDER (%s): jobs programados", _WORKER_ID)
            return True
        return False

    # Ya somos líderes: renovar mientras el lock siga siendo nuestro.
    current = await r.get(_LEADER_KEY)
    if current == _WORKER_ID:
        await r.expire(_LEADER_KEY, _LEADER_TTL)
        return True

    # Perdimos el liderazgo (caso raro): parar y dejar que otro releve.
    logger.warning("Scheduler perdió el liderazgo; deteniendo scheduler")
    if scheduler.running:
        scheduler.shutdown(wait=False)
    return False


async def run_scheduler_with_leader_lock() -> None:
    """Arranca el scheduler solo en el proceso que gana el lock de líder.

    Renueva el lock mientras vive y reintenta la elección si el líder cae (el lock
    expira por TTL), de modo que el scheduling se recupera sin reiniciar el
    contenedor. Bucle cancelable en el shutdown del lifespan.
    """
    if not settings.SCHEDULER_ENABLED:
        logger.info("Scheduler disabled via SCHEDULER_ENABLED=False")
        return

    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    is_leader = False
    try:
        while True:
            try:
                is_leader = await _leader_step(r, is_leader)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # Redis caído, etc. → reintentar, no morir.
                logger.warning("Leader-lock loop error: %s", exc)
            await asyncio.sleep(_RENEW_EVERY)
    except asyncio.CancelledError:
        pass
    finally:
        try:
            if is_leader:
                if scheduler.running:
                    scheduler.shutdown(wait=False)
                # Liberar el lock si sigue siendo nuestro → relevo inmediato.
                current = await r.get(_LEADER_KEY)
                if current == _WORKER_ID:
                    await r.delete(_LEADER_KEY)
        except Exception:
            pass
        await r.aclose()


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
