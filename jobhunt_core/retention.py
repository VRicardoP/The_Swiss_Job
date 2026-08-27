"""Retención de las tablas de TRABAJO TERMINADO del core (O-4, 2026-08-27).

Cuatro tablas crecían sin cota alguna. Medido en el clúster el 2026-08-27
(SELECT de solo lectura), 28 días de vida del despliegue:

    integration_outbox             13 425 filas   6,5 MB   2026-07-28..08-25
    integration_outbox_deliveries  13 425 filas   3,9 MB   TODAS 'delivered'
    shadow_inbox                   13 425 filas   8,6 MB   2026-07-28..08-25
    match_evaluations              13 419 filas   9,9 MB   2026-07-28..08-25
                                                  -------
                                                  28,9 MB en 28 días

Es decir ~377 MB/año de tabla más sus índices, sobre una instancia compartida
con el legacy cuyo volumen ya estaba al 75 %. `integration_outbox_deliveries`
agrupada por estado devolvía UNA fila —`delivered = 13 425`—: el 100 % del
contenido era trabajo terminado que nadie iba a recoger nunca.

Ya existían dos retenciones en el paquete (`idempotency_records` y
`shadow_change_log`); esto es el mismo molde para las cuatro que faltaban.

CRITERIO — se borra solo lo que ni el GATE ni una auditoría pueden necesitar:

- `integration_outbox_deliveries`: SOLO `state = 'delivered'` con `ack_at`
  fuera de retención. Las `dead` NO se tocan jamás: el gate `outbox_dead`
  (shadow/metrics.py) cuenta `state = 'dead'` SIN cota temporal, así que
  borrar una dead cambiaría una métrica del gate. Las `pending`/`inflight`
  tampoco: son trabajo por hacer.
- `integration_outbox`: solo los eventos que ya NO tienen NINGUNA entrega
  (las suyas se purgaron arriba) y están fuera de retención. La FK es
  `ON DELETE CASCADE` desde el evento, así que borrar por edad a secas
  habría arrastrado entregas `dead` vivas: por eso la anti-unión explícita.
  `delivery.stats` mide `oldest_pending_s` sobre pending/inflight, que por
  construcción conservan su evento.
- `shadow_inbox`: por edad. Es la EVIDENCIA del consumo idempotente de
  ADR-06 (PK (consumer_id, event_id) absorbiendo la re-entrega). La
  retención tiene que ser mucho mayor que la ventana máxima de re-entrega
  —lease + backoff + dead-letter, minutos— y 30 días lo es con holgura.
- `match_evaluations`: la más delicada, con DOS guardas acumulativas. (1) No
  se borra ninguna referenciada por `profile_vacancy_state.current_eval_id`
  (la FK es `ON DELETE RESTRICT`, o sea que un descuido abortaría la pasada
  en vez de corromper — pero no se delega en eso: la anti-unión es
  explícita). (2) Solo se borra una evaluación SUPERADA, es decir cuando
  existe otra MÁS NUEVA del mismo par (profile_id, vacancy_id): así la
  última evaluación de un par nunca desaparece, aunque su fila de estado se
  haya quedado sin puntero. Retención propia y más larga (90 d) porque
  `_coste_row` del gate las cuenta por ciclo: recalcular un ciclo antiguo
  con las evaluaciones purgadas daría un número menor.

ACOTADA POR PASADA (`CORE_RETENTION_MAX_ROWS`, 20 000): la purga no coge un
lock largo sobre tablas que el dispatcher y el matching están usando. Si un
día sobra trabajo, el resto se lo lleva la pasada siguiente — es idempotente
y converge.

Coste medido de los predicados (`EXPLAIN ANALYZE` del `SELECT` equivalente,
sobre los datos reales, con retención de 20 d para que hubiera candidatos):
entregas 3,9 ms · eventos huérfanos 27,3 ms · evaluaciones 11,6 ms. Los tres
resuelven por barrido secuencial y NO hace falta ningún índice nuevo: el
propósito de esta purga es justamente que esas tablas dejen de crecer, así que
el barrido queda acotado por la propia retención. Un índice por `ack_at` o
`created_at` costaría escritura en el camino caliente del dispatcher para
ahorrar milisegundos una vez al día.
"""

import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.config import settings

logger = logging.getLogger(__name__)

# Entregas ya reconocidas: se borran por `ack_at`, no por `created_at` del
# evento — lo que cierra el trabajo es el ack.
_DELIVERIES_SQL = (
    "DELETE FROM integration_outbox_deliveries "
    "WHERE ctid IN (SELECT ctid FROM integration_outbox_deliveries "
    "               WHERE state = 'delivered' AND ack_at IS NOT NULL "
    "                 AND ack_at < now() - make_interval(days => :dias) "
    "               LIMIT :tope)"
)

# Eventos que se quedaron SIN entregas (las suyas murieron en el paso
# anterior). NOT EXISTS y no edad a secas: el CASCADE arrastraría dead vivas.
_OUTBOX_SQL = (
    "DELETE FROM integration_outbox "
    "WHERE ctid IN (SELECT o.ctid FROM integration_outbox o "
    "               WHERE o.created_at < now() - make_interval(days => :dias) "
    "                 AND NOT EXISTS (SELECT 1 "
    "                                 FROM integration_outbox_deliveries d "
    "                                 WHERE d.event_id = o.event_id) "
    "               LIMIT :tope)"
)

_INBOX_SQL = (
    "DELETE FROM shadow_inbox "
    "WHERE ctid IN (SELECT ctid FROM shadow_inbox "
    "               WHERE received_at < now() - make_interval(days => :dias) "
    "               LIMIT :tope)"
)

# Evaluación SUPERADA (existe otra más nueva del mismo par) y SIN puntero
# vivo. Las dos condiciones se acumulan: la última evaluación de un par
# sobrevive siempre, tenga o no fila de estado apuntándola.
_EVALS_SQL = (
    "DELETE FROM match_evaluations "
    "WHERE ctid IN (SELECT m.ctid FROM match_evaluations m "
    "               WHERE m.created_at < now() - make_interval(days => :dias) "
    "                 AND NOT EXISTS (SELECT 1 FROM profile_vacancy_state s "
    "                                 WHERE s.current_eval_id = m.id) "
    "                 AND EXISTS (SELECT 1 FROM match_evaluations n "
    "                             WHERE n.profile_id = m.profile_id "
    "                               AND n.vacancy_id = m.vacancy_id "
    "                               AND n.created_at > m.created_at) "
    "               LIMIT :tope)"
)


async def purge_retention(session: AsyncSession) -> dict:
    """Una pasada de retención. Devuelve conteos JSON-serializables (tarea).

    ORDEN OBLIGATORIO: entregas antes que eventos — el barrido de eventos
    huérfanos solo encuentra algo si las entregas de ese evento ya cayeron en
    la MISMA transacción.

    Idempotente: la segunda pasada del mismo día no encuentra nada que borrar.
    """
    tope = int(settings.CORE_RETENTION_MAX_ROWS)
    outbox_dias = int(settings.CORE_OUTBOX_RETENTION_DAYS)
    inbox_dias = int(settings.CORE_SHADOW_INBOX_RETENTION_DAYS)
    evals_dias = int(settings.CORE_EVAL_RETENTION_DAYS)

    async def borrar(sql: str, dias: int) -> int:
        return (
            await session.execute(sa.text(sql), {"dias": dias, "tope": tope})
        ).rowcount

    result = {
        "entregas": await borrar(_DELIVERIES_SQL, outbox_dias),
        "eventos": await borrar(_OUTBOX_SQL, outbox_dias),
        "inbox_sombra": await borrar(_INBOX_SQL, inbox_dias),
        "evaluaciones": await borrar(_EVALS_SQL, evals_dias),
        "tope_por_pasada": tope,
    }
    if any(result[k] for k in ("entregas", "eventos", "inbox_sombra", "evaluaciones")):
        logger.info("purge_retention: %s", result)
    return result
