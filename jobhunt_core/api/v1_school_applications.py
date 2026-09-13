"""Private watchlist state, separate from ordinary job applications.

No email or application submission occurs here. State changes require ownership
and a fresh ETag; drafts are never copied into idempotency receipts.
"""

import json
import uuid
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from jobhunt_core import documents, schools
from jobhunt_core.api.deps import (
    ApiError,
    ensure_json_storable,
    error_404,
    get_session,
    require_scope,
)
from jobhunt_core.api.http_contract import json_response, request_hash, with_etag
from jobhunt_core.api.idempotency import run_idempotent
from jobhunt_core.api.v1 import decode_vacancy_cursor, encode_vacancy_cursor
from jobhunt_core.api.v1_schools import _key, _precondition

router = APIRouter(
    prefix="/v1/profiles/{profile_id}/school-applications", tags=["schools"]
)

SchoolStatus = Literal[
    "detected",
    "reviewed",
    "drafted",
    "sent",
    "awaiting",
    "followup_due",
    "interview",
    "closed_positive",
    "closed_negative",
    "draft_ready",
    "awaiting_response",
    "follow_up_due",
    "interview_scheduled",
]


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monitor_id: uuid.UUID
    school_job_id: uuid.UUID | None = None
    source_ref: str = Field(min_length=1, max_length=300)
    status: SchoolStatus = "detected"
    draft_content: str | None = Field(default=None, max_length=1_000_000)
    context: dict = Field(default_factory=dict)


class ApplicationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: SchoolStatus | None = None
    draft_content: str | None = Field(default=None, max_length=1_000_000)
    context: dict | None = None


def _values(body):
    data = body.model_dump(mode="json", exclude_unset=True)
    ensure_json_storable(data)
    if len(json.dumps(data, ensure_ascii=False).encode()) > 1_200_000:
        raise ApiError(400, "school_application_too_large", "el estado excede la cota")
    return data


async def _owned(session, principal, profile_id, write=False):
    if (
        await documents.owner(session, profile_id, principal.consumer_id, write=write)
        is None
    ):
        raise error_404("perfil")


async def _get(session, profile_id, application_id):
    row = (
        (
            await session.execute(
                sa.text(
                    "SELECT * FROM school_applications WHERE profile_id=:p AND id=:id"
                ),
                {"p": profile_id, "id": application_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise error_404("candidatura escolar")
    return dict(row)


@router.get("")
async def list_applications(
    profile_id: uuid.UUID,
    request: Request,
    source_ref: str | None = Query(None, max_length=300),
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = None,
    session=Depends(get_session),
    principal=Depends(require_scope("schools:read")),
):
    await _owned(session, principal, profile_id)
    ensure_json_storable(source_ref)
    sql = "SELECT * FROM school_applications WHERE profile_id=:p"
    params = {"p": profile_id, "limit": limit + 1}
    if source_ref is not None:
        sql += " AND source_ref=:ref"
        params["ref"] = source_ref
    if cursor:
        params["ts"], params["id"] = decode_vacancy_cursor(cursor)
        sql += " AND (created_at,id)<(:ts,:id)"
    rows = (
        (
            await session.execute(
                sa.text(sql + " ORDER BY created_at DESC,id DESC LIMIT :limit"), params
            )
        )
        .mappings()
        .all()
    )
    items = [dict(row) for row in rows[:limit]]
    return with_etag(
        request,
        {
            "items": items,
            "next_cursor": (
                encode_vacancy_cursor(items[-1]["created_at"], items[-1]["id"])
                if len(rows) > limit
                else None
            ),
        },
    )


@router.get("/{application_id}")
async def get_application(
    profile_id: uuid.UUID,
    application_id: uuid.UUID,
    request: Request,
    session=Depends(get_session),
    principal=Depends(require_scope("schools:read")),
):
    await _owned(session, principal, profile_id)
    return with_etag(request, await _get(session, profile_id, application_id))


@router.post("", status_code=201)
async def create_application(
    profile_id: uuid.UUID,
    body: ApplicationCreate,
    request: Request,
    session=Depends(get_session),
    principal=Depends(require_scope("schools:write")),
):
    key = _key(request)
    values = _values(body)
    await _owned(session, principal, profile_id, write=True)

    async def handler():
        if (
            await schools.monitor(
                session, principal.consumer_id, body.monitor_id, write=True
            )
            is None
        ):
            raise error_404("monitor")
        if body.school_job_id is not None:
            linked = (
                await session.execute(
                    sa.text(
                        "SELECT id FROM school_job_details WHERE id=:id AND monitor_id=:m AND consumer_id=:c"
                    ),
                    {
                        "id": body.school_job_id,
                        "m": body.monitor_id,
                        "c": principal.consumer_id,
                    },
                )
            ).scalar_one_or_none()
            if linked is None:
                raise error_404("oferta escolar")
        aid = (
            await session.execute(
                sa.text(
                    "INSERT INTO school_applications(id,profile_id,consumer_id,monitor_id,school_job_id,"
                    "source_ref,status,draft_content,context) VALUES (:id,:p,:c,:m,:j,:ref,:status,:draft,CAST(:ctx AS jsonb)) "
                    "ON CONFLICT(profile_id,source_ref) DO NOTHING RETURNING id"
                ),
                {
                    "id": uuid.uuid4(),
                    "p": profile_id,
                    "c": principal.consumer_id,
                    "m": body.monitor_id,
                    "j": body.school_job_id,
                    "ref": body.source_ref,
                    "status": body.status,
                    "draft": body.draft_content,
                    "ctx": json.dumps(body.context),
                },
            )
        ).scalar_one_or_none()
        if aid is None:
            raise ApiError(
                409,
                "school_application_exists",
                "el estado ya existe; editar con If-Match",
            )
        return 201, {"id": str(aid)}

    code, result = await run_idempotent(
        session,
        principal,
        f"POST /v1/profiles/{profile_id}/school-applications",
        request_hash(values),
        key,
        handler,
        profile_id=profile_id,
    )
    return json_response(code, await _get(session, profile_id, uuid.UUID(result["id"])))


@router.patch("/{application_id}")
async def update_application(
    profile_id: uuid.UUID,
    application_id: uuid.UUID,
    body: ApplicationUpdate,
    request: Request,
    session=Depends(get_session),
    principal=Depends(require_scope("schools:write")),
):
    key = _key(request)
    values = _values(body)
    if any(
        values.get(field) is None for field in ("status", "context") if field in values
    ):
        raise ApiError(400, "invalid_school_state", "status/context no admiten null")
    await _owned(session, principal, profile_id, write=True)

    async def handler():
        row = await _get(session, profile_id, application_id)
        _precondition(request, row)
        # Match the existing watchlist: creating a draft advances only these two
        # states. A drafted letter must not reset an already sent application.
        next_status = values.get("status", row["status"])
        if (
            "status" not in values
            and values.get("draft_content")
            and next_status in ("detected", "reviewed")
        ):
            next_status = "drafted"
        await session.execute(
            sa.text(
                "UPDATE school_applications SET status=:status,draft_content=:draft,context=CAST(:ctx AS jsonb),"
                "version=version+1,updated_at=clock_timestamp() WHERE id=:id AND profile_id=:p"
            ),
            {
                "status": next_status,
                "draft": values.get("draft_content", row["draft_content"]),
                "ctx": json.dumps(values.get("context", row["context"])),
                "id": application_id,
                "p": profile_id,
            },
        )
        return 200, {"id": str(application_id)}

    code, _ = await run_idempotent(
        session,
        principal,
        f"PATCH /v1/profiles/{profile_id}/school-applications/{application_id}",
        request_hash(values),
        key,
        handler,
        profile_id=profile_id,
    )
    return json_response(code, await _get(session, profile_id, application_id))
