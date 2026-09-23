"""Celery: resolver el idioma de los títulos encolados, FUERA de la respuesta.

El indicador de idioma de la UI costaba 50,1 ms por oferta servida y ~90 s por
petición (punto 5). Aquí se paga una vez por título distinto, en segundo plano
y de forma acotada; el router sólo LEE lo ya resuelto.

Patrón `def task(): asyncio.run(_impl())` — Celery no es async.

El lote es acotado a propósito: el NAS tiene dos núcleos compartidos con el
resto de servicios y esta tarea no tiene ninguna urgencia. Lo que importa no es
resolver rápido sino **no volver a resolver nunca lo mismo**.
"""

import asyncio
import logging
from typing import Any

from celery_app import celery_app

logger = logging.getLogger(__name__)

# ~500 x 50,1 ms = 25 s de detección por pasada, con la cadencia del scheduler.
DEFAULT_BATCH = 500


@celery_app.task(
    name="tasks.language_tasks.resolve_pending_languages", bind=True, max_retries=1
)
def resolve_pending_languages(self, batch: int = DEFAULT_BATCH) -> dict[str, Any]:
    """Resuelve un lote de títulos pendientes y lo persiste."""
    try:
        return asyncio.run(_resolve(batch))
    except Exception as exc:
        logger.error("resolve_pending_languages failed: %s", exc)
        raise self.retry(exc=exc, countdown=300)


async def _resolve(batch: int) -> dict[str, Any]:
    from database import task_session
    from services import language_store
    from services.translation_service import TranslationService

    async with task_session() as db:
        titulos = await language_store.pending_titles(db, batch)
        if not titulos:
            return {"resolved": 0, "pending_left": 0}
        # La detección es lo ÚNICO caro de esta tarea y es CPU pura: se hace
        # aquí, en el worker, nunca en el camino de una petición.
        resueltos = {t: (TranslationService._detect_language(t) or "") for t in titulos}
        await language_store.store_resolved(db, resueltos)
        quedan = await language_store.pending_count(db)

    desconocidos = sum(1 for v in resueltos.values() if not v)
    logger.info(
        "resolve_pending_languages: %d resueltos (%d desconocidos), %d pendientes",
        len(resueltos),
        desconocidos,
        quedan,
    )
    return {"resolved": len(resueltos), "unknown": desconocidos, "pending_left": quedan}
