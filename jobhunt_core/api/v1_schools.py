"""Consumer-scoped school monitors. BFF admin/auth remains at the BFF boundary.

Receipts contain IDs only (no contact information). Updates require a current
ETag and take a row lock. A core outage must never select a local writer.
"""

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request

from jobhunt_core import documents, schools
from jobhunt_core.api import school_schemas as schemas
from jobhunt_core.api.deps import (
    ApiError,
    ensure_json_storable,
    error_404,
    get_session,
    require_scope,
)
from jobhunt_core.api.http_contract import (
    check_if_match,
    json_response,
    request_hash,
    with_etag,
)
from jobhunt_core.api.idempotency import run_idempotent
from jobhunt_core.api.v1 import decode_vacancy_cursor, encode_vacancy_cursor

router = APIRouter(prefix="/v1", tags=["schools"])


def _dto(row):
    return schemas.MonitorDTO(**row).model_dump(mode="json")


def _key(request):
    key = request.headers.get("idempotency-key")
    if key is None:
        raise ApiError(
            400, "idempotency_key_required", "la escritura exige Idempotency-Key"
        )
    return key


def _precondition(request, row):
    if request.headers.get("if-match") is None:
        raise ApiError(428, "precondition_required", "la edición exige If-Match")
    check_if_match(request, row)


@router.get("/schools", response_model=schemas.MonitorsPage)
async def list_monitors(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = None,
    session=Depends(get_session),
    principal=Depends(require_scope("schools:read")),
):
    cur = decode_vacancy_cursor(cursor) if cursor else None
    items, following = await schools.monitors(
        session, principal.consumer_id, limit, cur
    )
    return with_etag(
        request,
        {
            "items": [_dto(row) for row in items],
            "next_cursor": encode_vacancy_cursor(*following) if following else None,
        },
    )


@router.post("/schools", status_code=201, response_model=schemas.MonitorDTO)
async def create_monitor(
    body: schemas.MonitorCreate,
    request: Request,
    session=Depends(get_session),
    principal=Depends(require_scope("schools:write")),
):
    key = _key(request)
    values = body.model_dump(mode="json")
    ensure_json_storable(values)

    async def handler():
        mid = await schools.create_monitor(
            session, principal.consumer_id, body.external_ref, values["settings"]
        )
        if mid is None:
            raise ApiError(409, "school_exists", "el monitor ya existe")
        return 201, {"id": str(mid)}

    code, receipt = await run_idempotent(
        session, principal, "POST /v1/schools", request_hash(values), key, handler
    )
    row = await schools.monitor(
        session, principal.consumer_id, uuid.UUID(receipt["id"])
    )
    if row is None:
        raise error_404("monitor")
    return json_response(code, _dto(row))


@router.get("/schools/{monitor_id}", response_model=schemas.MonitorDTO)
async def get_monitor(
    monitor_id: uuid.UUID,
    request: Request,
    session=Depends(get_session),
    principal=Depends(require_scope("schools:read")),
):
    row = await schools.monitor(session, principal.consumer_id, monitor_id)
    if row is None:
        raise error_404("monitor")
    return with_etag(request, _dto(row))


@router.put("/schools/{monitor_id}", response_model=schemas.MonitorDTO)
async def update_monitor(
    monitor_id: uuid.UUID,
    body: schemas.SchoolSettings,
    request: Request,
    session=Depends(get_session),
    principal=Depends(require_scope("schools:write")),
):
    key = _key(request)
    values = body.model_dump(mode="json")
    ensure_json_storable(values)
    row = await schools.monitor(session, principal.consumer_id, monitor_id, write=True)
    if row is None:
        raise error_404("monitor")

    async def handler():
        _precondition(request, _dto(row))
        if values["country"].upper() != row["settings"]["country"].upper():
            raise ApiError(
                409, "school_identity_immutable", "el país es parte de la identidad"
            )
        await schools.update_monitor(session, principal.consumer_id, monitor_id, values)
        return 200, {"id": str(monitor_id)}

    code, _ = await run_idempotent(
        session,
        principal,
        f"PUT /v1/schools/{monitor_id}",
        request_hash(values),
        key,
        handler,
    )
    current = await schools.monitor(session, principal.consumer_id, monitor_id)
    if current is None:
        raise error_404("monitor")
    return json_response(code, _dto(current))


@router.delete("/schools/{monitor_id}", status_code=204)
async def delete_monitor(
    monitor_id: uuid.UUID,
    request: Request,
    session=Depends(get_session),
    principal=Depends(require_scope("schools:write")),
):
    key = _key(request)
    row = await schools.monitor(session, principal.consumer_id, monitor_id, write=True)

    async def handler():
        if row is None:
            raise error_404("monitor")
        _precondition(request, _dto(row))
        if not await schools.delete_monitor(session, principal.consumer_id, monitor_id):
            raise ApiError(
                409, "school_in_use", "el monitor conserva ofertas o candidaturas"
            )
        return 204, {}

    code, _ = await run_idempotent(
        session,
        principal,
        f"DELETE /v1/schools/{monitor_id}",
        request_hash({}),
        key,
        handler,
    )
    return json_response(code, None)


@router.get(
    "/profiles/{profile_id}/school-preferences",
    response_model=schemas.SchoolPreferences,
)
async def get_preferences(
    profile_id: uuid.UUID,
    request: Request,
    session=Depends(get_session),
    principal=Depends(require_scope("schools:read")),
):
    if await documents.owner(session, profile_id, principal.consumer_id) is None:
        raise error_404("perfil")
    enabled = (
        await session.execute(
            sa.text(
                "SELECT enabled FROM school_profile_preferences WHERE profile_id=:p"
            ),
            {"p": profile_id},
        )
    ).scalar_one_or_none()
    return with_etag(request, {"enabled": enabled if enabled is not None else False})


@router.put(
    "/profiles/{profile_id}/school-preferences",
    response_model=schemas.SchoolPreferences,
)
async def put_preferences(
    profile_id: uuid.UUID,
    body: schemas.SchoolPreferences,
    request: Request,
    session=Depends(get_session),
    principal=Depends(require_scope("schools:write")),
):
    key = _key(request)
    if (
        await documents.owner(session, profile_id, principal.consumer_id, write=True)
        is None
    ):
        raise error_404("perfil")

    async def handler():
        enabled = (
            await session.execute(
                sa.text(
                    "SELECT enabled FROM school_profile_preferences WHERE profile_id=:p"
                ),
                {"p": profile_id},
            )
        ).scalar_one_or_none()
        _precondition(request, {"enabled": enabled if enabled is not None else False})
        await session.execute(
            sa.text(
                "INSERT INTO school_profile_preferences(profile_id,enabled) VALUES (:p,:e) "
                "ON CONFLICT(profile_id) DO UPDATE SET enabled=excluded.enabled,updated_at=clock_timestamp()"
            ),
            {"p": profile_id, "e": body.enabled},
        )
        return 200, {"enabled": body.enabled}

    code, result = await run_idempotent(
        session,
        principal,
        f"PUT /v1/profiles/{profile_id}/school-preferences",
        request_hash(body.model_dump()),
        key,
        handler,
        profile_id=profile_id,
    )
    return json_response(code, result)
