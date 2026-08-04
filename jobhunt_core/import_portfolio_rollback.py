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
- SEGURIDAD (abort, CASCADE): al borrar una `application` el CASCADE
  application_status_events→applications arrastra TODOS sus eventos; uno AJENO (fuera de la
  procedencia — p.ej. un cambio de estado registrado tras el freeze) se borraría en SILENCIO
  porque el rowcount de `applications` no cuenta los cascadeados. Antes de borrar se comprueba que
  los eventos que referencian a las applications de la procedencia son EXACTAMENTE
  provenance['application_status_events'] (`_cascade_event_mismatch`, con FOR UPDATE que bloquea
  inserts hijos concurrentes vía FOR KEY SHARE); cualquier ajeno o ausente ABORTA sin borrar ni
  marcar, recuperable (quitar el ajeno y reintentar) — P1 rev. externa §4-LOCAL ronda 7.
- FUERA DE ALCANCE (single-writer): el CUARTO SET NULL,
  applications.source_listing_incarnation_id→source_listing_incarnations (per-columna, core0011),
  NO se guarda — a diferencia de los tres de `vacancies` es una nulificación de puntero, no un
  borrado. Las applications de ESTE run nacen con ese puntero NULL (no se inserta la columna), y
  una application AJENA apuntando a una incarnación de la procedencia solo ocurre en una BD VIVA
  multi-writer, que este rollback no soporta. Su nulificación silenciosa queda para el §4-REAL.

Marca además el CICLO DE VIDA de la fila durable del manifiesto (core0014: status
applied|rolled_back|rollback_aborted) si se le pasa `manifest_id`, para que un `verdict='ok'`
obsoleto tras el rollback no alimente un falso GATE-C (P1 rev. externa §4-LOCAL).

El módulo NO importa import_portfolio (opera solo sobre la procedencia) — sin ciclo.
"""

import logging
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False

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

# Tablas de PK COMPUESTA (id = 'a:b'); sus ids deben contener el separador ':'.
_COMPOSITE_TABLES = ("profile_vacancy_state", "offer_revision_sources")


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


async def _validate_manifest(
    session: AsyncSession, manifest_id: str
) -> tuple[str | None, dict | None]:
    """Valida (con FOR UPDATE, ANTES de borrar) que `manifest_id` sea una fila 'applied' y la MÁS
    RECIENTE (por `seq`, orden total). Devuelve (motivo_abort|None, procedencia_ALMACENADA). La
    procedencia se lee del PROPIO manifiesto (manifest->'provenance'), NO de la que pase el
    llamador — así el borrado queda VINCULADO al manifest_id y no se puede borrar la procedencia
    de m1 marcando m2 (P1 rev. externa 3). FAIL-CLOSED: no existe / no 'applied' / hay una
    'applied' con seq mayor (LIFO) → abort sin borrar nada."""
    row = (
        await session.execute(
            sa.text(
                "SELECT status, seq, manifest FROM portfolio_migration_manifest "
                "WHERE id = :id FOR UPDATE"
            ),
            {"id": manifest_id},
        )
    ).first()
    if row is None:
        return f"manifest_id {manifest_id} no existe — fallo cerrado, no se borra nada", None
    if row.status != "applied":
        return (
            f"manifest {manifest_id} no está 'applied' (status={row.status}) — no se borra nada",
            None,
        )
    # LIFO: bloquear si hay un manifiesto POSTERIOR cuyos datos PUEDEN seguir presentes —
    # 'applied' O 'rollback_aborted' (un rollback inseguro NO borró sus datos). Deshacer el más
    # reciente primero, o su evidencia sobreviviría (P1 rev. externa 4).
    later = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM portfolio_migration_manifest "
                "WHERE status IN ('applied', 'rollback_aborted') AND seq > :seq"
            ),
            {"seq": row.seq},
        )
    ).scalar_one()
    if later:
        return (
            f"hay {later} manifiesto(s) POSTERIOR(es) con datos aún presentes (applied/"
            f"rollback_aborted) — deshaz el más reciente primero (rollback LIFO)",
            None,
        )
    # Procedencia ALMACENADA, validada FAIL-CLOSED: `manifest` debe ser objeto, contener la clave
    # 'provenance' EXPLÍCITAMENTE y ser un dict. Una {} es legítima (rerun idempotente); AUSENTE o
    # malformada → abort, JAMÁS marcar rolled_back sin borrar (P1 rev. externa 4).
    manifest_json = row.manifest
    if not isinstance(manifest_json, dict) or "provenance" not in manifest_json:
        return (
            f"manifest {manifest_id} malformado o sin clave 'provenance' — fallo cerrado, "
            f"no se borra ni se marca nada",
            None,
        )
    provenance = manifest_json["provenance"]
    if not isinstance(provenance, dict):
        return f"manifest {manifest_id}: 'provenance' no es un objeto — fallo cerrado", None
    # Cada valor debe ser list[str] de PKs VÁLIDAS: UUID para las simples, exactamente DOS UUID
    # ('uuid:uuid') para las compuestas. Una [] es legítima. Un null/escalar/objeto o una PK
    # malformada (no-UUID, ":" suelto, 'a:b:c') → abort: si no, el borrado no matcharía y se
    # marcaría rolled_back sin borrar (P1 rev. externa 5/6). La completitud del borrado se
    # re-verifica tras borrar (savepoint), por si un UUID VÁLIDO no existe (stale/inconsistente).
    for table, ids in provenance.items():
        if table not in _SINGLE_PK_TABLES and table not in _COMPOSITE_TABLES:
            # Una tabla que el rollback NO borra (deriva de esquema: una migración futura la
            # inserta y registra en la procedencia sin actualizar las listas de borrado; o
            # tamper) → dejaría residuo bajo un manifiesto marcado rolled_back. Fail-closed
            # (defense-in-depth, verificación ronda 6): productor y consumidor deben cubrir el
            # MISMO conjunto de tablas.
            return (
                f"manifest {manifest_id}: procedencia con tabla DESCONOCIDA '{table}' que el "
                f"rollback no borra — fallo cerrado",
                None,
            )
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            return (
                f"manifest {manifest_id}: procedencia['{table}'] no es list[str] — fallo cerrado",
                None,
            )
        if table in _COMPOSITE_TABLES:
            ok = all(
                len(parts := x.split(":")) == 2 and _is_uuid(parts[0]) and _is_uuid(parts[1])
                for x in ids
            )
        else:
            ok = all(_is_uuid(x) for x in ids)
        if not ok:
            return (
                f"manifest {manifest_id}: procedencia['{table}'] con PK malformada (no-UUID) — "
                f"fallo cerrado",
                None,
            )
    return None, provenance


async def _cascade_event_mismatch(
    session: AsyncSession, provenance: dict[str, list[str]]
) -> list[str]:
    """IDs de application_status_events donde el CASCADE (application_status_events.application_id
    → applications ON DELETE CASCADE) NO coincide con la procedencia: los que ACTUALMENTE
    referencian a las applications de la procedencia deben ser EXACTAMENTE
    provenance['application_status_events']. Un evento AJENO (añadido tras la migración) se
    borraría en SILENCIO al borrar su application (el rowcount de applications no lo cuenta) y el
    rollback afirmaría 'rolled_back' habiendo perdido una fila ajena (P1 rev. externa 7). Bloquea
    las applications (FOR UPDATE) para que no entre un evento nuevo entre la comprobación y el
    borrado. Devuelve la diferencia SIMÉTRICA (ajenos o ausentes); [] si coincide."""
    app_ids = provenance.get("applications", [])
    if not app_ids:
        return []
    await session.execute(
        sa.text("SELECT 1 FROM applications WHERE id::text IN :ids FOR UPDATE").bindparams(
            sa.bindparam("ids", expanding=True)
        ),
        {"ids": app_ids},
    )
    rows = await session.execute(
        sa.text(
            "SELECT id::text k FROM application_status_events WHERE application_id::text IN :ids"
        ).bindparams(sa.bindparam("ids", expanding=True)),
        {"ids": app_ids},
    )
    current = {r.k for r in rows}
    expected = set(provenance.get("application_status_events", []))
    return sorted(current ^ expected)


async def rollback_migration(session: AsyncSession, manifest_id: str) -> dict:
    """Deshace la migración del manifiesto `manifest_id` borrando su procedencia ALMACENADA
    (leída del propio manifiesto) en orden FK-safe. `manifest_id` es OBLIGATORIO: un rollback
    confirmado SIN él dejaría el manifiesto 'applied' tras borrar los datos → falso GATE-C
    (P1 rev. externa 5). No hay parámetro `provenance` (era atacable: borrar m1 marcando m2).
    DESTRUCTIVO y NO commitea. VALIDA fail-closed (applied + más reciente + procedencia bien
    formada) ANTES de borrar; marca la fila con el estado resultante (rolled_back|
    rollback_aborted). Devuelve {status: 'rolled_back'|'aborted', deleted|reason}."""
    # VALIDACIÓN fail-closed ANTES de tocar nada (FOR UPDATE bloquea la fila hasta el commit del
    # llamador). Se borra la procedencia ALMACENADA en el manifiesto, vinculada al id.
    abort_reason, provenance = await _validate_manifest(session, manifest_id)
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

    # El borrado de una application CASCADEA (application_status_events.application_id → applications
    # ON DELETE CASCADE) sus eventos: un evento AJENO añadido tras la migración (fuera de la
    # procedencia) se borraría en SILENCIO — el rowcount de applications no lo cuenta, así que el
    # control de cardinalidad no lo vería y se marcaría rolled_back perdiendo una fila ajena
    # (P1 rev. externa 7). Se comprueba ANTES de borrar; recuperable (quitar el ajeno y reintentar)
    # → aborta SIN marcar ni borrar, el manifiesto sigue 'applied'.
    cascade_mismatch = await _cascade_event_mismatch(session, provenance)
    if cascade_mismatch:
        logger.error(
            "import_portfolio_rollback: ABORTADO — los eventos que el CASCADE afectaría no "
            "coinciden con la procedencia (ajenos o ausentes): %s",
            cascade_mismatch[:5],
        )
        return {
            "status": "aborted",
            "reason": "eventos de application_status_events ajenos a la procedencia serían "
            "borrados por el CASCADE; el borrado explícito no los cuenta",
            "cascade_mismatch": cascade_mismatch,
        }

    # Borrado dentro de un SAVEPOINT: si alguna tabla NO borra EXACTAMENTE sus identidades
    # esperadas (borrado ≠ len(procedencia) — una PK stale/inconsistente que no existe), se
    # REVIERTE TODO el bloque y se aborta SIN marcar rolled_back — jamás dejar datos presentes
    # bajo un manifiesto que dice estar deshecho (P1 rev. externa 6).
    deleted: dict[str, int] = {}
    nested = await session.begin_nested()
    deleted["profile_vacancy_state"] = await _del_composite(
        session, "profile_vacancy_state", "profile_id", "vacancy_id",
        provenance.get("profile_vacancy_state", []),
    )
    deleted["offer_revision_sources"] = await _del_composite(
        session, "offer_revision_sources", "offer_revision_id", "source_listing_revision_id",
        provenance.get("offer_revision_sources", []),
    )
    for table in _SINGLE_PK_TABLES:
        deleted[table] = await _del_single(session, table, provenance.get(table, []))
    incomplete = {
        t: {"borrado": n, "esperado": len(provenance.get(t, []))}
        for t, n in deleted.items()
        if n != len(provenance.get(t, []))
    }
    if incomplete:
        await nested.rollback()
        logger.error(
            "import_portfolio_rollback: ABORTADO — borrado INCOMPLETO (procedencia "
            "inconsistente con el destino): %s",
            incomplete,
        )
        return {
            "status": "aborted",
            "reason": f"borrado incompleto: identidades no encontradas en {sorted(incomplete)}",
            "incomplete": incomplete,
        }
    await nested.commit()
    await _mark_manifest_status(session, manifest_id, "rolled_back")
    logger.info("import_portfolio_rollback: rolled_back — %s", deleted)
    return {"status": "rolled_back", "deleted": deleted}
