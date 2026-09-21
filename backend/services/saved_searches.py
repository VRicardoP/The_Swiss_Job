"""SwissJob search adapter: core authority, existing UI shape, no local fallback.

Routing remains local until its explicit handover. Existing imported identities
must be reconciled before that flip; names are never treated as unique keys.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import select

from config import settings
from models.jobhunt_routing import CONSUMER_SWISSJOB, PROFILE_WILDCARD, JobhuntRouting
from schemas.saved_searches import SavedSearchListResponse, SavedSearchResponse
from services.matching.identity import resolve_core_profile_id
from services.routing import MODE_CORE_PRIMARY, MODE_ROLLBACK_PENDING

CAPABILITY = "saved_searches"


def block_search_writes(request: Request):
    if settings.SAVED_SEARCH_WRITES_FROZEN and request.method not in {"GET", "HEAD", "OPTIONS"}:
        raise HTTPException(503, "Saved-search writes temporarily frozen")


class SearchCoreError(Exception):
    def __init__(self, status=503, message="Saved-search core unavailable"):
        self.status = status
        super().__init__(message)


class _Search(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    filters: dict
    min_score: int = Field(strict=True, ge=0, le=100)
    notify_frequency: Literal["realtime", "daily", "weekly"]
    notify_push: bool = Field(strict=True)
    is_active: bool = Field(strict=True)
    last_run_at: AwareDatetime | None
    total_matches: int = Field(strict=True, ge=0)
    created_at: AwareDatetime


class _Page(BaseModel):
    items: list[_Search]
    next_cursor: str | None = None


async def core_owns_searches(db, user_id):
    # Fresh routing for this durable authority; no TTL window at cutover.
    rows = (await db.execute(select(JobhuntRouting.profile_id, JobhuntRouting.mode).where(
        JobhuntRouting.consumer_id == CONSUMER_SWISSJOB,
        JobhuntRouting.capability == CAPABILITY,
        JobhuntRouting.profile_id.in_([user_id, PROFILE_WILDCARD]),
    ))).all()
    modes = dict(rows)
    mode = modes.get(user_id, modes.get(PROFILE_WILDCARD, "local"))
    return mode in {MODE_CORE_PRIMARY, MODE_ROLLBACK_PENDING}


def default_client_factory():
    return httpx.AsyncClient(
        base_url=settings.CORE_API_BASE_URL,
        headers={"Authorization": f"Bearer {settings.CORE_CONSUMER_KEY}"},
        timeout=settings.CORE_HTTP_TIMEOUT_SECONDS,
    )


class CoreSavedSearches:
    def __init__(self, db, user_id, client_factory=None):
        self.db, self.user_id = db, user_id
        self.client_factory = client_factory or default_client_factory

    @asynccontextmanager
    async def _client(self):
        if not settings.CORE_CONSUMER_KEY:
            raise SearchCoreError()
        pid = await resolve_core_profile_id(self.db, self.user_id)
        if pid is None:
            raise SearchCoreError()
        try:
            async with asyncio.timeout(max(float(settings.CORE_HTTP_TIMEOUT_SECONDS), 1) * 2):
                async with self.client_factory() as client:
                    yield client, pid
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError):
            # Never echo response content or preferences from failed validation.
            raise SearchCoreError() from None

    def _view(self, row, pid, sid=None):
        if row.profile_id != pid or (sid is not None and row.id != sid):
            raise SearchCoreError()
        return SavedSearchResponse.model_validate({**row.model_dump(), "user_id": self.user_id})

    @staticmethod
    def _require(response, expected):
        if response.status_code == expected:
            return
        if response.status_code == 404:
            raise SearchCoreError(404, "Saved search not found")
        if response.status_code in {400, 409, 412, 422}:
            raise SearchCoreError(response.status_code, "Saved-search operation rejected; refresh and retry")
        raise SearchCoreError()

    async def list(self, limit=50, offset=0):
        async with self._client() as (client, pid):
            rows, seen_ids, cursors = [], set(), set()
            cursor = None
            for _ in range(100):
                params = {"profile": str(pid), "limit": 100}
                if cursor is not None:
                    params["cursor"] = cursor
                response = await client.get("/saved-searches", params=params)
                # A missing profile is broken provisioning, not an empty list.
                if response.status_code != 200:
                    raise SearchCoreError()
                page = _Page.model_validate(response.json())
                for row in page.items:
                    if row.id in seen_ids:
                        raise SearchCoreError()
                    rows.append(self._view(row, pid))
                    seen_ids.add(row.id)
                cursor = page.next_cursor
                if cursor is None:
                    return SavedSearchListResponse(data=rows[offset:offset+limit], total=len(rows))
                if not cursor or cursor in cursors:
                    raise SearchCoreError()
                cursors.add(cursor)
            raise SearchCoreError()

    async def create(self, values, operation_id):
        async with self._client() as (client, pid):
            response = await client.post("/saved-searches", json={
                **values, "profile_id": str(pid), "execution_contract": "swissjob-v1",
            }, headers={"Idempotency-Key": str(operation_id)})
            if response.status_code == 404:
                raise SearchCoreError()  # missing profile, not missing search
            self._require(response, 201)
            return self._view(_Search.model_validate(response.json()), pid)

    async def _owned(self, client, pid, sid):
        response = await client.get(f"/saved-searches/{sid}")
        self._require(response, 200)
        self._view(_Search.model_validate(response.json()), pid, sid)
        etag = response.headers.get("etag", "")
        if not (etag.startswith('"') and etag.endswith('"') and len(etag) > 2):
            raise SearchCoreError()
        return etag

    async def update(self, sid, values):
        async with self._client() as (client, pid):
            etag = await self._owned(client, pid, sid)
            response = await client.put(f"/saved-searches/{sid}", json=values,
                headers={"If-Match": etag, "Idempotency-Key": str(uuid.uuid4())})
            self._require(response, 200)
            return self._view(_Search.model_validate(response.json()), pid, sid)

    async def delete(self, sid):
        async with self._client() as (client, pid):
            etag = await self._owned(client, pid, sid)
            response = await client.delete(f"/saved-searches/{sid}",
                headers={"If-Match": etag, "Idempotency-Key": str(uuid.uuid4())})
            self._require(response, 204)

    async def run(self, sid):
        async with self._client() as (client, pid):
            await self._owned(client, pid, sid)
            response = await client.post(f"/saved-searches/{sid}/run")
            self._require(response, 202)
            result = response.json()
            if result != {"status": "dispatched", "search_id": str(sid)}:
                raise SearchCoreError()
            return result
