"""Punto 5 §10.3-ter — calentar el recorrido del feed fuera de la petición.

Recorrer el feed una vez por cambio de versión es inevitable; que lo pague el
usuario en la cara, no. En el NAS ese recorrido costaba **11 s** (18 idas y
vueltas al core; ahora 4, de 500) y lo sufría íntegro quien entrase el primero
tras un despliegue, una expulsión de caché o una cosecha.

Tres decisiones que conviene no deshacer sin medir:

- **Calienta SOLO el recorrido**, no las vistas. Las vistas dependen del overlay
  local (candidatura, urgencia, borrador) y cachearlas serviría estado rancio:
  es exactamente la regresión que cerró T2.
- **Corre en CADA worker, sin leader-lock.** La caché del recorrido vive EN
  PROCESO (`_feed_cache`), así que calentar en un worker no sirve al otro. Los
  demás bucles del BFF sí usan leader-lock porque escriben; éste sólo lee.
- **Cuesta una petición cuando no hay nada que hacer**: si la versión no ha
  cambiado, `warm_feed` devuelve de caché tras preguntar `/matches/version`
  (~0,4 s en el NAS). Con dos perfiles y 60 s de periodo es ruido.
"""

import asyncio
import logging

from sqlalchemy import select

from config import settings
from database import async_session
from models.jobhunt_profile_map import JobhuntProfileMap

logger = logging.getLogger(__name__)


async def _usuarios_enrolados(db) -> list:
    """Usuarios con vínculo de identidad en el core; sin vínculo no hay feed."""
    return list((await db.execute(select(JobhuntProfileMap.user_id))).scalars().all())


async def calentar_una_vez() -> dict[str, int]:
    """Una pasada sobre todos los enrolados. No propaga fallos por usuario."""
    from services.matching import resolve_matching

    calentados, fallos = 0, 0
    async with async_session() as db:
        usuarios = await _usuarios_enrolados(db)
    for user_id in usuarios:
        try:
            async with async_session() as db:
                backend = await resolve_matching(db, user_id)
                calentar = getattr(backend, "warm_feed", None)
                if calentar is None:
                    continue  # backend local: no hay recorrido que calentar
                if await calentar(user_id):
                    calentados += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            fallos += 1
            # Un fallo al calentar NO degrada nada: la petición siguiente
            # recorre como siempre. Se registra para que no sea invisible.
            logger.warning("calentamiento del feed fallido para %s", user_id)
    return {"usuarios": len(usuarios), "calentados": calentados, "fallos": fallos}


async def run_feed_warmup() -> None:
    """Bucle de fondo; se cancela con el resto en el apagado del lifespan."""
    if not settings.FEED_WARMUP_ENABLED:
        logger.info("calentamiento del feed DESACTIVADO (FEED_WARMUP_ENABLED)")
        return
    intervalo = settings.FEED_WARMUP_INTERVAL_SECONDS
    logger.info("calentamiento del feed activo cada %s s", intervalo)
    while True:
        try:
            resumen = await calentar_una_vez()
            if resumen["calentados"] or resumen["fallos"]:
                logger.info("calentamiento del feed: %s", resumen)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("calentamiento del feed: pasada fallida")
        await asyncio.sleep(intervalo)
