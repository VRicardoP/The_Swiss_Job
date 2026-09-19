"""F: old links keep working without using the retired local corpus."""

import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from config import settings
from services.catalog.core_client import CoreCatalog
from services.catalog.port import CoreUnavailableError


@pytest.mark.asyncio
async def test_legacy_catalog_link_resolves_in_core_without_local_database(monkeypatch):
    monkeypatch.setattr(settings, "CORE_FEEDBACK_ENABLED", True)
    vid, ref = str(uuid.uuid4()), "a" * 32
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/v1/listing-references":
            assert request.url.params["external_id"] == ref
            return httpx.Response(200, json={"vacancy_id": vid})
        assert request.url.path == f"/v1/vacancies/{vid}"
        return httpx.Response(200, json={"id": vid, "title": "Native", "company": "Company",
            "tags": [], "primary_listing": {"source": "remotive", "url": "https://example.test/job"}, "listings": []})

    db = AsyncMock()
    catalog = CoreCatalog(db=db, client_factory=lambda: httpx.AsyncClient(
        base_url="http://core.test/v1", transport=httpx.MockTransport(handler)))
    result = await catalog.get(ref)
    assert result.hash == vid and result.title == "Native"
    assert await catalog._resolve_legacy_hashes([result.url]) == {}
    db.execute.assert_not_called()
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [httpx.Response(409), httpx.Response(503), httpx.Response(200, json={"vacancy_id": "bad"})])
async def test_bad_alias_response_never_falls_back_to_local(monkeypatch, response):
    monkeypatch.setattr(settings, "CORE_FEEDBACK_ENABLED", True)
    db = AsyncMock()
    catalog = CoreCatalog(db=db, client_factory=lambda: httpx.AsyncClient(
        base_url="http://core.test/v1", transport=httpx.MockTransport(lambda _: response)))
    with pytest.raises(CoreUnavailableError):
        await catalog.get("b" * 32)
    db.execute.assert_not_called()
