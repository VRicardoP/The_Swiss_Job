"""Tareas de mantenimiento del core (cola core.default)."""

import asyncio
import logging
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from kombu.exceptions import ChannelError

from jobhunt_core import runs
from jobhunt_core.archive import archive_sweep
from jobhunt_core.celery_app import celery_app
from jobhunt_core.database import task_session_factory
from jobhunt_core.config import settings
from jobhunt_core.dedup import scan_semantic_candidates_batch
from jobhunt_core.dedup_merge import load_rejected, merge_batch
from jobhunt_core.retention import purge_retention

logger = logging.getLogger(__name__)


@celery_app.task(name="jobhunt.maintenance.archive_sweep")
def archive_sweep_task() -> dict:
    """Barrido de archivado ADR-07 (ver jobhunt_core/archive.py)."""
    return asyncio.run(_run(archive_sweep))


@celery_app.task(name="jobhunt.maintenance.dedup_lex_backfill")
def dedup_lex_backfill_task() -> dict:
    """One-shot post-deploy del Track R (P1-1 revisión): backfill LÉXICO
    completo — el beat solo cubre 26 h y el corpus antiguo (holdout incl.)
    jamás recibiría candidatos léxicos. Lanzar UNA vez tras desplegar."""
    from jobhunt_core.dedup import lexical_backfill

    # asyncio.run OBLIGATORIO (re-confirmación Track R, P1-A: sin él la
    # tarea devolvía la CORRUTINA sin ejecutar y el backfill nunca corría).
    return {"candidatos": asyncio.run(_run(lexical_backfill))}


@celery_app.task(name="jobhunt.maintenance.dedup_revalidate_by_rule")
def dedup_revalidate_by_rule_task(apply: bool = False) -> dict:
    """One-shot AUDITABLE (revisión FASE 2 P2-1): revalida los candidatos
    pendientes contra la regla de ubicación ratificada. Por defecto PREVIEW
    (cuenta + hash de ids, sin escribir); con apply=True aplica con
    procedencia y devuelve el mismo resumen para comparar."""
    from functools import partial

    from jobhunt_core.dedup import revalidate_pending_candidates

    return asyncio.run(_run(partial(revalidate_pending_candidates, apply=apply)))


@celery_app.task(name="jobhunt.maintenance.translate_titles")
def translate_titles_task() -> dict:
    """Títulos al inglés, una vez por título (D10, jobhunt_core/title_translation.py)."""
    return asyncio.run(_translate_titles_impl())


async def _translate_titles_impl() -> dict:
    from jobhunt_core.title_translation import run_pass

    async with task_session_factory() as factory:
        return await run_pass(factory)


@celery_app.task(name="jobhunt.maintenance.purge_retention")
def purge_retention_task() -> dict:
    """Retención de las tablas de trabajo terminado (O-4, jobhunt_core/retention.py)."""
    return asyncio.run(_run(purge_retention))


def _aware_timestamp(value: str, field: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"{field} debe incluir zona horaria")
    return parsed


async def _run_dedup_fragment(
    window_start: str | None = None,
    window_end: str | None = None,
    after_created_at: str | None = None,
    after_id: str | None = None,
) -> dict:
    if (window_start is None) != (window_end is None):
        raise ValueError("ventana dedup incompleta")
    if (after_created_at is None) != (after_id is None):
        raise ValueError("cursor dedup incompleto")

    if window_end is None:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=int(settings.CORE_DEDUP_SCAN_WINDOW_H))
    else:
        start = _aware_timestamp(window_start, "window_start")
        end = _aware_timestamp(window_end, "window_end")
    cursor_at = (
        _aware_timestamp(after_created_at, "after_created_at")
        if after_created_at is not None
        else None
    )
    cursor_id = uuid.UUID(after_id) if after_id is not None else None

    async with task_session_factory() as factory:
        async with factory() as session:
            result = await scan_semantic_candidates_batch(
                session,
                window_start=start,
                window_end=end,
                after_created_at=cursor_at,
                after_id=cursor_id,
            )
            await session.commit()
    return {
        **result,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
    }


@celery_app.task(name="jobhunt.maintenance.dedup_scan", bind=True, max_retries=3)
def dedup_scan_task(
    self,
    window_start: str | None = None,
    window_end: str | None = None,
    after_created_at: str | None = None,
    after_id: str | None = None,
    merge_deadline: str | None = None,
) -> dict:
    """Persist one bounded dedup fragment and enqueue its continuation.

    Con `merge_deadline` (lo pone `dedup_nightly`), el ÚLTIMO fragmento encola
    la fusión: los candidatos de la noche entran en la misma pasada.
    """
    try:
        result = asyncio.run(
            _run_dedup_fragment(
                window_start,
                window_end,
                after_created_at,
                after_id,
            )
        )
        cursor = result.get("next_cursor")
        extra = {"merge_deadline": merge_deadline} if merge_deadline else {}
        if cursor is not None:
            dedup_scan_task.apply_async(
                kwargs={
                    "window_start": result["window_start"],
                    "window_end": result["window_end"],
                    "after_created_at": cursor["created_at"],
                    "after_id": cursor["vacancy_id"],
                    **extra,
                },
                countdown=1,
            )
            result["continuation_enqueued"] = True
        elif merge_deadline:
            dedup_merge_task.apply_async(
                kwargs={"deadline": merge_deadline}, countdown=1
            )
            result["merge_enqueued"] = True
        return result
    except Exception as exc:
        logger.error("dedup_scan fragment failed: %s", exc)
        raise self.retry(exc=exc, countdown=60) from exc


# D2 (2026-11-26): la pasada diaria corre DESPUÉS de la última cosecha del día
# (24 h a las 23:30, y la semanal de los lunes a las 23:45) y termina ANTES de
# que vuelva la de 6 h a las 01:30.
ZURICH = ZoneInfo("Europe/Zurich")
DEDUP_NIGHTLY_DEADLINE = time(1, 20)
# Tope de un lanzamiento fuera de hora (a mano): no se come las cosechas del día.
DEDUP_NIGHTLY_MAX = timedelta(hours=1)
DEDUP_HARVEST_POLL_S = 600
# Cada fragmento de fusión cabe con holgura bajo task_soft_time_limit (1800 s).
DEDUP_MERGE_FRAGMENT_S = 300
HARVEST_QUEUE = "core.harvest"


def nightly_deadline(now: datetime) -> datetime:
    """Próximas 01:20 de Zurich, como mucho una hora después de `now`."""
    local = now.astimezone(ZURICH)
    cut = datetime.combine(local.date(), DEDUP_NIGHTLY_DEADLINE, tzinfo=ZURICH)
    if cut <= local:
        cut += timedelta(days=1)
    return min(cut, now + DEDUP_NIGHTLY_MAX)


def harvest_queue_depth() -> int:
    """Mensajes esperando en `core.harvest`.

    Un scope encolado y aún no reclamado no tiene fila en `source_harvest_runs`:
    mirar sólo la tabla daría «cosecha terminada» con la mitad sin empezar.
    """
    with celery_app.connection_for_read() as conn:
        try:
            return conn.default_channel.queue_declare(
                queue=HARVEST_QUEUE, passive=True
            ).message_count
        except ChannelError:
            return 0  # cola nunca declarada: nadie ha encolado nada


async def _harvest_busy() -> bool:
    if harvest_queue_depth() > 0:
        return True
    async with task_session_factory() as factory:
        async with factory() as session:
            return await runs.any_scope_running(session)


@celery_app.task(name="jobhunt.maintenance.dedup_nightly", bind=True, max_retries=3)
def dedup_nightly_task(self, deadline: str | None = None) -> dict:
    """Escaneo de candidatos + fusión, cuando la cosecha ha terminado.

    Si aún hay cosecha, se reprograma cada `DEDUP_HARVEST_POLL_S`; pasada la
    hora límite, la noche se salta con un ERROR en vez de solaparse con la
    cosecha de las 01:30.
    """
    now = datetime.now(timezone.utc)
    limit = (
        _aware_timestamp(deadline, "deadline")
        if deadline is not None
        else nightly_deadline(now)
    )
    try:
        busy = asyncio.run(_harvest_busy())
    except Exception as exc:
        logger.error("dedup_nightly: no se pudo comprobar la cosecha: %s", exc)
        raise self.retry(exc=exc, countdown=60) from exc
    if busy:
        if now + timedelta(seconds=DEDUP_HARVEST_POLL_S) < limit:
            dedup_nightly_task.apply_async(
                kwargs={"deadline": limit.isoformat()},
                countdown=DEDUP_HARVEST_POLL_S,
            )
            return {"status": "esperando_cosecha", "deadline": limit.isoformat()}
        logger.error(
            "dedup_nightly: la cosecha sigue en curso a la hora límite %s — "
            "no hay dedup esta noche",
            limit.isoformat(),
        )
        return {"status": "saltada_cosecha_en_curso", "deadline": limit.isoformat()}
    dedup_scan_task.apply_async(kwargs={"merge_deadline": limit.isoformat()})
    return {"status": "escaneo_encolado", "deadline": limit.isoformat()}


async def _run_merge_fragment(deadline: datetime, after_id: str | None) -> dict:
    """Lotes de fusión, cada uno en su transacción, hasta agotar el tiempo."""
    stop = min(
        deadline, datetime.now(timezone.utc) + timedelta(seconds=DEDUP_MERGE_FRAGMENT_S)
    )
    totals = {"merged": 0, "collapsed": 0, "vetoed": 0, "transfers": 0, "lotes": 0}
    cursor = uuid.UUID(after_id) if after_id is not None else None
    rejected = None
    async with task_session_factory() as factory:
        while True:
            async with factory() as session:
                if rejected is None:
                    rejected = await load_rejected(session)
                result = await merge_batch(session, after_id=cursor, rejected=rejected)
                await session.commit()
            for key in ("merged", "collapsed", "vetoed", "transfers"):
                totals[key] += result[key]
            totals["lotes"] += 1
            cursor = result["next_after"]
            if result["complete"] or datetime.now(timezone.utc) >= stop:
                break
    return {
        **totals,
        "complete": result["complete"],
        "next_after": None if result["complete"] else str(cursor),
    }


@celery_app.task(name="jobhunt.maintenance.dedup_merge", bind=True, max_retries=3)
def dedup_merge_task(self, deadline: str, after_id: str | None = None) -> dict:
    """Fusión de duplicados por regla (D2). Reanudable: lo fusionado ya no es
    `pending`, así que cortar en cualquier punto sólo aplaza el resto."""
    if not settings.CORE_DEDUP_MERGE_ENABLED:
        return {"status": "deshabilitada"}
    limit = _aware_timestamp(deadline, "deadline")
    if datetime.now(timezone.utc) >= limit:
        return {"status": "fuera_de_hora", "deadline": deadline}
    try:
        result = asyncio.run(_run_merge_fragment(limit, after_id))
    except Exception as exc:
        logger.error("dedup_merge fragment failed: %s", exc)
        raise self.retry(exc=exc, countdown=60) from exc
    if not result["complete"]:
        if datetime.now(timezone.utc) < limit:
            dedup_merge_task.apply_async(
                kwargs={"deadline": deadline, "after_id": result["next_after"]},
                countdown=1,
            )
            result["continuation_enqueued"] = True
        else:
            logger.info("dedup_merge: hora límite; sigue la noche siguiente")
    logger.info("dedup_merge: %s", result)
    return result


async def _run(fn) -> dict:
    async with task_session_factory() as factory:
        async with factory() as session:
            result = await fn(session)
            await session.commit()
            return result
