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
            try:
                setup_schedules()
                scheduler.start()
            except Exception:
                # A20-16: el cerrojo acaba de tomarse con NX, es nuestro. Si el
                # scheduler no arranca, retenerlo dejaba a TODOS los workers
                # sin despachar hasta que caducara el TTL.
                await r.delete(_LEADER_KEY)
                raise
            logger.info("Scheduler LÍDER (%s): jobs programados", _WORKER_ID)
            return True
        return False

    # Ya somos líderes: renovar mientras el lock siga siendo nuestro.
    try:
        current = await r.get(_LEADER_KEY)
        renewed = current == _WORKER_ID and bool(
            await r.expire(_LEADER_KEY, _LEADER_TTL)
        )
    except Exception as exc:
        # G1/P3-18: una renovación que LANZA (hipo de Redis) dejaba
        # is_leader=True en el bucle mientras el lock expiraba por TTL →
        # ~40s con DOS schedulers y doble daily_harvest. Conservador: ante
        # la duda se CEDE el liderazgo (nadie puede adquirir el lock hasta
        # que expire, así que no hay doble líder; como mucho un hueco ≤ TTL
        # hasta la re-elección).
        logger.warning(
            "Renovación del leader-lock falló (%s); se cede el liderazgo", exc
        )
        if scheduler.running:
            scheduler.shutdown(wait=False)
        return False

    if renewed:
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

    # DT-148: aquí vivían el chequeo de URLs (03:00) y la limpieza de
    # caducadas (03:30). Las dos operaban sobre la tabla legacy `jobs`, sin
    # escritores desde el corte de la cosecha nativa: la primera sondeaba
    # 3.000 URLs por noche de un archivo congelado y la segunda habría
    # borrado en masa todas sus filas a los 60 días. Retiradas.

    # Purga de refresh caducados: diaria a las 03:45 CET. La política T9
    # conserva las filas REVOCADAS (memoria del robo) y borra por caducidad;
    # sin esta cita nadie llamaba a `purgar_caducados` (DT-148 A3).
    scheduler.add_job(
        _dispatch_purge_refresh_tokens,
        CronTrigger(hour=3, minute=45, timezone="Europe/Zurich"),
        id="purge_refresh_tokens",
        replace_existing=True,
    )

    # Idioma de los títulos encolados: cada 5 min, en lotes acotados.
    # Deliberadamente frecuente y pequeño: lo que importa no es resolver rápido
    # sino que la detección NUNCA vuelva al camino de una petición (punto 5).
    scheduler.add_job(
        _dispatch_resolve_languages,
        IntervalTrigger(minutes=5),
        id="resolve_pending_languages",
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
        "Scheduler configured: refresh purge 03:45, languages, watchlist, alert"
    )


def _dispatch_purge_refresh_tokens() -> None:
    celery_app.send_task("tasks.purge_refresh_tokens")
    logger.debug("Dispatched tasks.purge_refresh_tokens")


def _dispatch_resolve_languages() -> None:
    celery_app.send_task("tasks.language_tasks.resolve_pending_languages")
    logger.debug("Dispatched tasks.language_tasks.resolve_pending_languages")


def _dispatch_watchlist_health() -> None:
    celery_app.send_task("tasks.watchlist.check_health")
    logger.debug("Dispatched tasks.watchlist.check_health")


def _dispatch_watchlist_digest() -> None:
    celery_app.send_task("tasks.watchlist.send_digest")
    logger.debug("Dispatched tasks.watchlist.send_digest")


def _dispatch_teacher_alert() -> None:
    celery_app.send_task("tasks.alert_tasks.detect_teacher_alerts")
    logger.debug("Dispatched tasks.alert_tasks.detect_teacher_alerts")
