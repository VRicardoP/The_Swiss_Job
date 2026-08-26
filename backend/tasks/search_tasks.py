"""Celery tasks: execute saved searches and dispatch notifications.

DECISIÓN D.1 (gate anti-doble-motor, §15bis): esta tarea NO se gatea. Las
búsquedas guardadas se resuelven contra el CORPUS de ofertas legacy —que se
sigue cosechando mientras quede algún perfil local—, no contra `match_results`,
así que no se quedan viejas al migrar un perfil; y la capacidad `saved_searches`
se sirve de local en TODOS los modos (el /v1 del core no la expone), luego el
core no puede duplicar este aviso. Revisar cuando el core la asuma.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.search_tasks.run_saved_searches",
    bind=True,
    max_retries=1,
)
def run_saved_searches(self) -> dict[str, Any]:
    """Run all active saved searches whose schedule is due."""
    try:
        return asyncio.run(_run_saved_searches_async())
    except Exception as exc:
        logger.error("run_saved_searches failed: %s", exc)
        raise self.retry(exc=exc, countdown=120)


async def _run_saved_searches_async() -> dict[str, Any]:
    """Async implementation: find due searches, run matching, create notifications."""
    from sqlalchemy import select

    from config import settings
    from database import task_session
    from models.enums import NotifyFrequency
    from models.saved_search import SavedSearch

    now = datetime.now(timezone.utc)
    processed = 0
    total_matches = 0
    failed = 0

    async with task_session() as db:
        # Solo los IDs: cada búsqueda se recarga dentro del bucle. Tras un
        # rollback (G3/P2-8) los objetos ORM quedan expirados y tocarlos
        # dispararía IO implícita — recargar por id mantiene el barrido vivo.
        stmt = select(SavedSearch.id).where(
            SavedSearch.is_active.is_(True),
        )
        search_ids = list((await db.execute(stmt)).scalars().all())

        for search_id in search_ids:
            search = await db.get(SavedSearch, search_id)
            if search is None:
                continue
            # Check if search is due based on frequency
            if search.last_run_at:
                if search.notify_frequency == NotifyFrequency.realtime:
                    interval = timedelta(minutes=5)
                elif search.notify_frequency == NotifyFrequency.daily:
                    interval = timedelta(hours=24)
                elif search.notify_frequency == NotifyFrequency.weekly:
                    interval = timedelta(weeks=1)
                else:
                    continue

                if now - search.last_run_at < interval:
                    continue

            # G3/P2-8: sin esta guarda, un `filters` hostil (o simplemente mal
            # tipado: `{"source": 123}`) de UN usuario lanzaba, la excepción
            # salía del bucle, el retry tropezaba con la MISMA fila y todas las
            # búsquedas POSTERIORES —de otros usuarios— no se ejecutaban nunca.
            try:
                matches = await _execute_single_search(db, search, settings)
            except Exception:
                logger.exception(
                    "Saved search %s (usuario %s) falló; se omite y sigue el barrido",
                    search.id,
                    search.user_id,
                )
                await db.rollback()
                failed += 1
                continue
            total_matches += matches
            processed += 1

    return {
        "status": "success",
        "searches_processed": processed,
        "total_matches": total_matches,
        "failed": failed,
    }


async def _execute_single_search(db, search, settings) -> int:
    """Execute a single saved search and create notifications if matches found."""
    from datetime import datetime, timedelta, timezone

    from models.notification import Notification
    from models.job import Job
    from sqlalchemy import select, func

    from tasks.watermarks import watermark_lag

    now = datetime.now(timezone.utc)
    filters = search.filters or {}

    # Build query from filters
    conditions = [Job.is_active.is_(True), Job.duplicate_of.is_(None)]

    if filters.get("source"):
        sources = [s.strip() for s in filters["source"].split(",") if s.strip()]
        if sources:
            conditions.append(Job.source.in_(sources))

    if filters.get("canton"):
        cantons = [c.strip().upper() for c in filters["canton"].split(",") if c.strip()]
        if cantons:
            conditions.append(Job.canton.in_(cantons))

    if filters.get("remote_only"):
        conditions.append(Job.remote.is_(True))

    if filters.get("language"):
        conditions.append(Job.language == filters["language"])

    # Solo ofertas NUEVAS desde el último run (G1/P1-4): `last_seen_at` lo
    # refresca el upsert cada día para toda oferta aún listada (mecanismo
    # anti-archivado), así que con él match_count ≈ todo el corpus activo que
    # casa, CADA run — «Found 800 new jobs» diarios sin ningún alta real.
    # `first_seen_at` es la fecha de ALTA: eso sí mide novedad.
    if search.last_run_at:
        # G3/P1-1 — LAG de solape: `first_seen_at` lo pone el server_default de
        # Postgres, que hasta esta corrección era `now()` = INICIO de la
        # transacción de cosecha. Una cosecha de varios minutos daba de alta
        # ofertas fechadas ANTES del `last_run_at` que este barrido ya había
        # guardado: no las contaba nunca más. Con el retroceso, la ventana
        # solapa; el precio es que una oferta del borde puede contarse dos
        # veces (aquí la salida es un CONTEO, no un envío: es el lado barato).
        floor = search.last_run_at - watermark_lag()
        conditions.append(Job.first_seen_at > floor)
    else:
        # G3/P3-2 — corrida de ESTRENO: sin cota inferior, la primera pasada
        # contaba el corpus activo entero como «nuevo» y notificaba ofertas de
        # hace meses. La cota superior sí era incondicional.
        conditions.append(
            Job.first_seen_at
            > now - timedelta(days=settings.SAVED_SEARCH_INITIAL_LOOKBACK_DAYS)
        )
    # G2/P3-5: cota superior — una oferta insertada entre la captura de `now`
    # y la query entraba en ESTE run y en el SIGUIENTE (>= last_run_at == now):
    # notificación y total_matches duplicados. Misma marca de agua que los
    # fixes hermanos de G1/P2-15 en alert_tasks y el digest de watchlist.
    conditions.append(Job.first_seen_at <= now)

    stmt = select(func.count()).select_from(Job).where(*conditions)
    match_count = (await db.execute(stmt)).scalar_one()

    # Update search metadata
    search.last_run_at = now
    search.total_matches = (search.total_matches or 0) + match_count

    # G1/P1-4: `min_score` era un umbral de PUNTUACIÓN comparado contra un
    # CONTEO de ofertas: una búsqueda amplia notificaba todo el corpus cada
    # día y una estrecha (<50 resultados) no notificaba JAMÁS. Esta query es
    # un filtro sobre el corpus — aquí no se calcula ningún score, así que el
    # umbral de score no aplica: se notifica cuando hay novedades reales.
    notification_id = "None"
    if match_count > 0:
        # Create notification
        notification = Notification(
            user_id=search.user_id,
            event_type="new_matches",
            title=f"New matches for '{search.name}'",
            body=f"Found {match_count} new jobs matching your saved search.",
            data={
                "search_id": str(search.id),
                "search_name": search.name,
                "match_count": match_count,
            },
        )
        db.add(notification)
        # G3/P3-3: `Notification.id` lo genera SQLAlchemy en el FLUSH, no en el
        # `db.add`. Sin esto el evento SSE viajaba con
        # `notification_id: "None"` y el cliente no podía resolverla.
        await db.flush()
        notification_id = str(notification.id)

    await db.commit()

    # El evento se publica DESPUÉS del commit: publicado antes, el cliente podía
    # pedir una notificación que aún no era visible (o que un rollback anulaba).
    if match_count > 0 and search.notify_push:
        _publish_new_matches(search, match_count, notification_id)

    return match_count


def _publish_new_matches(search, match_count: int, notification_id: str) -> None:
    """Publica el evento SSE de novedades (fire-and-forget vía Redis)."""
    try:
        import json

        import redis

        from config import settings as cfg

        r = redis.from_url(cfg.REDIS_URL)
        r.publish(
            f"sse:{search.user_id}",
            json.dumps(
                {
                    "event": "new_matches",
                    "data": {
                        "search_id": str(search.id),
                        "search_name": search.name,
                        "match_count": match_count,
                        "notification_id": notification_id,
                    },
                }
            ),
        )
        r.close()
    except Exception:
        logger.warning("Failed to broadcast SSE for search %s", search.id)


@celery_app.task(
    name="tasks.search_tasks.run_single_saved_search",
    bind=True,
    max_retries=1,
)
def run_single_saved_search(self, search_id: str, user_id: str) -> dict[str, Any]:
    """Run a single saved search manually (triggered from API)."""
    try:
        return asyncio.run(_run_single_async(search_id, user_id))
    except Exception as exc:
        logger.error("run_single_saved_search failed for %s: %s", search_id, exc)
        raise self.retry(exc=exc, countdown=30)


async def _run_single_async(search_id: str, user_id: str) -> dict[str, Any]:
    """Async implementation for manual single search run."""
    import uuid as uuid_mod

    from sqlalchemy import select

    from config import settings
    from database import task_session
    from models.saved_search import SavedSearch

    uid = uuid_mod.UUID(search_id)

    async with task_session() as db:
        search = (
            await db.execute(
                select(SavedSearch).where(
                    SavedSearch.id == uid,
                    SavedSearch.user_id == uuid_mod.UUID(user_id),
                )
            )
        ).scalar_one_or_none()

        if search is None:
            return {"status": "error", "reason": "search_not_found"}

        matches = await _execute_single_search(db, search, settings)
        return {
            "status": "success",
            "search_id": search_id,
            "matches": matches,
        }
