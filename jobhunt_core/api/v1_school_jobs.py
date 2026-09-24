"""School observations and delivery acknowledgements, scoped to one consumer."""

from datetime import date, datetime, timezone
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from jobhunt_core import school_ingest, schools
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

router = APIRouter(prefix="/v1", tags=["schools"])


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Only a live extractor may publish a missing vacancy; historical imports
    # and Swiss CDC reconciliation only link to an already-observed corpus.
    publish_missing: bool = Field(default=False, strict=True)
    title: str = Field(min_length=1, max_length=500)
    url: str | None = Field(default=None, max_length=2000)
    description_snippet: str | None = Field(default=None, max_length=65536)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    dedup_key: str = Field(min_length=1, max_length=300)
    source: str = Field(default="official", max_length=50)
    role_score: float = Field(default=0.0, ge=-1, le=1, allow_inf_nan=False)
    urgency_score: int = Field(default=0, ge=0, le=100)
    template: str | None = Field(default=None, pattern=r"^[AB]$")
    date_detected: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    date_posted: datetime | None = None
    deadline: date | None = None


async def _get(session, cid, jid):
    row = (
        (
            await session.execute(
                sa.text(
                    "SELECT * FROM school_job_details WHERE id=:id AND consumer_id=:c"
                ),
                {"id": jid, "c": cid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise error_404("oferta escolar")
    return dict(row)


@router.post("/schools/{monitor_id}/jobs")
async def observe(
    monitor_id: uuid.UUID,
    body: Observation,
    request: Request,
    session=Depends(get_session),
    principal=Depends(require_scope("schools:write")),
):
    key = _key(request)
    values = body.model_dump(exclude={"publish_missing"})
    # Timezones are explicit at the storage boundary; never inherit NAS/local TZ.
    for field in ("date_detected", "date_posted"):
        if values[field] is not None and values[field].tzinfo is None:
            raise ApiError(400, "timezone_required", f"{field} exige zona horaria")
    ensure_json_storable(body.model_dump(mode="json"))
    monitor = await schools.monitor(
        session, principal.consumer_id, monitor_id, write=True
    )
    if monitor is None:
        raise error_404("monitor")

    async def handler():
        jid, created = await school_ingest.record_observation(
            session, monitor, values, publish_missing=body.publish_missing
        )
        return (201 if created else 200), {"id": str(jid), "created": created}

    # The implicit detection timestamp is excluded unless supplied: retrying an
    # identical request without a timestamp must have the same request hash.
    code, receipt = await run_idempotent(
        session,
        principal,
        f"POST /v1/schools/{monitor_id}/jobs",
        request_hash(body.model_dump(mode="json", exclude_unset=True)),
        key,
        handler,
    )
    return json_response(
        code,
        {
            "item": await _get(
                session, principal.consumer_id, uuid.UUID(receipt["id"])
            ),
            "created": receipt["created"],
        },
    )


@router.get("/school-jobs")
async def list_jobs(
    request: Request,
    monitor_id: uuid.UUID | None = None,
    min_urgency: int = Query(0, ge=0, le=100),
    min_role: float = Query(-1, ge=-1, le=1, allow_inf_nan=False),
    since: datetime | None = None,
    pending: bool = False,
    dedup_key: list[str] = Query(default=[]),
    limit: int = Query(100, ge=1, le=500),
    cursor: str | None = None,
    session=Depends(get_session),
    principal=Depends(require_scope("schools:read")),
):
    sql = (
        "SELECT * FROM school_job_details WHERE consumer_id=:c "
        "AND CAST(metadata->>'urgency_score' AS integer)>=:urgency "
        "AND CAST(metadata->>'role_score' AS numeric)>=:role"
    )
    params = {
        "c": principal.consumer_id,
        "urgency": min_urgency,
        "role": min_role,
        "limit": limit + 1,
    }
    if monitor_id is not None:
        if await schools.monitor(session, principal.consumer_id, monitor_id) is None:
            raise error_404("monitor")
        sql += " AND monitor_id=:m"
        params["m"] = monitor_id
    if pending:
        sql += " AND notified_at IS NULL AND COALESCE(metadata->>'notified','false')='false'"
    if since is not None:
        if since.tzinfo is None:
            raise ApiError(400, "timezone_required", "since exige zona horaria")
        sql += " AND created_at>=:since"
        params["since"] = since
    if dedup_key:
        if len(dedup_key) > 2 or any(len(key) > 300 for key in dedup_key):
            raise ApiError(
                400, "invalid_dedup_keys", "se admiten dos claves de 300 caracteres"
            )
        ensure_json_storable(dedup_key)
        sql += " AND source_ref=ANY(:keys)"
        params["keys"] = dedup_key
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


@router.get("/school-jobs/{job_id}")
async def get_job(
    job_id: uuid.UUID,
    request: Request,
    session=Depends(get_session),
    principal=Depends(require_scope("schools:read")),
):
    return with_etag(request, await _get(session, principal.consumer_id, job_id))


@router.post("/school-jobs/{job_id}/notified")
async def acknowledge_notification(
    job_id: uuid.UUID,
    request: Request,
    session=Depends(get_session),
    principal=Depends(require_scope("schools:write")),
):
    key = _key(request)
    row = (
        (
            await session.execute(
                sa.text(
                    "SELECT * FROM school_job_details WHERE id=:id AND consumer_id=:c FOR UPDATE"
                ),
                {"id": job_id, "c": principal.consumer_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise error_404("oferta escolar")

    async def handler():
        _precondition(request, dict(row))
        await session.execute(
            sa.text(
                "UPDATE school_job_details SET notified_at=COALESCE(notified_at,clock_timestamp()),"
                "metadata=metadata||jsonb_build_object('notified',true),updated_at=clock_timestamp() WHERE id=:id"
            ),
            {"id": job_id},
        )
        return 200, {"id": str(job_id)}

    code, _ = await run_idempotent(
        session,
        principal,
        f"POST /v1/school-jobs/{job_id}/notified",
        request_hash({}),
        key,
        handler,
    )
    return json_response(code, await _get(session, principal.consumer_id, job_id))
