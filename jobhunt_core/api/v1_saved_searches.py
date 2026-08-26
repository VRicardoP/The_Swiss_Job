"""Endpoints /v1 de búsquedas guardadas (C-4, DISEÑO v2.1).

Espejo 1:1 del puerto real (`SAVED_SEARCH_OPS`): list, create, update, delete.
Scopes exactos `saved_searches:read`/`saved_searches:write` (H13); ownership
por JOIN → 404 indistinguible (Decisión 7); ETag + If-Match FUERTE bajo FOR
UPDATE (Decisión 2). **Idempotency-Key REQUERIDA en el POST** (R2-8: sin
UNIQUE natural tras retirar el de name, la key es el candado anti-duplicado;
400 idempotency_key_required si falta — único verbo donde es obligatoria).
PUT completo SOLO de client-writable; los ausentes conservan el valor vigente
y los engine-owned se IGNORAN (Decisión 5). Cursor keyset (created_at DESC,
id DESC) — Decisión 10.
"""

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request

from jobhunt_core import applications as apps
from jobhunt_core import saved_searches as searches
from jobhunt_core.api import schemas
from jobhunt_core.api.deps import (
    ApiError,
    Principal,
    error_404,
    get_session,
    require_scope,
)
from jobhunt_core.api.idempotency import run_idempotent
from jobhunt_core.api.v1 import (
    MAX_PAGE_LIMIT,
    _with_etag,
    decode_vacancy_cursor,
    encode_vacancy_cursor,
)
from jobhunt_core.api.v1_applications import (
    _WRITE_RESPONSES,
    check_if_match,
    json_response,
    request_hash,
)

router = APIRouter(
    prefix="/v1",
    responses={
        400: {"model": schemas.ErrorDTO},
        401: {"model": schemas.ErrorDTO},
        403: {"model": schemas.ErrorDTO},
        404: {"model": schemas.ErrorDTO},
    },
)


def _dto_json(values: dict) -> dict:
    return schemas.SavedSearchDTO(**values).model_dump(mode="json")


def _client_values(body, provided) -> dict:
    """SOLO los client-writable PRESENTES en `provided` (Decisión 5); valida
    filters a objeto (400 invalid_filters — R2-6). Un client-writable
    presente a null (name/min_score... no anulables) se trata como ausente.

    `filters` es la EXCEPCIÓN DELIBERADA (R2-6) y solo en el ALTA: ahí un null
    no tiene valor vigente que conservar y el default {} ACTIVO alertaría de
    todas las ofertas, así que se rechaza con 400. En el PUT sí hay valor
    vigente: quien llama retira `filters` de `provided` cuando llega a null,
    y la letra «presente a null = ausente» se cumple (G2-P3-5)."""
    values = {}
    for field in searches.CLIENT_WRITABLE:
        if field in provided and getattr(body, field) is not None:
            values[field] = getattr(body, field)
    if "filters" in provided:
        if not isinstance(body.filters, dict):
            raise ApiError(
                400, "invalid_filters", "filters debe ser un objeto JSON"
            )
        values["filters"] = body.filters
    return values


@router.get(
    "/saved-searches", response_model=schemas.SavedSearchesPageDTO,
    responses={304: {"description": "Not Modified"}},
)
async def list_saved_searches(
    request: Request,
    session=Depends(get_session),
    principal: Principal = Depends(require_scope("saved_searches:read")),
    profile: uuid.UUID = Query(...),
    limit: int = Query(20, ge=1, le=MAX_PAGE_LIMIT),
    cursor: str | None = Query(None),
):
    """Listado por perfil, keyset (created_at DESC, id DESC), cursor opaco.
    Cross-tenant/ausente → 404 indistinguible."""
    if await apps.profile_owner(session, profile, principal.consumer_id) is None:
        raise error_404("perfil")
    cur = decode_vacancy_cursor(cursor) if cursor else None
    items, next_cur = await searches.feed_page(session, profile, limit, cur)
    page = schemas.SavedSearchesPageDTO(
        items=[schemas.SavedSearchDTO(**i) for i in items],
        next_cursor=encode_vacancy_cursor(*next_cur) if next_cur else None,
    )
    return _with_etag(request, page.model_dump(mode="json"))


@router.post(
    "/saved-searches", status_code=201, response_model=schemas.SavedSearchDTO,
    responses=_WRITE_RESPONSES,
)
async def create_saved_search(
    request: Request,
    body: schemas.SavedSearchCreateDTO,
    session=Depends(get_session),
    principal: Principal = Depends(require_scope("saved_searches:write")),
):
    """Alta con Idempotency-Key REQUERIDA (R2-8): un reintento de red sin key
    crearía una búsqueda duplicada en silencio (no hay UNIQUE natural — H10).
    El replay devuelve el 201 ORIGINAL byte a byte."""
    idem_key = request.headers.get("idempotency-key")
    if idem_key is None:
        raise ApiError(
            400, "idempotency_key_required",
            "POST /v1/saved-searches exige el header Idempotency-Key",
        )
    route = f"POST {request.url.path}"
    req_hash = request_hash(body.model_dump(mode="json"))
    values = _client_values(body, body.model_fields_set)

    async def handler():
        consumer_name = await apps.profile_owner(
            session, body.profile_id, principal.consumer_id
        )
        if consumer_name is None:
            raise error_404("perfil")
        values["name"] = body.name
        search_id = await searches.create(
            session, profile_id=body.profile_id, values=values,
            destination=consumer_name,
        )
        row = await searches.fetch_owned(session, search_id, principal.consumer_id)
        return 201, _dto_json(searches.compose(row))

    status, payload = await run_idempotent(
        session, principal, route, req_hash, idem_key, handler
    )
    return json_response(status, payload)


@router.put(
    "/saved-searches/{search_id}", response_model=schemas.SavedSearchDTO,
    responses=_WRITE_RESPONSES,
)
async def update_saved_search(
    search_id: uuid.UUID,
    request: Request,
    body: schemas.SavedSearchPutDTO,
    session=Depends(get_session),
    principal: Principal = Depends(require_scope("saved_searches:write")),
):
    """PUT completo SOLO de client-writable (Decisión 5): ausentes conservan
    el valor vigente; engine-owned inmunes. If-Match bajo FOR UPDATE;
    revision+1 + evento `saved_search.changed` en la misma tx (Decisión 6)."""
    idem_key = request.headers.get("idempotency-key")
    route = f"PUT {request.url.path}"
    req_hash = request_hash(body.model_dump(mode="json", exclude_unset=True))
    provided = body.model_fields_set
    if body.filters is None:
        # G2-P3-5: en el PUT, un client-writable presente a null se trata como
        # AUSENTE — la letra del contrato (PUT-conserva) para TODOS los campos.
        # Antes `filters: null` era la única excepción y devolvía 400: un BFF
        # que serializa el objeto completo para no tocar los filtros perdía la
        # mutación ENTERA salvo que eliminara físicamente la clave del JSON.
        provided = provided - {"filters"}
    values = _client_values(body, provided)

    async def handler():
        row = await searches.fetch_owned(
            session, search_id, principal.consumer_id, for_update=True
        )
        if row is None:
            raise error_404("búsqueda guardada")
        check_if_match(request, _dto_json(searches.compose(row)))
        await searches.update(session, row, values, row.consumer_name)
        fresh = await searches.fetch_owned(session, search_id, principal.consumer_id)
        return 200, _dto_json(searches.compose(fresh))

    status, payload = await run_idempotent(
        session, principal, route, req_hash, idem_key, handler
    )
    return json_response(status, payload)


@router.delete(
    "/saved-searches/{search_id}", status_code=204, responses=_WRITE_RESPONSES,
)
async def delete_saved_search(
    search_id: uuid.UUID,
    request: Request,
    session=Depends(get_session),
    principal: Principal = Depends(require_scope("saved_searches:write")),
):
    """Borrado con If-Match bajo FOR UPDATE; 204. Emite el último
    `saved_search.changed` (revision+1, deleted=true) ANTES del DELETE —
    misma tx — para que un read-model en core_primary aprenda la baja."""
    idem_key = request.headers.get("idempotency-key")
    route = f"DELETE {request.url.path}"

    async def handler():
        row = await searches.fetch_owned(
            session, search_id, principal.consumer_id, for_update=True
        )
        if row is None:
            raise error_404("búsqueda guardada")
        check_if_match(request, _dto_json(searches.compose(row)))
        await searches.emit_changed(
            session, search_id=row.id, profile_id=row.profile_id,
            revision=row.revision + 1, destination=row.consumer_name,
            deleted=True,
        )
        await session.execute(
            sa.text("DELETE FROM saved_searches WHERE id = :id"), {"id": row.id}
        )
        return 204, None

    status, payload = await run_idempotent(
        session, principal, route, request_hash({}), idem_key, handler
    )
    return json_response(status, payload)
