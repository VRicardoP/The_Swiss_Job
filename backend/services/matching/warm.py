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

# Marca de actividad: el router la escribe al servir el feed o sus facetas, y el
# calentador sólo calienta a quien la tiene. Sin ella se calentaba a todos los
# enrolados cada pasada aunque nadie entrase (1.732 versiones/día sin usuarios).
# Coste aceptado: quien vuelve tras más de un día paga la primera carga en frío.
ACTIVE_KEY_PREFIX = "feed:active:"
ACTIVE_TTL_SECONDS = 86_400


async def mark_active(redis, user_id) -> None:
    """Marca al usuario como activo. Sin Redis no hace nada; un fallo no se
    propaga: la marca es una optimización del fondo, no parte de la respuesta."""
    if redis is None:
        return
    try:
        await redis.set(f"{ACTIVE_KEY_PREFIX}{user_id}", b"1", ex=ACTIVE_TTL_SECONDS)
    except Exception:
        logger.debug("marca de actividad del feed no escrita para %s", user_id)


async def _usuarios_activos(redis, usuarios: list) -> list:
    """Filtra por la marca de actividad en UNA ida (MGET). Sin Redis, o si
    falla, devuelve todos: es el comportamiento de antes de la marca."""
    if redis is None or not usuarios:
        return usuarios
    try:
        marcas = await redis.mget([f"{ACTIVE_KEY_PREFIX}{u}" for u in usuarios])
    except Exception:
        logger.warning(
            "calentamiento del feed: Redis sin respuesta, se calienta a todos"
        )
        return usuarios
    return [u for u, marca in zip(usuarios, marcas) if marca]


async def _usuarios_enrolados(db) -> list:
    """Usuarios con vínculo de identidad en el core; sin vínculo no hay feed."""
    return list((await db.execute(select(JobhuntProfileMap.user_id))).scalars().all())


async def calentar_una_vez(redis=None) -> dict[str, int]:
    """Una pasada sobre los enrolados con marca de actividad. No propaga fallos
    por usuario."""
    from services.matching import resolve_matching

    calentados, facetas, fallos, traducidos = 0, 0, 0, 0
    async with async_session() as db:
        usuarios = await _usuarios_enrolados(db)
    usuarios = await _usuarios_activos(redis, usuarios)
    for user_id in usuarios:
        try:
            async with async_session() as db:
                backend = await resolve_matching(db, user_id)
                calentar = getattr(backend, "warm_feed", None)
                if calentar is None:
                    continue  # backend local: no hay recorrido que calentar
                if await calentar(user_id):
                    calentados += 1
                calentar_facetas = getattr(backend, "warm_facets", None)
                if calentar_facetas is not None and await calentar_facetas(user_id):
                    facetas += 1
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
        "facetas": facetas,
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
    from services.translation_service import SKIP_LANGUAGES, TranslationService

    groq = GroqService(redis_client=redis)
    if not groq.is_available:
        return 0
    translator = TranslationService(groq)
    ya = await translator.cached_translations(titulos)
    pendientes = [t for t in dict.fromkeys(titulos) if t not in ya]
    # El idioma va ANTES del corte: un título en EN/ES nunca se cachea (no hay
    # traducción que guardar), así que si entraba en la ventana la ocupaba en
    # TODAS las pasadas y la traducción no avanzaba (282 de 284 pasadas con
    # `traducidos: 200` en producción, 25-11). Un fallo del proveedor no marca
    # nada: lo que no se tradujo sigue sin clave y vuelve a la pasada siguiente.
    idiomas, _vistos = await language_store.lookup(db, pendientes)
    pendientes = [
        t
        for t in pendientes
        if idiomas.get(language_store.normalise(t)) not in SKIP_LANGUAGES
    ]
    pendientes = pendientes[: settings.TRANSLATION_WARMUP_MAX_PER_PASS]
    if not pendientes:
        return 0
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
            if (
                resumen["calentados"]
                or resumen["facetas"]
                or resumen["fallos"]
                or resumen["traducidos"]
            ):
                logger.info("calentamiento del feed: %s", resumen)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("calentamiento del feed: pasada fallida")
        await asyncio.sleep(intervalo)
