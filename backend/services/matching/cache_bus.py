"""M3/T12 — invalidación de la caché del feed ENTRE procesos.

La caché del recorrido (`core_client._feed_cache`) vive **en proceso**, y en
producción gunicorn corre con `-w 2`. Así que un `clear_feed_cache()` sólo
limpiaba el worker que atendió la escritura: el otro seguía sirviendo el
recorrido viejo hasta que cambiara la versión del feed. Un «me interesa»
guardado por un worker podía no verse al recargar si la siguiente petición caía
en el otro — que es exactamente la regresión que cerró T2, sólo que repartida
entre procesos.

Redis pub/sub lo reparte: quien invalida lo publica, y cada worker aplica en el
suyo. El publicador también recibe su propio mensaje; limpiar dos veces es
inofensivo.

Deliberado: esto NO sustituye al borrado local inmediato. Se limpia en el acto
Y se publica, para que el worker que atiende la escritura no dependa de la
vuelta por Redis para servirse a sí mismo correcto.
"""

import asyncio
import logging

import redis.asyncio as aioredis

from config import settings

logger = logging.getLogger(__name__)

CANAL = "feed-cache:invalidate"
_TODOS = "*"

_cliente: aioredis.Redis | None = None


def _conexion() -> aioredis.Redis:
    """Cliente compartido del proceso; las invalidaciones son raras."""
    global _cliente
    if _cliente is None:
        _cliente = aioredis.from_url(settings.REDIS_URL)
    return _cliente


async def publicar(profile_id=None) -> None:
    """Avisa a los demás procesos. Un fallo aquí NO rompe la escritura.

    Best-effort a propósito: el borrado local ya se hizo. Si Redis no está,
    el otro worker se queda con su copia hasta el siguiente cambio de versión
    — que es el comportamiento de antes, no uno peor.
    """
    try:
        await _conexion().publish(CANAL, str(profile_id) if profile_id else _TODOS)
    except Exception:
        logger.warning("cache_bus: no se pudo publicar la invalidación del feed")


async def escuchar() -> None:
    """Bucle de fondo: aplica en ESTE proceso lo que invalidaron los demás."""
    from .core_client import clear_feed_cache

    while True:
        try:
            pubsub = _conexion().pubsub()
            await pubsub.subscribe(CANAL)
            logger.info("cache_bus: escuchando %s", CANAL)
            async for mensaje in pubsub.listen():
                if mensaje.get("type") != "message":
                    continue
                dato = mensaje["data"]
                pid = dato.decode() if isinstance(dato, bytes) else str(dato)
                clear_feed_cache(None if pid == _TODOS else pid)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Una caída de Redis no puede dejar el worker sin escucha para
            # siempre: se reintenta. Sin esto, el primer corte de red apagaba
            # la invalidación entre procesos en silencio y para el resto de la
            # vida del worker.
            logger.exception("cache_bus: escucha caída; se reintenta")
            await asyncio.sleep(5)


def publicar_sin_esperar(profile_id=None) -> None:
    """Versión para llamadores SÍNCRONOS (p. ej. `clear_erased_caches`).

    Si hay bucle corriendo, se lanza como tarea y se guarda la referencia: una
    tarea suelta puede recogerla el recolector antes de ejecutarse. Si no hay
    bucle, no se hace nada — el borrado local ya está hecho y el otro worker se
    entera en el siguiente cambio de versión, que es el comportamiento previo.
    """
    try:
        bucle = asyncio.get_running_loop()
    except RuntimeError:
        return
    tarea = bucle.create_task(publicar(profile_id))
    _pendientes.add(tarea)
    tarea.add_done_callback(_pendientes.discard)


_pendientes: set = set()
