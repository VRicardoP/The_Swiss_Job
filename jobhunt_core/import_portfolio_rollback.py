"""Rollback FK-safe de la importación del portfolio (§4, parte 4; adelantado en LOCAL,
ejecución sobre datos reales gated al NAS).

Borra EXACTAMENTE las filas de la PROCEDENCIA EXACTA (parte 2) — lo que insertó ESTE run — en
orden child→parent, para deshacer la migración sin tocar nada ajeno. DESTRUCTIVO: NO commitea
(el llamador confirma tras revisar el informe). Del grafo FK del core (core0002/core0011):

- Única CASCADE: application_status_events→applications. El resto es NO ACTION (bloquea el
  borrado del padre mientras exista el hijo) salvo los punteros INTERNOS de `vacancies`
  (current_offer_revision_id / primary_incarnation_id / merged_into) que son SET NULL.
- CICLO: vacancies.current_offer_revision_id ↔ offer_revisions.vacancy_id (NOT NULL). Se
  rompe por el orden: se borran offer_revisions/incarnations ANTES que vacancies; al borrarlos,
  el SET NULL nulifica los punteros de la vacante (inofensivo para una vacante que se borra a
  continuación).
- SEGURIDAD (abort): si una vacante REUTILIZADA (no creada este run) apunta vía
  current_offer_revision_id/primary_incarnation_id a un offer_revision/incarnación de la
  PROCEDENCIA, borrarlo nulificaría su canónica → se ABORTA sin borrar nada (restaurar el
  puntero previo exige un snapshot pre-migración: paso del §4 completo / manual). El guard
  cubre esos DOS punteros SET NULL (los que pueden corromper en SILENCIO). El TERCER SET NULL,
  `merged_into`, NO se comprueba a propósito: los merges (Fase B) no se producen durante el
  cutover (freeze; la dedup solo deja dedup_candidates 'pending', jamás funde), y si un merge
  POST-commit apuntara a una vacante de la procedencia, sus propias filas dedup_candidates/
  merge_log (NO ACTION hacia la vacante) harían FALLAR el DELETE de forma RUIDOSA (sin commit),
  no en silencio. Este rollback está pensado para la ventana single-writer del cutover.

Marca además el CICLO DE VIDA de la fila durable del manifiesto (core0014: status
applied|rolled_back|rollback_aborted) si se le pasa `manifest_id`, para que un `verdict='ok'`
obsoleto tras el rollback no alimente un falso GATE-C (P1 rev. externa §4-LOCAL).

El módulo NO importa import_portfolio (opera solo sobre la procedencia) — sin ciclo.
"""

import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Orden de borrado child→parent (single-PK). Los compuestos y el abort-check se intercalan
# en rollback_migration en su posición correcta.
_SINGLE_PK_TABLES = (
    "application_status_events",  # hijo de applications (CASCADE, pero explícito)
    "applications",  # referencia vacancies/profiles
    "saved_searches",  # referencia profiles
    "link_evidence",  # referencia source_listings/vacancies
    "dedup_candidates",  # referencia vacancies
    "source_listing_revisions",  # hijo de incarnations
    "source_listing_incarnations",  # hijo de source_listings; referencia vacancies
    "offer_revisions",  # referencia vacancies (NO ACTION)
    "source_listings",  # referencia sources
    "vacancies",  # tras TODOS sus hijos
    "harvest_scopes",  # referencia sources
    "sources",  # raíz
)


async def _del_single(session: AsyncSession, table: str, ids: list[str]) -> int:
    """DELETE ... WHERE id IN ids (por texto, sin importar el tipo de la PK). `table` viene
    de la lista fija _SINGLE_PK_TABLES (no es input externo — sin inyección)."""
    if not ids:
        return 0
    result = await session.execute(
        sa.text(f"DELETE FROM {table} WHERE id::text IN :ids").bindparams(
            sa.bindparam("ids", expanding=True)
        ),
        {"ids": ids},
    )
    return result.rowcount


async def _del_composite(
    session: AsyncSession, table: str, c1: str, c2: str, ids: list[str]
) -> int:
    """DELETE de una tabla con PK COMPUESTA (ids = 'v1:v2'). Por fila (cardinalidad pequeña)."""
    count = 0
    for composite in ids:
        a, b = composite.split(":", 1)
        result = await session.execute(
            sa.text(f"DELETE FROM {table} WHERE {c1}::text = :a AND {c2}::text = :b"),
            {"a": a, "b": b},
        )
        count += result.rowcount
    return count


async def _reused_pointing_to_provenance(
    session: AsyncSession, provenance: dict[str, list[str]]
) -> list[str]:
    """IDs de vacantes REUTILIZADAS (no en la procedencia) que apuntan vía
    current_offer_revision_id / primary_incarnation_id a un offer_revision/incarnación de la
    PROCEDENCIA — borrar ese hijo nulificaría su canónica. Si las hay, el rollback ABORTA."""
    offrev = provenance.get("offer_revisions", [])
    inc = provenance.get("source_listing_incarnations", [])
    vac = provenance.get("vacancies", [])
    if not offrev and not inc:
        return []
    conds: list[str] = []
    binds: list = []
    params: dict = {}
    if offrev:
        conds.append("v.current_offer_revision_id::text IN :offrev")
        binds.append(sa.bindparam("offrev", expanding=True))
        params["offrev"] = offrev
    if inc:
        conds.append("v.primary_incarnation_id::text IN :inc")
        binds.append(sa.bindparam("inc", expanding=True))
        params["inc"] = inc
    sql = "SELECT v.id::text k FROM vacancies v WHERE (" + " OR ".join(conds) + ")"
    if vac:  # excluir las vacantes CREADAS este run (esas sí se borran)
        sql += " AND v.id::text NOT IN :vac"
        binds.append(sa.bindparam("vac", expanding=True))
        params["vac"] = vac
    rows = await session.execute(sa.text(sql).bindparams(*binds), params)
    return [r.k for r in rows]


async def _mark_manifest_status(
    session: AsyncSession, manifest_id: str | None, status: str
) -> None:
    """Marca el ciclo de vida de la fila durable del manifiesto (core0014): tras un rollback
    el `verdict='ok'` queda OBSOLETO; `status` (rolled_back|rollback_aborted) evita que el
    operador/GATE-C atesten un ok caduco (P1 rev. externa). No-op si no se pasa manifest_id."""
    if manifest_id is None:
        return
    await session.execute(
        sa.text("UPDATE portfolio_migration_manifest SET status = :s WHERE id = :id"),
        {"s": status, "id": manifest_id},
    )


async def _validate_manifest(session: AsyncSession, manifest_id: str) -> str | None:
    """Valida (con FOR UPDATE, ANTES de borrar) que `manifest_id` sea una fila 'applied' y la
    MÁS RECIENTE. Devuelve el motivo de abort o None si es válido. FAIL-CLOSED: si no existe,
    no está 'applied', o hay una fila 'applied' POSTERIOR (rollback LIFO: su evidencia obsoleta
    sobreviviría), se aborta SIN borrar nada (P1 rev. externa 2)."""
    row = (
        await session.execute(
            sa.text(
                "SELECT status, created_at FROM portfolio_migration_manifest "
                "WHERE id = :id FOR UPDATE"
            ),
            {"id": manifest_id},
        )
    ).first()
    if row is None:
        return f"manifest_id {manifest_id} no existe — fallo cerrado, no se borra nada"
    if row.status != "applied":
        return f"manifest {manifest_id} no está 'applied' (status={row.status}) — no se borra nada"
    later = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM portfolio_migration_manifest "
                "WHERE status = 'applied' AND created_at > :ts"
            ),
            {"ts": row.created_at},
        )
    ).scalar_one()
    if later:
        return (
            f"hay {later} manifiesto(s) 'applied' POSTERIOR(es) — deshaz el más reciente "
            f"primero (rollback LIFO); si no, su evidencia obsoleta sobreviviría"
        )
    return None


async def rollback_migration(
    session: AsyncSession, provenance: dict[str, list[str]], manifest_id: str | None = None
) -> dict:
    """Deshace la migración borrando las filas de `provenance` (parte 2) en orden FK-safe.
    DESTRUCTIVO y NO commitea. Si se pasa `manifest_id`, VALIDA su fila (applied + más reciente,
    fail-closed) ANTES de borrar y la marca con el estado resultante (rolled_back|
    rollback_aborted) para que un `verdict='ok'` obsoleto no alimente un falso GATE-C. Devuelve
    {status: 'rolled_back'|'aborted', deleted|reason}."""
    # VALIDACIÓN fail-closed ANTES de tocar nada: un manifest_id inexistente/no-applied/no-más-
    # reciente aborta SIN borrar (P1 rev. externa 2). El FOR UPDATE bloquea la fila hasta el
    # commit del llamador, así el estado no cambia bajo nuestros pies.
    if manifest_id is not None:
        abort_reason = await _validate_manifest(session, manifest_id)
        if abort_reason is not None:
            logger.error("import_portfolio_rollback: ABORTADO (validación) — %s", abort_reason)
            return {"status": "aborted", "reason": abort_reason}

    unsafe = await _reused_pointing_to_provenance(session, provenance)
    if unsafe:
        logger.error(
            "import_portfolio_rollback: ABORTADO — %d vacante(s) reutilizada(s) apuntan a "
            "corpus de la procedencia (borrarlo corrompería su canónica): %s",
            len(unsafe),
            unsafe[:5],
        )
        await _mark_manifest_status(session, manifest_id, "rollback_aborted")
        return {
            "status": "aborted",
            "reason": "vacantes reutilizadas apuntan a offer_revisions/incarnaciones de la "
            "procedencia; restaurar su puntero previo es un paso del §4 completo",
            "unsafe_vacancies": unsafe,
        }

    deleted: dict[str, int] = {}
    # Compuestos, en su posición del orden child→parent:
    deleted["profile_vacancy_state"] = await _del_composite(
        session, "profile_vacancy_state", "profile_id", "vacancy_id",
        provenance.get("profile_vacancy_state", []),
    )
    deleted["offer_revision_sources"] = await _del_composite(
        session, "offer_revision_sources", "offer_revision_id", "source_listing_revision_id",
        provenance.get("offer_revision_sources", []),
    )
    # Single-PK en orden:
    for table in _SINGLE_PK_TABLES:
        deleted[table] = await _del_single(session, table, provenance.get(table, []))

    await _mark_manifest_status(session, manifest_id, "rolled_back")
    logger.info("import_portfolio_rollback: rolled_back — %s", deleted)
    return {"status": "rolled_back", "deleted": deleted}
