"""Registro de la salud de cosecha por fuente (V.0).

Responsabilidad única: traducir el resultado de un run (nº de ofertas + fallos
de descarga recogidos por `utils.fetch_diagnostics`) a una fila de
`source_health`, y decidir si esa fuente merece alerta.

NO decide permisos (eso es `ComplianceEngine`) ni reintenta nada.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.source_health import (
    OUTCOME_EMPTY,
    OUTCOME_ERROR,
    OUTCOME_OK,
    SourceHealth,
)
from utils.fetch_diagnostics import FetchIssue

logger = logging.getLogger(__name__)

# Longitud de la columna: recortamos aquí para no depender del error de la BD.
_DETAIL_MAX = 500


def _summarize(collected: list[FetchIssue]) -> str:
    """Resumen legible de los fallos: el primero + cuántos más hubo."""
    if not collected:
        return ""
    head = collected[0].describe()
    if len(collected) > 1:
        head = f"{head} (+{len(collected) - 1} más)"
    return head[:_DETAIL_MAX]


async def record_outcome(
    db: AsyncSession,
    source_key: str,
    outcome: str,
    job_count: int,
    collected: list[FetchIssue] | None = None,
) -> SourceHealth:
    """Persiste el resultado del run de una fuente y actualiza sus rachas.

    Devuelve la fila para que el llamante pueda decidir sobre ella (p.ej.
    `needs_alert`). No hace commit: lo hace el pipeline con su transacción.
    """
    collected = collected or []
    now = datetime.now(timezone.utc)

    row = (
        await db.execute(
            select(SourceHealth).where(SourceHealth.source_key == source_key)
        )
    ).scalar_one_or_none()
    if row is None:
        # Contadores explícitos a 0: el `default=` de SQLAlchemy solo se aplica
        # al INSERT, y aquí se incrementan ANTES del flush (serían None += 1).
        row = SourceHealth(
            source_key=source_key,
            last_jobs_count=0,
            consecutive_errors=0,
            consecutive_empty=0,
        )
        db.add(row)

    row.last_attempt_at = now
    row.last_outcome = outcome
    row.last_jobs_count = job_count

    if outcome == OUTCOME_OK:
        row.last_success_at = now
        row.consecutive_errors = 0
        row.consecutive_empty = 0
    elif outcome == OUTCOME_ERROR:
        row.last_error_at = now
        row.last_error_detail = _summarize(collected)
        row.consecutive_errors += 1
        # La racha de vacíos NO se toca: un error no es un vacío.
    elif outcome == OUTCOME_EMPTY:
        row.consecutive_empty += 1

    return row


async def record_and_alert(
    db: AsyncSession,
    source_key: str,
    outcome: str,
    job_count: int,
    collected: list[FetchIssue] | None = None,
) -> str | None:
    """Registra el veredicto, CONFIRMA y devuelve el motivo de alerta (o None).

    Punto de entrada único para los dos pipelines (providers y scrapers).

    El commit es propio y acotado a propósito: la salud tiene que quedar
    registrada aunque el procesado posterior de las ofertas falle y haga
    rollback — es exactamente el caso que antes no dejaba rastro. Si el propio
    registro falla, se traga el error: la observabilidad NUNCA debe tumbar la
    cosecha.
    """
    try:
        row = await record_outcome(db, source_key, outcome, job_count, collected)
        motivo = needs_alert(row)
        await db.commit()
    except Exception as e:  # noqa: BLE001 — degradar, no romper la cosecha
        await db.rollback()
        logger.error("No se pudo registrar la salud de %s: %s", source_key, e)
        return None

    if motivo:
        logger.error("FUENTE DEGRADADA %s — %s", source_key, motivo)
    return motivo


def needs_alert(row: SourceHealth) -> str | None:
    """Motivo por el que esta fuente merece alerta, o None si está sana.

    Dos umbrales separados porque exigen acciones distintas: una racha de
    errores es un portal caído o un feed muerto (hay que arreglarlo); una racha
    de vacíos puede ser estacionalidad o un selector roto (hay que mirarlo).
    """
    if row.consecutive_errors >= settings.SOURCE_HEALTH_ERROR_STREAK:
        return (
            f"{row.consecutive_errors} runs seguidos con error"
            f" — último: {row.last_error_detail or 'sin detalle'}"
        )
    if row.consecutive_empty >= settings.SOURCE_HEALTH_EMPTY_STREAK:
        return f"{row.consecutive_empty} runs seguidos sin traer ofertas"
    return None
