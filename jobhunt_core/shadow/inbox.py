"""Transporte de producción de la SOMBRA (P1-1b, CONTRATOS_FASE_B.md §8).

Decisión delegada del propietario [EJECUTADA, registrada en el contrato]:
en Fase B el outbox core entrega REALMENTE — pero SOLO al inbox sombra
`jobhunt.shadow_inbox` (core0009), persistente e idempotente por
PK(consumer_id, event_id): el contrato de ADR-06 (transporte at-least-once +
consumo idempotente) queda demostrado EN CONTINUO sin ningún efecto visible
a usuarios (§8: prohibición dura en sombra). El transporte real (HTTP al
inbox del BFF) llega con el cutover de Fase C y SUSTITUYE a este por la
misma costura (`delivery.set_transport`).

Forma: función SÍNCRONA `fn(destination, event)` que lanza si falla — el
contrato exacto de `delivery.set_transport`. psycopg2 (como capture.py):
el despacho corre fuera del loop async y una excepción aquí marca la entrega
como fallida (backoff + reintento del dispatcher, dead-letter al agotar).

Registro: `register_if_unset()` desde el arranque del worker (señal
worker_process_init en tasks/delivery.py) — SOLO si nadie inyectó ya un
transporte: los tests (y la Fase C) siguen pudiendo inyectar el suyo, y
`.apply()` en tests no dispara señales de worker.

Conexión CACHEADA por proceso (autocommit, un INSERT por evento): el
dispatcher entrega lotes de hasta 100 eventos cada 5 min — una conexión por
evento sería ruido. Tras un error se cierra y descarta: la siguiente entrega
reconecta.
"""

import json
import logging
import os

import psycopg2

from jobhunt_core import delivery
from jobhunt_core.config import settings

logger = logging.getLogger(__name__)

_conn = None  # conexión cacheada del proceso (se recrea tras un fallo)


def _dsn() -> str:
    return os.getenv("CORE_DATABASE_URL", settings.CORE_DATABASE_URL).replace(
        "postgresql+asyncpg://", "postgresql://"
    )


def _connection():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(
            _dsn(), options=f"-c search_path={settings.CORE_DB_SCHEMA},public"
        )
        _conn.autocommit = True
    return _conn


def shadow_inbox_transport(destination: str, event: dict) -> None:
    """Entrega UN evento al inbox sombra — síncrono; excepción = fallo.

    INSERT idempotente: la re-entrega (at-least-once real: lease caducado,
    ack perdido) colisiona en PK(consumer_id, event_id) y se absorbe con
    DO NOTHING — el "consumo idempotente" del contrato, persistido."""
    global _conn
    try:
        with _connection().cursor() as cur:
            cur.execute(
                "INSERT INTO shadow_inbox (consumer_id, event_id, payload) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (consumer_id, event_id) DO NOTHING",
                (destination, event["event_id"], json.dumps(event, default=str)),
            )
    except psycopg2.Error:
        # Conexión/estado dudoso: descartar — la próxima entrega reconecta.
        try:
            if _conn is not None:
                _conn.close()
        except Exception:  # cerrar jamás enmascara el error original
            pass
        _conn = None
        raise


def register_if_unset() -> bool:
    """Registra el transporte sombra SOLO si no hay ninguno inyectado.

    Un transporte ya presente (test, o el HTTP real de Fase C) MANDA:
    jamás se pisa. Devuelve True si registró."""
    if delivery.get_transport() is not None:
        return False
    delivery.set_transport(shadow_inbox_transport)
    logger.info(
        "shadow.inbox: transporte sombra registrado (entrega REAL al inbox "
        "jobhunt.shadow_inbox — el HTTP al BFF llega en Fase C)"
    )
    return True
