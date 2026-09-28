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


async def calentar_una_vez(redis=None) -> dict[str, int]:
    """Una pasada sobre todos los enrolados. No propaga fallos por usuario."""
    from services.matching import resolve_matching

    calentados, fallos, traducidos = 0, 0, 0
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
                traducidos += await _traducir_en_fondo(db, backend, user_id, redis)
        except asyncio.CancelledError:
            raise
        except Exception:
            fallos += 1
            # Un fallo al calentar NO degrada nada: la petición siguiente
            # recorre como siempre. Se registra para que no sea invisible.
            logger.warning("calentamiento del feed fallido para %s", user_id)
    return {
        "usuarios": len(usuarios),
        "calentados": calentados,
        "fallos": fallos,
        "traducidos": traducidos,
    }


async def _traducir_en_fondo(db, backend, user_id, redis) -> int:
    """A19-15 §D — traduce títulos del recorrido cacheado que aún no están en
    Redis, acotado por pasada. Devuelve cuántos se pidieron al LLM.

    Va aquí y no en el router a propósito: el camino de respuesta NO llama al
    LLM (CLAUDE.md §5). Esto lo paga el fondo, una vez por título, y la
    pantalla principal sólo lee. Sin Groq configurado no hace nada.
    """
    if not settings.TRANSLATION_WARMUP_ENABLED or redis is None:
        return 0  # sin Redis no hay dónde dejar la traducción: sería pagar en vano
    titulos_de = getattr(backend, "cached_feed_titles", None)
    if titulos_de is None:
        return 0
    titulos = await titulos_de(user_id)
    if not titulos:
        return 0
    from services import language_store
    from services.groq_service import GroqService
    from services.translation_service import TranslationService

    groq = GroqService(redis_client=redis)
    if not groq.is_available:
        return 0
    translator = TranslationService(groq)
    ya = await translator.cached_translations(titulos)
    pendientes = [t for t in dict.fromkeys(titulos) if t not in ya]
    pendientes = pendientes[: settings.TRANSLATION_WARMUP_MAX_PER_PASS]
    if not pendientes:
        return 0
    idiomas, _vistos = await language_store.lookup(db, pendientes)
    await translator.translate_titles(
        [{"title": t, "language": ""} for t in pendientes], languages=idiomas
    )
    return len(pendientes)


async def run_feed_warmup(redis=None) -> None:
    """Bucle de fondo; se cancela con el resto en el apagado del lifespan."""
    if not settings.FEED_WARMUP_ENABLED:
        logger.info("calentamiento del feed DESACTIVADO (FEED_WARMUP_ENABLED)")
        return
    intervalo = settings.FEED_WARMUP_INTERVAL_SECONDS
    logger.info("calentamiento del feed activo cada %s s", intervalo)
    while True:
        try:
            resumen = await calentar_una_vez(redis)
            if resumen["calentados"] or resumen["fallos"] or resumen["traducidos"]:
                logger.info("calentamiento del feed: %s", resumen)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("calentamiento del feed: pasada fallida")
        await asyncio.sleep(intervalo)
