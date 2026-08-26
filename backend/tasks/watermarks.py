"""Marcas de agua de las tareas periódicas de notificación (G3/P1-1).

En PostgreSQL `now()` es `transaction_timestamp()`: se congela al ABRIR la
transacción, no al INSERT ni al commit. `Job.first_seen_at` y
`MatchResult.created_at` los pone ese `server_default` (están en
`_SYSTEM_MANAGED`), y la transacción que los persiste se abre ANTES de la
descarga y se cierra minutos después. Consecuencia medida por la auditoría G3:

    T0 = inicio de la transacción de cosecha → las altas nacen con T0
    T1 = la tarea de aviso corre, no ve nada (aún sin commit) y guarda marca T1
    T2 = la cosecha commitea
    corrida siguiente: `first_seen_at > T1` y las ofertas valen T0 < T1
    → el lote entero no se notifica JAMÁS, y la tarea devuelve `success`

El arreglo tiene dos mitades y las dos son necesarias:

1. `save_watermark` retrocede la marca un LAG de seguridad mayor que la
   transacción de cosecha/matching más larga: la ventana de la corrida
   siguiente SOLAPA con la anterior en vez de dejar un hueco permanente.
2. Como el solape reintroduce candidatos ya avisados, la idempotencia pasa a
   ser POR ELEMENTO: un marcador `SET NX EX` en Redis por elemento enviado.
   Esto sustituye además al trade-off documentado en `alert_tasks` («marca
   antes de enviar para no duplicar nunca, aun a costa de perder ese aviso»).

Complementario (fuera de este módulo): `clock_timestamp()` como default de esas
columnas reduce el desfase de «inicio de tx→commit» a «INSERT→commit», pero NO
lo elimina — el LAG sigue siendo la red de seguridad.
"""

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def watermark_lag() -> timedelta:
    """Retroceso de seguridad de la marca (config, en minutos)."""
    from config import settings

    return timedelta(minutes=settings.NOTIFY_WATERMARK_LAG_MINUTES)


def sent_marker_ttl_seconds() -> int:
    """Caducidad del marcador «ya avisado» por elemento (config, en días)."""
    from config import settings

    return settings.NOTIFY_SENT_MARKER_TTL_DAYS * 24 * 3600


def save_watermark(r, key: str, now: datetime) -> datetime:
    """Guarda la marca RETROCEDIDA el lag de seguridad y devuelve lo guardado."""
    value = now - watermark_lag()
    r.set(key, value.isoformat())
    return value


def filter_unsent(r, prefix: str, ids: Iterable[str]) -> list[str]:
    """Devuelve los ids aún NO avisados y los marca como avisados.

    Marcador por elemento con `SET NX EX`: idempotencia real (el solape de la
    ventana no re-envía) y expiración automática (no queda un conjunto que
    crezca sin límite). Si Redis falla, se devuelven TODOS los ids: mejor un
    aviso repetido que un aviso perdido.
    """
    unique = list(dict.fromkeys(ids))
    if not unique:
        return []
    ttl = sent_marker_ttl_seconds()
    fresh: list[str] = []
    for item in unique:
        try:
            if r.set(f"{prefix}:{item}", b"1", nx=True, ex=ttl):
                fresh.append(item)
        except Exception as exc:  # Redis caído: no bloquear el aviso
            logger.warning("Marcador de aviso no disponible (%s): %s", prefix, exc)
            return unique
    return fresh


def unmark_sent(r, prefix: str, ids: Iterable[str]) -> None:
    """Retira los marcadores de `ids` (el envío falló: debe reintentarse)."""
    for item in ids:
        try:
            r.delete(f"{prefix}:{item}")
        except Exception as exc:
            logger.warning(
                "No se pudo retirar el marcador %s:%s: %s", prefix, item, exc
            )
