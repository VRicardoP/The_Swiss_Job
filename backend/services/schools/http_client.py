"""School HTTP boundary shared by the watchlist reader and its state writer.

Only the consumer credential travels to the core. Source/user identities are
resolved by the BFF; no local SQL or LLM invocation belongs to this client.
"""

import asyncio
import uuid

import httpx
from pydantic import AwareDatetime, BaseModel, Field

from config import settings
from .port import CoreUnavailableError


class Monitor(BaseModel):
    id: uuid.UUID
    school_id: uuid.UUID
    external_ref: str
    settings: dict
    version: int = Field(ge=1)


class SchoolState(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    monitor_id: uuid.UUID
    source_ref: str
    status: str
    draft_content: str | None
    context: dict
    created_at: AwareDatetime
    updated_at: AwareDatetime
    version: int = Field(ge=1)


def default_client_factory():
    return httpx.AsyncClient(
        base_url=settings.CORE_API_BASE_URL,
        headers={"Authorization": f"Bearer {settings.CORE_CONSUMER_KEY}"},
        timeout=settings.CORE_HTTP_TIMEOUT_SECONDS,
    )


class SchoolClient:
    def __init__(self, client_factory=None):
        self._factory = client_factory or default_client_factory

    async def request(self, method, path, **kwargs):
        if not settings.CORE_CONSUMER_KEY and self._factory is default_client_factory:
            raise CoreUnavailableError("school core credential unavailable")
        try:
            async with asyncio.timeout(
                max(float(settings.CORE_HTTP_TIMEOUT_SECONDS), 1) * 2
            ):
                async with self._factory() as client:
                    response = await client.request(method, path, **kwargs)
            if response.status_code == 404:
                return response
            if response.status_code not in (200, 201, 204):
                raise CoreUnavailableError(f"school core HTTP {response.status_code}")
            if response.status_code != 204:
                if not isinstance(response.json(), dict) or not response.headers.get(
                    "etag"
                ):
                    raise CoreUnavailableError("invalid school response contract")
            return response
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError):
            raise CoreUnavailableError(
                "school core unavailable or invalid response"
            ) from None

    async def pages(self, path, params=None):
        result, seen, cursor = [], set(), None
        try:
            async with asyncio.timeout(
                max(float(settings.CORE_HTTP_TIMEOUT_SECONDS), 1) * 2
            ):
                for _ in range(100):
                    query = {"limit": 100, **(params or {})}
                    if cursor:
                        query["cursor"] = cursor
                    response = await self.request("GET", path, params=query)
                    if response.status_code == 404:
                        raise CoreUnavailableError("school profile unavailable")
                    page = response.json()
                    if not isinstance(page.get("items"), list):
                        raise CoreUnavailableError("invalid school page")
                    result.extend(page["items"])
                    cursor = page.get("next_cursor")
                    if cursor is None:
                        return result
                    if not isinstance(cursor, str) or not cursor or cursor in seen:
                        raise CoreUnavailableError("invalid school cursor")
                    seen.add(cursor)
        except (ValueError, TypeError, KeyError, TimeoutError):
            raise CoreUnavailableError(
                "invalid or timed out school collection"
            ) from None
        raise CoreUnavailableError("school page budget exhausted")

    async def monitors(self):
        try:
            rows = [Monitor.model_validate(r) for r in await self.pages("/schools")]
            if len({r.external_ref for r in rows}) != len(rows):
                raise CoreUnavailableError("repeated school identity")
            return rows
        except (ValueError, TypeError, KeyError):
            raise CoreUnavailableError("invalid school catalogue") from None

    async def states(self, profile_id, source_ref=None):
        try:
            rows = [
                SchoolState.model_validate(r)
                for r in await self.pages(
                    f"/profiles/{profile_id}/school-applications",
                    {"source_ref": source_ref} if source_ref is not None else None,
                )
            ]
            if any(
                r.profile_id != profile_id
                or (source_ref is not None and r.source_ref != source_ref)
                for r in rows
            ):
                raise CoreUnavailableError("school state ownership mismatch")
            return rows
        except (ValueError, TypeError, KeyError):
            raise CoreUnavailableError("invalid school state") from None

    async def write_state(
        self, profile_id, source_ref, monitor_id, changes, context=None
    ):
        path = f"/profiles/{profile_id}/school-applications"
        existing = await self.states(profile_id, source_ref)
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        if existing:
            path += f"/{existing[0].id}"
            current = await self.request("GET", path)
            if current.status_code == 404:
                raise CoreUnavailableError("school state changed during edit")
            headers["If-Match"] = current.headers["etag"]
            response = await self.request("PATCH", path, json=changes, headers=headers)
        else:
            body = {
                "monitor_id": str(monitor_id),
                "source_ref": source_ref,
                "context": context or {},
                **changes,
            }
            if changes.get("draft_content") and "status" not in changes:
                body["status"] = "drafted"
            response = await self.request("POST", path, json=body, headers=headers)
        try:
            row = SchoolState.model_validate(response.json())
            if (
                row.profile_id != profile_id
                or row.source_ref != source_ref
                or row.monitor_id != monitor_id
            ):
                raise CoreUnavailableError("school state identity mismatch")
            return row
        except (ValueError, TypeError, KeyError):
            raise CoreUnavailableError("invalid edited school state") from None

    async def preferences(self, profile_id, enabled=None):
        path = f"/profiles/{profile_id}/school-preferences"
        response = await self.request("GET", path)
        if response.status_code == 404:
            raise CoreUnavailableError("school preference owner unavailable")
        if enabled is not None:
            response = await self.request(
                "PUT",
                path,
                json={"enabled": enabled},
                headers={
                    "Idempotency-Key": str(uuid.uuid4()),
                    "If-Match": response.headers["etag"],
                },
            )
        body = response.json()
        if type(body.get("enabled")) is not bool:
            raise CoreUnavailableError("invalid school preferences")
        return body["enabled"]
