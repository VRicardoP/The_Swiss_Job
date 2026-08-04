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
from jobhunt_core.harvest.provider import BaseProvider, ListingSink, ProviderConfigError
from jobhunt_core.harvest.types import ScopeRunResult

logger = logging.getLogger(__name__)

# Clave interna (no del provider) con el fingerprint semántico dentro del cursor.
FINGERPRINT_KEY = "_params_fp"


async def _still_claim_owner(session, scope_id: str, token) -> bool:
    """True si el scope sigue reclamado por ESTE `token` y en 'running'. Un worker cuyo lease venció
    y fue RE-ARMADO por otro (claim_token sobrescrito en claim_scope_run) ya NO es dueño: no debe
    mutar `source_scope_state` (cursor / consecutive_failures) porque pisaría el estado del worker
    VIGENTE (P1 rev. externa integral ronda 2). `FOR UPDATE` bloquea un re-arm concurrente entre la
    comprobación y la escritura → check+write atómico. `token` None (llamada directa/legacy)
    preserva el comportamiento previo (sin fencing)."""
    if token is None:
        return True
    row = (
        await session.execute(
            sa.text(
                "SELECT 1 FROM source_harvest_runs WHERE scope_id = :sid "
                "AND claim_token = :tok AND status = 'running' FOR UPDATE"
            ),
            {"sid": scope_id, "tok": token},
        )
    ).first()
    return row is not None


async def run_scope(
    scope_id: str,
    provider: BaseProvider,
    sink: ListingSink,
    http: httpx.AsyncClient,
    session_factory=SessionLocal,
    claim_token=None,
) -> ScopeRunResult:
    async with session_factory() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT hs.params, hs.enabled, s.name AS source_name, sss.cursor, "
                    "  sss.last_complete_at "
                    "FROM harvest_scopes hs "
                    "JOIN sources s ON s.id = hs.source_id "
                    "LEFT JOIN source_scope_state sss ON sss.scope_id = hs.id "
                    "WHERE hs.id = :sid"
                ),
                {"sid": scope_id},
            )
        ).one_or_none()
        if row is None:
            # Scope eliminado tras encolar: caso NORMAL permanente (rev. 2ª
            # #3) — no es fallo de fuente y la tarea no debe reintentar.
            return ScopeRunResult(
                scope_id=scope_id, status="not_found",
                detail={"reason": "scope inexistente"},
            )
        if not row.enabled:
            return ScopeRunResult(scope_id=scope_id, status="skipped")
        if row.source_name != provider.name:
            return ScopeRunResult(
                scope_id=scope_id, status="error",
                error=f"provider {provider.name!r} != source {row.source_name!r}",
            )

        params = row.params or {}
        snapshot = row.cursor  # tal cual en BD: base del check de concurrencia
        # (cursor, last_complete_at) = ESTADO pre-fetch para la autoritatividad del run sin token.
        # Clave: `last_complete_at` recibe un now() FRESCO (≠ el previo) en CADA cosecha COMPLETA,
        # que es EXACTAMENTE cuando run_all resetea consecutive_failures=0 → "failures reseteado"
        # ⟺ "last_complete_at cambió". El VALOR del cursor NO basta: un feed estacionario re-escribe
        # el MISMO valor (P2 rev. externa integral ronda 3). Un reloj hacia atrás solo da falsos
        # NEGATIVOS (dirección segura), jamás un clobber.
        state_snapshot = (row.cursor, row.last_complete_at)
        fingerprint = provider.params_fingerprint(params)
        provider_cursor = _provider_cursor(snapshot, fingerprint, scope_id)
        await session.rollback()  # cierra la tx de lectura: el fetch va fuera de tx

        try:
            result = await provider.fetch_new(params, provider_cursor, http)
        except ProviderConfigError:
            # Config PERMANENTE inválida (rev. 2ª #3): NO es fallo de la
            # fuente (sin backoff) — sube a la tarea, que falla sin retry.
            raise
        except Exception as exc:
            await _record_failure_safe(
                session, scope_id, claim_token, state_snapshot=state_snapshot
            )
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
                # Borrado DURANTE el fetch (rev. 2ª #3): también not_found —
                # sin registrar fallo (el INSERT del contador violaría la FK).
                await session.rollback()
                logger.info("scope %s: eliminado durante el run, not_found", scope_id)
                return ScopeRunResult(
                    scope_id=scope_id, status="not_found",
                    detail={"reason": "scope eliminado durante el run"},
                )
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
            # FENCING: si el lease venció y otro worker re-armó el scope, este run YA NO es dueño —
            # NO debe escribir cursor/consecutive_failures pisando al vigente (P1 rev. externa
            # integral ronda 2). El FOR UPDATE del guard es de la MISMA tx que la persistencia.
            if not await _still_claim_owner(session, scope_id, claim_token):
                await session.rollback()
                logger.info(
                    "scope %s: lease vencido y re-armado por otro worker, stale", scope_id
                )
                return ScopeRunResult(scope_id=scope_id, status="stale")

            await sink.handle(session, scope_id, result.listings)
            new_cursor = {**result.next_cursor, FINGERPRINT_KEY: fingerprint}
            # Un barrido INCOMPLETO se persiste (sus listings son válidos) pero
            # NO cuenta como cosecha completa: last_complete_at intacto y los
            # fallos no se resetean (rev. 4ª #1).
            await session.execute(
                sa.text(
                    "INSERT INTO source_scope_state "
                    "(scope_id, cursor, last_complete_at, consecutive_failures) "
                    "VALUES (:sid, CAST(:cur AS jsonb), "
                    "CASE WHEN :complete THEN now() END, 0) "
                    "ON CONFLICT (scope_id) DO UPDATE SET "
                    "cursor = EXCLUDED.cursor, "
                    "last_complete_at = CASE WHEN :complete THEN now() "
                    "ELSE source_scope_state.last_complete_at END, "
                    "consecutive_failures = CASE WHEN :complete THEN 0 "
                    "ELSE source_scope_state.consecutive_failures END"
                ),
                {"sid": scope_id, "cur": json.dumps(new_cursor), "complete": result.complete},
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()  # ni listings a medias ni cursor avanzado
            await _record_failure_safe(
                session, scope_id, claim_token, state_snapshot=state_snapshot
            )
            logger.warning("scope %s: persistencia falló, cursor intacto: %s", scope_id, exc)
            return ScopeRunResult(scope_id=scope_id, status="error", error=str(exc)[:200])

        status = "ok" if result.complete else "partial"
        log = logger.info if result.complete else logger.warning
        log(
            "scope %s: %d listings, %d páginas, cursor commiteado (%s)",
            scope_id, len(result.listings), result.pages_fetched, status,
        )
        return ScopeRunResult(
            scope_id=scope_id, status=status,
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


_NO_SNAPSHOT = object()  # centinela: distingue "sin snapshot" (legacy) de "snapshot=None" (sin estado)


async def _still_authoritative(session, scope_id: str, token, state_snapshot) -> bool:
    """True si este run sigue siendo AUTORITATIVO sobre el scope para mutar su estado:
    - CON token (vía run_all): el claim sigue vigente (_still_claim_owner, FOR UPDATE).
    - SIN token pero CON state_snapshot=(cursor, last_complete_at) (vía run_scope_task individual,
      que NO reclama): el ESTADO del scope NO ha cambiado desde antes del fetch. La clave es
      `last_complete_at`: recibe un now() FRESCO (≠ el previo) en cada cosecha COMPLETA — que es
      EXACTAMENTE cuando run_all resetea consecutive_failures=0; si cambió, otro run cosechó y este
      quedó OBSOLETO → no debe pisar su estado. El VALOR del cursor NO basta (un feed estacionario
      re-escribe el mismo valor y daría falso-autoritativo — P2 rev. externa integral ronda 3).
      INVARIANTE del que depende: `runner.py` es el ÚNICO escritor de source_scope_state; si un
      futuro endpoint reseteara consecutive_failures sin tocar last_complete_at, el clobber volvería.
    - Sin ninguno (llamada directa legacy): autoritativo (comportamiento previo)."""
    if token is not None:
        return await _still_claim_owner(session, scope_id, token)
    if state_snapshot is _NO_SNAPSHOT:
        return True
    row = (
        await session.execute(
            sa.text(
                "SELECT cursor, last_complete_at FROM source_scope_state "
                "WHERE scope_id = :sid FOR UPDATE"
            ),
            {"sid": scope_id},
        )
    ).one_or_none()
    current = (row.cursor, row.last_complete_at) if row is not None else (None, None)
    return current == state_snapshot


async def _record_failure_safe(
    session, scope_id: str, token=None, state_snapshot=_NO_SNAPSHOT
) -> None:
    """Cuenta el fallo SIN tocar el cursor (insumo del backoff, ADR-05).

    FENCING (P1/P2 rev. externa integral ronda 2/3): solo cuenta el fallo si este run sigue siendo
    AUTORITATIVO sobre el scope (_still_authoritative) — por claim vigente (run_all) o por estado
    (cursor, last_complete_at) inalterado (run_scope_task sin claim). Un run obsoleto (lease vencido
    y re-armado, o superado por otro que ya cosechó con consecutive_failures=0) NO debe incrementar
    el contador ni disparar un backoff espurio sobre el estado del vigente.

    NUNCA propaga: si la propia contabilización falla (p.ej. BD caída — la
    causa probable del fallo original), se loguea y el runner devuelve igual
    su ScopeRunResult con el error ORIGINAL.
    """
    try:
        await session.rollback()  # asegura sesión utilizable tras el fallo previo
        if not await _still_authoritative(session, scope_id, token, state_snapshot):
            await session.rollback()
            logger.info(
                "scope %s: run OBSOLETO (otro run avanzó/reclama el scope) — fallo NO contabilizado",
                scope_id,
            )
            return
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
