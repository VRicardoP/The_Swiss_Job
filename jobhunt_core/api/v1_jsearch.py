"""Authenticated JSearch live search; outbound traffic remains inside Core."""

from collections.abc import Callable
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from jobhunt_core.api.deps import ApiError, require_scope
from jobhunt_core.harvest.provider import ProviderConfigError, ProviderResponseError
from jobhunt_core.harvest.providers.native_jsearch import (
    SearchParams,
    live_job,
    search_raw,
)

router = APIRouter(prefix="/v1/providers/jsearch", tags=["providers"])
_client_factory: Callable[[], httpx.AsyncClient] = httpx.AsyncClient


class LiveJobDTO(BaseModel):
    job_id: str | int | None = None
    title: str | None = None
    employer: str | None = None
    employer_logo: str | None = None
    location: str
    is_remote: bool
    employment_type: str | None = None
    publisher: str | None = None
    apply_link: str | None = None
    posted_at: str | None = None
    min_salary: int | float | None = None
    max_salary: int | float | None = None
    salary_currency: str | None = None
    description_snippet: str


class LiveSearchFiltersDTO(BaseModel):
    date_posted: str
    employment_type: str | None = None
    remote_only: bool


class LiveSearchMetadataDTO(BaseModel):
    query: str
    country: str
    page: int
    results_count: int
    filters: LiveSearchFiltersDTO


class LiveSearchDTO(BaseModel):
    data: list[LiveJobDTO]
    metadata: LiveSearchMetadataDTO


@router.get("/search", response_model=LiveSearchDTO)
async def search_jsearch(
    query: str = Query(min_length=2, max_length=500),
    page: int = Query(1, ge=1, le=100),
    num_pages: int = Query(1, ge=1, le=10),
    country: str = Query("us", min_length=2, max_length=2, pattern="^[A-Za-z]{2}$"),
    date_posted: Literal["all", "today", "3days", "week", "month"] = "all",
    employment_type: Literal["FULLTIME", "PARTTIME", "CONTRACTOR", "INTERN"]
    | None = None,
    remote_only: bool = False,
    principal=Depends(require_scope("vacancies:read")),
):
    """Run the historical Portfolio live query without exposing its API key."""
    del principal
    params = SearchParams(
        query=query,
        page=page,
        num_pages=num_pages,
        country=country.lower(),
        date_posted=date_posted,
        employment_type=employment_type,
        remote_only=remote_only,
    )
    try:
        async with _client_factory() as client:
            rows = await search_raw(client, params)
    except ProviderConfigError as exc:
        raise ApiError(503, "source_unavailable", str(exc)) from exc
    except ProviderResponseError as exc:
        raise ApiError(503, "source_unavailable", "JSearch is unavailable") from exc
    jobs = [live_job(row) for row in rows]
    return {
        "data": jobs,
        "metadata": {
            "query": query,
            "country": params.country,
            "page": page,
            "results_count": len(jobs),
            "filters": {
                "date_posted": date_posted,
                "employment_type": employment_type,
                "remote_only": remote_only,
            },
        },
    }
