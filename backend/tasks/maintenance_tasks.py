"""Celery task: refresh-token purge.

Hasta DT-148 este módulo tenía además `check_job_urls` (hasta 3.000 HEAD
nocturnos contra los portales) y `cleanup_stale_jobs` (borrado a 60 días).
Las dos trabajaban sobre la tabla legacy `jobs`, que desde el corte de la
cosecha nativa (15-10-2026) no tiene ningún escritor: sondear un archivo
congelado sólo podía ganarnos bloqueos, y la limpieza habría empezado a borrar
en masa TODAS sus filas a los 60 días del corte. `jobs` queda congelada tal
cual, como archivo de solo lectura para el overlay local.
"""

import asyncio
import logging
from typing import Any

from celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.purge_refresh_tokens", bind=True, max_retries=1)
def purge_refresh_tokens(self) -> dict[str, Any]:
    """Borra las filas de refresh que ya no pueden presentarse.

    La política de sesiones (T9) conserva las filas revocadas A PROPÓSITO: son
    la memoria que distingue «token reutilizado» de «token desconocido». La
    purga va por CADUCIDAD y vive en `token_store.purgar_caducados`, que
    existía desde T9 y a la que NADIE llamaba: `refresh_tokens` crecía una
    fila por rotación y no decrecía jamás (DT-148 A3).
    """
    try:
        return asyncio.run(_purge_refresh_tokens_async())
    except Exception as exc:
        # Nunca el texto: una excepción de BD puede arrastrar la URL con
        # credencial (misma disciplina que profile_sync).
        logger.error("purge_refresh_tokens failed: %s", type(exc).__name__)
        raise self.retry(exc=exc, countdown=300)


async def _purge_refresh_tokens_async() -> dict[str, Any]:
    from database import task_session
    from services.token_store import purgar_caducados

    async with task_session() as db:
        borrados = await purgar_caducados(db)
        await db.commit()
    logger.info("purge_refresh_tokens: %d filas caducadas borradas", borrados)
    return {"status": "success", "deleted": borrados}
