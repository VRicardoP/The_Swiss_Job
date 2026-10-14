"""Authenticated manual harvest dispatch for trusted service consumers."""

import hashlib
from datetime import datetime
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, Field

from jobhunt_core import runs, source_aggregate
from jobhunt_core.api.deps import ApiError, get_session, require_scope
from jobhunt_core.api.http_contract import json_response, request_hash, with_etag
from jobhunt_core.api.idempotency import run_idempotent
from jobhunt_core.tasks.harvest import run_scope_task

router = APIRouter(prefix="/v1/harvest", tags=["harvest"])


class HarvestRunAcceptedDTO(BaseModel):
    source: str
    status: Literal["accepted"]
    dispatched: int = Field(ge=1)
    scope_ids: list[str]


class SourceAggregateItemDTO(BaseModel):
    external_id: str
    url: str
    apply_url: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    title: str | None = None
    company: str | None = None
    description: str | None = None
    location: str | None = None
    salary: str | None = None
    salary_min: int | float | None = None
    salary_max: int | float | None = None
    salary_currency: str | None = None
    logo: str | None = None
    remote: bool | None = None
    tags: list[str] = []
    canton: str | None = None
    prof_group: str | None = None
    category: str | None = None
    categories: list[str] = []
    job_type: str | None = None
    job_types: list[str] = []
    published_at: str | int | float | None = None


class SourceAggregateCountsDTO(BaseModel):
    locations: dict[str, int]
    tags: dict[str, int]
    companies: dict[str, int]
    categories: dict[str, int]
    job_types: dict[str, int]
    cantons: dict[str, int]
    prof_groups: dict[str, int]
    remote: int = Field(ge=0)
    onsite: int = Field(ge=0)


class SourceAggregateDTO(BaseModel):
    source: str
    total: int = Field(ge=0)
    corpus_updated_at: datetime | None = None
    last_complete_at: datetime | None = None
    enabled_scopes: int = Field(ge=0)
    observed_scopes: int = Field(ge=0)
    max_consecutive_failures: int = Field(ge=0)
    update_in_progress: bool
    counts: SourceAggregateCountsDTO
    items: list[SourceAggregateItemDTO]


async def _enabled_scope_ids(session, source: str) -> list[str]:
    return [
        str(scope_id)
        for scope_id in (
            await session.execute(
                sa.text(
                    "SELECT hs.id FROM harvest_scopes hs "
                    "JOIN sources s ON s.id=hs.source_id "
                    "WHERE hs.enabled AND s.name=:source ORDER BY hs.id"
                ),
                {"source": source},
            )
        )
        .scalars()
        .all()
    ]


@router.get(
    "/sources/{source}/aggregate",
    response_model=SourceAggregateDTO,
    responses={304: {"description": "Not Modified"}},
)
async def get_source_aggregate(
    request: Request,
    source: str = Path(min_length=1, max_length=100),
    item_limit: int = Query(200, ge=1, le=500),
    top_n: int = Query(100, ge=1, le=500),
    session=Depends(get_session),
    principal=Depends(require_scope("vacancies:read")),
):
    """Return one bounded catalog snapshot for a registered source."""
    del principal
    source = source.strip().lower()
    if not source:
        raise ApiError(400, "invalid_source", "fuente vacía")
    aggregate = await source_aggregate.fetch_source_aggregate(
        session,
        source,
        item_limit=item_limit,
        top_n=top_n,
        lease=runs.SCOPE_LEASE_S,
    )
    if aggregate is None:
        raise ApiError(404, "not_found", "fuente no encontrada")
    payload = SourceAggregateDTO(**aggregate).model_dump(mode="json")
    return with_etag(request, payload)


async def _has_live_run(session, scope_ids: list[str]) -> bool:
    return bool(
        await session.scalar(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM source_harvest_runs "
                "WHERE scope_id=ANY(CAST(:scope_ids AS uuid[])) "
                "AND status='running' AND finished_at IS NULL "
                "AND COALESCE(heartbeat_at, started_at) >= "
                "clock_timestamp()-make_interval(secs => :lease))"
            ),
            {"scope_ids": scope_ids, "lease": runs.SCOPE_LEASE_S},
        )
    )


@router.post(
    "/sources/{source}/runs",
    status_code=202,
    response_model=HarvestRunAcceptedDTO,
    responses={409: {"description": "Harvest already in progress"}},
)
async def dispatch_source_harvest(
    request: Request,
    source: str = Path(min_length=1, max_length=100),
    session=Depends(get_session),
    principal=Depends(require_scope("harvest:write")),
):
    """Queue every enabled scope for one source without performing HTTP work here."""
    source = source.strip().lower()
    if not source:
        raise ApiError(400, "invalid_source", "fuente vacía")
    key = request.headers.get("idempotency-key")
    if key is None:
        raise ApiError(
            400,
            "idempotency_key_required",
            "el refresco de cosecha exige Idempotency-Key",
        )

    async def handler():
        scope_ids = await _enabled_scope_ids(session, source)
        if not scope_ids:
            raise ApiError(404, "not_found", "fuente habilitada no encontrada")
        if await _has_live_run(session, scope_ids):
            raise ApiError(
                409,
                "harvest_in_progress",
                "la cosecha de la fuente ya está en curso",
                {"source": source},
            )

        request_key = hashlib.sha256(key.strip().encode()).hexdigest()[:32]
        for scope_id in scope_ids:
            run_scope_task.apply_async(
                kwargs={
                    "scope_id": scope_id,
                    "run_key": f"api:{principal.consumer_id}:{request_key}:{scope_id}",
                },
                expires=21600,
            )
        return 202, {
            "source": source,
            "status": "accepted",
            "dispatched": len(scope_ids),
            "scope_ids": scope_ids,
        }

    status, payload = await run_idempotent(
        session,
        principal,
        f"POST /v1/harvest/sources/{source}/runs",
        request_hash({"source": source}),
        key,
        handler,
    )
    return json_response(status, payload)
