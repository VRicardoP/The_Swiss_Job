"""Runner de ingesta por scope (A-03) — disciplina ADR-05.

Orden inviolable: fetch → sink (persistencia, A-04) → COMMIT DEL CURSOR AL
FINAL, en la MISMA transacción que la persistencia. Si el sink falla: rollback,
el cursor NO avanza (ningún listing queda perdido entre fetch y persistencia) y
se registra el fallo (consecutive_failures) para el backoff del scheduler.
"""

import json
import logging

import httpx
import sqlalchemy as sa

from jobhunt_core.database import SessionLocal
from jobhunt_core.harvest.provider import BaseProvider, ListingSink
from jobhunt_core.harvest.types import ScopeRunResult

logger = logging.getLogger(__name__)


async def run_scope(
    scope_id: str,
    provider: BaseProvider,
    sink: ListingSink,
    http: httpx.AsyncClient,
    session_factory=SessionLocal,
) -> ScopeRunResult:
    async with session_factory() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT hs.params, hs.enabled, s.name AS source_name, sss.cursor "
                    "FROM harvest_scopes hs "
                    "JOIN sources s ON s.id = hs.source_id "
                    "LEFT JOIN source_scope_state sss ON sss.scope_id = hs.id "
                    "WHERE hs.id = :sid"
                ),
                {"sid": scope_id},
            )
        ).one_or_none()
        if row is None:
            return ScopeRunResult(scope_id=scope_id, status="error", error="scope inexistente")
        if not row.enabled:
            return ScopeRunResult(scope_id=scope_id, status="skipped")
        if row.source_name != provider.name:
            return ScopeRunResult(
                scope_id=scope_id, status="error",
                error=f"provider {provider.name!r} != source {row.source_name!r}",
            )
        await session.rollback()  # cierra la tx de lectura: el fetch va fuera de tx

        try:
            result = await provider.fetch_new(row.params or {}, row.cursor, http)
        except Exception as exc:
            await _record_failure_safe(session, scope_id)
            logger.warning("scope %s: fetch falló: %s", scope_id, exc)
            return ScopeRunResult(scope_id=scope_id, status="error", error=str(exc)[:200])

        try:
            # Persistencia + cursor en UNA transacción (commit del cursor AL FINAL).
            await sink.handle(session, scope_id, result.listings)
            await session.execute(
                sa.text(
                    "INSERT INTO source_scope_state "
                    "(scope_id, cursor, last_complete_at, consecutive_failures) "
                    "VALUES (:sid, CAST(:cur AS jsonb), now(), 0) "
                    "ON CONFLICT (scope_id) DO UPDATE SET "
                    "cursor = EXCLUDED.cursor, last_complete_at = now(), "
                    "consecutive_failures = 0"
                ),
                {"sid": scope_id, "cur": json.dumps(result.next_cursor)},
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()  # ni listings a medias ni cursor avanzado
            await _record_failure_safe(session, scope_id)
            logger.warning("scope %s: persistencia falló, cursor intacto: %s", scope_id, exc)
            return ScopeRunResult(scope_id=scope_id, status="error", error=str(exc)[:200])

        logger.info(
            "scope %s: %d listings, %d páginas, cursor commiteado",
            scope_id, len(result.listings), result.pages_fetched,
        )
        return ScopeRunResult(
            scope_id=scope_id, status="ok",
            listings=len(result.listings), pages=result.pages_fetched,
        )


async def _record_failure_safe(session, scope_id: str) -> None:
    """Cuenta el fallo SIN tocar el cursor (insumo del backoff, ADR-05).

    NUNCA propaga: si la propia contabilización falla (p.ej. BD caída — la
    causa probable del fallo original), se loguea y el runner devuelve igual
    su ScopeRunResult con el error ORIGINAL (auditoría A-03).
    """
    try:
        await session.rollback()  # asegura sesión utilizable tras el fallo previo
        await session.execute(
            sa.text(
                "INSERT INTO source_scope_state (scope_id, consecutive_failures) "
                "VALUES (:sid, 1) "
                "ON CONFLICT (scope_id) DO UPDATE SET "
                "consecutive_failures = source_scope_state.consecutive_failures + 1"
            ),
            {"sid": scope_id},
        )
        await session.commit()
    except Exception:
        logger.exception("scope %s: no se pudo registrar el fallo", scope_id)
