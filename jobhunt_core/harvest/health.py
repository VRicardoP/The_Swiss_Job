"""Vigilancia de la SALUD DE LA COSECHA por scope (auditoría G9 P2-C).

`source_scope_state` lleva desde A-03 las dos señales que dicen si una fuente
sigue ingiriendo —`consecutive_failures` y `last_complete_at`— y hasta ahora
NADIE las leía: ni una métrica, ni un gate, ni una alerta. Una fuente que
dejaba de cosechar (forma inválida persistente, credencial caducada, portal
caído) no producía más rastro que un `logger.warning` por run; el corpus se
quedaba quieto y, pasado `CORE_CORPUS_STALE_DAYS`, el barrido de archivado
(ADR-07) empezaba a archivar vacantes todavía publicadas. Es el MISMO desenlace
del falso verde que P1-1 cerró: solo cambia el color del semáforo interno.

Misma forma y misma disciplina que `shadow.gate.check_slot_health` (la otra
vigilancia del proyecto): función pura de lectura que devuelve la evidencia y
emite `logger.error` por alerta, cableada al beat del core-worker.

SILENCIO DELIBERADO en dos casos que NO son avería de cosecha:
- scope sin fila en `source_scope_state`: nunca se ha ejecutado (la cosecha del
  core se lanza a mano, sin beat propio) — no hay nada que se haya roto;
- scope deshabilitado: no se espera que coseche.
"""

import logging
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.config import settings

logger = logging.getLogger(__name__)


async def check_harvest_health(
    session: AsyncSession,
    now: datetime | None = None,
    max_consecutive_failures: int | None = None,
    stale_days: int | None = None,
) -> dict:
    """ALERTA (logger.error) por cada scope HABILITADO y ya ejecutado que:

    - acumula `consecutive_failures >= CORE_HARVEST_MAX_CONSECUTIVE_FAILURES`, o
    - no confirma una cosecha COMPLETA desde hace más de
      `CORE_HARVEST_STALE_ALERT_DAYS` (o no la ha confirmado nunca).

    Devuelve `{"alertas": [...], "scopes": n}` JSON-serializable (tarea Celery).
    Los umbrales son inyectables SOLO para tests.
    """
    moment = now or datetime.now(timezone.utc)
    max_failures = (
        max_consecutive_failures
        if max_consecutive_failures is not None
        else int(settings.CORE_HARVEST_MAX_CONSECUTIVE_FAILURES)
    )
    days = stale_days if stale_days is not None else int(settings.CORE_HARVEST_STALE_ALERT_DAYS)
    rows = (
        await session.execute(
            sa.text(
                "SELECT hs.id AS scope_id, s.name AS source, "
                "  sss.consecutive_failures, sss.last_complete_at "
                "FROM harvest_scopes hs "
                "JOIN sources s ON s.id = hs.source_id "
                "JOIN source_scope_state sss ON sss.scope_id = hs.id "
                "WHERE hs.enabled ORDER BY s.name, hs.id"
            )
        )
    ).all()
    alertas: list[dict] = []
    for row in rows:
        alertas += _scope_alerts(row, moment, max_failures, days)
    for alerta in alertas:
        logger.error("harvest_health: %s", alerta["msg"])
    return {"alertas": alertas, "scopes": len(rows)}


def _scope_alerts(row, moment: datetime, max_failures: int, stale_days: int) -> list[dict]:
    """Alertas de UN scope (las dos señales son independientes: una fuente puede
    fallar sin llevar tiempo rancia, y quedarse rancia sin fallar — un barrido
    eternamente PARCIAL no incrementa el contador)."""
    alertas: list[dict] = []
    fallos = int(row.consecutive_failures or 0)
    if fallos >= max_failures:
        alertas.append({
            "code": "cosecha_fallando",
            "scope_id": str(row.scope_id),
            "source": row.source,
            "consecutive_failures": fallos,
            "msg": (
                f"scope {row.scope_id} ({row.source}): {fallos} fallos consecutivos "
                f"(>= {max_failures}) — la fuente ha dejado de ingerir; revisar el "
                "log del scope antes de que el corpus se quede rancio"
            ),
        })
    edad_d = None
    if row.last_complete_at is not None:
        edad_d = (moment - row.last_complete_at).total_seconds() / 86400
    if edad_d is None or edad_d > stale_days:
        cuando = "NUNCA" if edad_d is None else f"hace {edad_d:.1f} d"
        alertas.append({
            "code": "cosecha_sin_completar",
            "scope_id": str(row.scope_id),
            "source": row.source,
            "dias_sin_cosecha_completa": edad_d,
            "msg": (
                f"scope {row.scope_id} ({row.source}): última cosecha COMPLETA "
                f"{cuando} (> {stale_days} d) — el corpus de esta fuente deja de "
                "refrescarse y el archivado ADR-07 acabará retirando vacantes vivas"
            ),
        })
    return alertas
