"""Búsquedas guardadas: lógica de negocio de C-4 (DISEÑO v2.1, Decisión 5/6).

- client-writable = name, filters, min_score, notify_frequency, notify_push,
  is_active (columnas REALES de core0011); engine-owned de solo lectura =
  id, last_run_at, total_matches, created_at, updated_at — el PUT los IGNORA.
- `revision` monotónica por agregado (INSERT = 1) alimenta el evento del
  catálogo `saved_search.changed` (clave natural saved_search_id + revision),
  emitido al outbox en la MISMA tx de cada mutación persistente.
- SIN UNIQUE(profile_id, name) (H10): el candado anti-duplicado del POST vivo
  es la Idempotency-Key REQUERIDA (R2-8) — la exige el endpoint.
"""

import json
import uuid

import sqlalchemy as sa

from jobhunt_core import outbox

CHANGED_EVENT = "saved_search.changed"
# Campos que el cliente puede escribir (Decisión 5); el resto es engine-owned.
CLIENT_WRITABLE = (
    "name", "filters", "min_score", "notify_frequency", "notify_push",
    "is_active",
)

_ROW_SQL = (
    "SELECT ss.id, ss.profile_id, ss.name, ss.filters, ss.min_score, "
    "ss.notify_frequency, ss.notify_push, ss.is_active, ss.last_run_at, "
    "ss.total_matches, ss.created_at, ss.updated_at, ss.revision, "
    "c.name AS consumer_name "
    "FROM saved_searches ss "
    "JOIN profiles p ON p.id = ss.profile_id AND p.consumer_id = :cid "
    "JOIN consumers c ON c.id = p.consumer_id "
    "WHERE ss.id = :sid"
)


def compose(row) -> dict:
    """Representación DTO (el hash de ESTO es el ETag). `revision` NO forma
    parte de la representación (Decisión 2: no es el ETag, es la versión de
    eventos)."""
    return {
        "id": row.id, "profile_id": row.profile_id, "name": row.name,
        "filters": row.filters, "min_score": row.min_score,
        "notify_frequency": row.notify_frequency,
        "notify_push": row.notify_push, "is_active": row.is_active,
        "last_run_at": row.last_run_at, "total_matches": row.total_matches,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


async def fetch_owned(session, search_id, consumer_id, *, for_update=False):
    """Fila + ownership por JOIN (Decisión 7): None si cross-tenant o
    ausente (→ 404 indistinguible). `for_update` toma el lock de la fila
    ANTES de comparar el ETag (TOCTOU cerrado, Decisión 2)."""
    sql = _ROW_SQL + (" FOR UPDATE OF ss" if for_update else "")
    return (
        await session.execute(sa.text(sql), {"sid": search_id, "cid": consumer_id})
    ).one_or_none()


async def emit_changed(session, *, search_id, profile_id, revision: int,
                       destination: str, deleted: bool = False) -> None:
    """Evento del catálogo `saved_search.changed` (event_id = uuid5 de
    saved_search_id + revision) en la MISMA tx de la mutación."""
    await outbox.emit(
        session,
        event_type=CHANGED_EVENT,
        natural_key=f"{search_id}:{revision}",
        aggregate="saved_search",
        aggregate_id=str(search_id),
        subject_profile_id=profile_id,
        version=revision,
        payload={
            "saved_search_id": str(search_id),
            "profile_id": str(profile_id),
            "revision": revision,
            "deleted": deleted,
        },
        destination=destination,
    )


async def feed_page(session, profile_id, limit: int, cursor) -> tuple[list, tuple | None]:
    """Página keyset (created_at DESC, id DESC) — Decisión 10. Patrón
    limit+1: la fila extra solo decide has_more."""
    params = {"pid": profile_id, "lim": limit + 1}
    where = "WHERE ss.profile_id = :pid"
    if cursor is not None:
        where += " AND (ss.created_at < :cts OR (ss.created_at = :cts AND ss.id < :cid))"
        params["cts"], params["cid"] = cursor
    rows = (
        await session.execute(
            sa.text(
                "SELECT ss.id, ss.profile_id, ss.name, ss.filters, ss.min_score, "
                "ss.notify_frequency, ss.notify_push, ss.is_active, "
                "ss.last_run_at, ss.total_matches, ss.created_at, ss.updated_at "
                f"FROM saved_searches ss {where} "
                "ORDER BY ss.created_at DESC, ss.id DESC LIMIT :lim"
            ),
            params,
        )
    ).all()
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cur = (
        (page_rows[-1].created_at, page_rows[-1].id)
        if has_more and page_rows
        else None
    )
    return [compose(r) for r in page_rows], next_cur


async def create(session, *, profile_id, values: dict, destination: str) -> uuid.UUID:
    """INSERT (revision=1) + evento. `values` trae SOLO client-writable
    presentes; los ausentes toman el default del core (Decisión 5: daily/true)."""
    search_id = uuid.uuid4()
    await session.execute(
        sa.text(
            "INSERT INTO saved_searches "
            "(id, profile_id, name, filters, min_score, notify_frequency, "
            " notify_push, is_active, revision) "
            "VALUES (:id, :pid, :name, CAST(:filters AS jsonb), :ms, "
            ":nf, :np, :ia, 1)"
        ),
        {
            "id": search_id, "pid": profile_id,
            "name": values["name"],
            "filters": json.dumps(values.get("filters") or {}, ensure_ascii=False),
            "ms": values.get("min_score", 0),
            "nf": values.get("notify_frequency", "daily"),
            "np": values.get("notify_push", True),
            "ia": values.get("is_active", True),
        },
    )
    await emit_changed(
        session, search_id=search_id, profile_id=profile_id, revision=1,
        destination=destination,
    )
    return search_id


async def update(session, row, values: dict, destination: str) -> None:
    """PUT completo SOLO de client-writable presentes en `values` (los
    ausentes conservan el valor vigente; engine-owned jamás se tocan).
    revision+1 + updated_at + evento, misma tx. La fila viene BLOQUEADA
    (fetch_owned for_update=True)."""
    merged = {k: values.get(k, getattr(row, k)) for k in CLIENT_WRITABLE}
    new_revision = row.revision + 1
    await session.execute(
        sa.text(
            "UPDATE saved_searches SET name = :name, "
            "filters = CAST(:filters AS jsonb), min_score = :ms, "
            "notify_frequency = :nf, notify_push = :np, is_active = :ia, "
            "revision = :rev, updated_at = clock_timestamp() "
            "WHERE id = :id"
        ),
        {
            "id": row.id, "name": merged["name"],
            "filters": json.dumps(merged["filters"] or {}, ensure_ascii=False),
            "ms": merged["min_score"], "nf": merged["notify_frequency"],
            "np": merged["notify_push"], "ia": merged["is_active"],
            "rev": new_revision,
        },
    )
    await emit_changed(
        session, search_id=row.id, profile_id=row.profile_id,
        revision=new_revision, destination=destination,
    )
