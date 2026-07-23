"""Runner de ingesta por scope (A-03) — disciplina ADR-05 + concurrencia.

Orden inviolable: fetch → sink (persistencia, A-04) → COMMIT DEL CURSOR AL
FINAL, en la MISMA transacción que la persistencia. Además (revisión externa):
- La transacción de persistencia BLOQUEA la fila del scope (`FOR UPDATE` sobre
  harvest_scopes, fila permanente) y RE-LEE el cursor: si otro run del mismo
  scope avanzó entre el fetch y el commit, este run aborta como `stale` sin
  pisar nada (lost-update imposible).
- El cursor guarda un FINGERPRINT de los parámetros semánticos del scope: si
  cambian (p.ej. la keyword), el cursor se reinicia — un watermark heredado
  enterraría ofertas que el filtro nuevo sí quiere.
- Si el sink falla: rollback conjunto, el cursor NO avanza y se registra el
  fallo (consecutive_failures) sin enmascarar jamás el error original.
"""

import json
import logging

import httpx
import sqlalchemy as sa

from jobhunt_core.database import SessionLocal
from jobhunt_core.harvest.provider import BaseProvider, ListingSink
from jobhunt_core.harvest.types import ScopeRunResult

logger = logging.getLogger(__name__)

# Clave interna (no del provider) con el fingerprint semántico dentro del cursor.
FINGERPRINT_KEY = "_params_fp"


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

        params = row.params or {}
        snapshot = row.cursor  # tal cual en BD: base del check de concurrencia
        fingerprint = provider.params_fingerprint(params)
        provider_cursor = _provider_cursor(snapshot, fingerprint, scope_id)
        await session.rollback()  # cierra la tx de lectura: el fetch va fuera de tx

        try:
            result = await provider.fetch_new(params, provider_cursor, http)
        except Exception as exc:
            await _record_failure_safe(session, scope_id)
            logger.warning("scope %s: fetch falló: %s", scope_id, exc)
            return ScopeRunResult(scope_id=scope_id, status="error", error=str(exc)[:200])

        try:
            # Persistencia + cursor en UNA transacción, con la fila del scope
            # BLOQUEADA: ningún otro run del mismo scope puede colarse. Se
            # re-valida TODA la configuración (no solo el cursor): enabled,
            # params y fuente pueden cambiar durante el fetch (rev. A-03 #3).
            locked = (
                await session.execute(
                    sa.text(
                        "SELECT hs.enabled, hs.params, s.name AS source_name, sss.cursor "
                        "FROM harvest_scopes hs "
                        "JOIN sources s ON s.id = hs.source_id "
                        "LEFT JOIN source_scope_state sss ON sss.scope_id = hs.id "
                        "WHERE hs.id = :sid FOR UPDATE OF hs"
                    ),
                    {"sid": scope_id},
                )
            ).one_or_none()
            if locked is None:
                raise RuntimeError("el scope desapareció durante el run")
            if not locked.enabled:
                await session.rollback()
                logger.info("scope %s: deshabilitado durante el run, skipped", scope_id)
                return ScopeRunResult(scope_id=scope_id, status="skipped")
            stale_reason = None
            if locked.cursor != snapshot:
                stale_reason = "cursor avanzado por otro run"
            elif locked.source_name != provider.name:
                stale_reason = f"fuente re-apuntada a {locked.source_name!r}"
            elif provider.params_fingerprint(locked.params or {}) != fingerprint:
                stale_reason = "parámetros semánticos cambiados durante el fetch"
            if stale_reason:
                # Abortar SIN pisar (no es fallo de la fuente: no cuenta backoff).
                await session.rollback()
                logger.info("scope %s: %s, stale", scope_id, stale_reason)
                return ScopeRunResult(scope_id=scope_id, status="stale")

            await sink.handle(session, scope_id, result.listings)
            new_cursor = {**result.next_cursor, FINGERPRINT_KEY: fingerprint}
            await session.execute(
                sa.text(
                    "INSERT INTO source_scope_state "
                    "(scope_id, cursor, last_complete_at, consecutive_failures) "
                    "VALUES (:sid, CAST(:cur AS jsonb), now(), 0) "
                    "ON CONFLICT (scope_id) DO UPDATE SET "
                    "cursor = EXCLUDED.cursor, last_complete_at = now(), "
                    "consecutive_failures = 0"
                ),
                {"sid": scope_id, "cur": json.dumps(new_cursor)},
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


def _provider_cursor(stored: dict | None, fingerprint: str, scope_id: str) -> dict | None:
    """Cursor a entregar al provider; None si los params semánticos cambiaron."""
    if not stored:
        return None
    stored_fp = stored.get(FINGERPRINT_KEY)
    if stored_fp is not None and stored_fp != fingerprint:
        logger.warning(
            "scope %s: parámetros semánticos cambiaron, cursor reiniciado", scope_id
        )
        return None
    cursor = {k: v for k, v in stored.items() if k != FINGERPRINT_KEY}
    return cursor or None


async def _record_failure_safe(session, scope_id: str) -> None:
    """Cuenta el fallo SIN tocar el cursor (insumo del backoff, ADR-05).

    NUNCA propaga: si la propia contabilización falla (p.ej. BD caída — la
    causa probable del fallo original), se loguea y el runner devuelve igual
    su ScopeRunResult con el error ORIGINAL.
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
