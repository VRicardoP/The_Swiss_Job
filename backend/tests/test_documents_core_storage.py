"""E.2 transport contract; routing remains local until the migration cutover."""
import hashlib
import json
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from config import settings
from services.documents import core_client
from services.documents.port import CoreUnavailableError
from services.documents.seam import FallbackDocuments

PID = uuid.UUID("dd477509-a10b-4f31-924b-f9395958bc32")
JOB = "a" * 32


def document(**changes):
    return {"id": str(uuid.uuid4()), "profile_id": str(PID), "source_ref": JOB,
            "doc_type": "cv", "content": "Original résumé", "language": "fr",
            "output_hash": hashlib.sha256("Original résumé".encode()).hexdigest(),
            "created_at": "2026-09-08T09:00:00Z", "version": 1, "async_state": "ready",
            "context": {"job_title": "Engineer", "job_company": "Example"}, **changes}


def client(monkeypatch, handler, profile=PID):
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "test.key")
    monkeypatch.setattr(core_client, "resolve_core_profile_id", AsyncMock(return_value=profile))
    return core_client.CoreDocuments(object(), client_factory=lambda: httpx.AsyncClient(
        base_url="http://core/v1/", transport=httpx.MockTransport(handler)))


async def test_document_create_retries_same_operation_without_new_key(monkeypatch):
    doc, calls = document(), []
    operation = uuid.uuid4()
    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            raise httpx.ReadTimeout("after remote commit", request=req)
        assert json.loads(req.content)["source_ref"] == JOB
        return httpx.Response(201, json=doc)
    core = client(monkeypatch, handler)
    for attempt in range(2):
        if attempt == 0:
            with pytest.raises(CoreUnavailableError):
                await core.create(uuid.uuid4(), JOB, "cv", doc["content"], "fr", job_title="Engineer", job_company="Example", operation_id=operation)
        else:
            result = await core.create(uuid.uuid4(), JOB, "cv", doc["content"], "fr", job_title="Engineer", job_company="Example", operation_id=operation)
            assert result.id == uuid.UUID(doc["id"]) and result.job_hash == JOB
    assert [r.headers["Idempotency-Key"] for r in calls] == [str(operation)] * 2


@pytest.mark.parametrize("changes", [
    {"profile_id": str(uuid.uuid4())}, {"source_ref": "wrong"}, {"output_hash": "0" * 64},
    {"content": []}, {"context": {"job_title": []}}, {"created_at": "2026-09-08"},
    {"version": 2}, {"async_state": "pending"},
])
async def test_document_corrupt_200_is_unavailable(monkeypatch, changes):
    core = client(monkeypatch, lambda r: httpx.Response(200, json={"items": [document(**changes)], "next_cursor": None}))
    with pytest.raises(CoreUnavailableError):
        await core.list(uuid.uuid4(), JOB)


async def test_document_pages_and_repeated_cursor_fail_closed(monkeypatch):
    a, b = document(), document(created_at="2026-09-07T09:00:00Z")
    requests = []
    def handler(req):
        requests.append(req)
        assert req.url.params["source_ref"] == JOB
        return httpx.Response(200, json={"items": [a if len(requests)==1 else b],
                                        "next_cursor": "next" if len(requests)==1 else None})
    core = client(monkeypatch, handler)
    result = await core.list(uuid.uuid4(), JOB)
    assert result.total == 2 and [str(d.id) for d in result.data] == [a["id"], b["id"]]
    assert requests[1].url.params["cursor"] == "next"
    core = client(monkeypatch, lambda r: httpx.Response(200, json={"items": [document()], "next_cursor": "loop"}))
    with pytest.raises(CoreUnavailableError):
        await core.list(uuid.uuid4(), JOB)


async def test_document_missing_config_or_identity_never_opens_http(monkeypatch):
    def no_http(req):
        pytest.fail("must not open HTTP")
    core = client(monkeypatch, no_http, profile=None)
    with pytest.raises(CoreUnavailableError):
        await core.list(uuid.uuid4(), JOB)
    core = client(monkeypatch, no_http)
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "")
    with pytest.raises(CoreUnavailableError):
        await core.list(uuid.uuid4(), JOB)


async def test_document_delete_uses_etag_and_distinguishes_forbidden(monkeypatch):
    doc, requests = document(), []
    def handler(req):
        requests.append(req)
        if req.method == "GET":
            return httpx.Response(200, json=doc, headers={"ETag": '"document-etag"'})
        assert req.headers["If-Match"] == '"document-etag"'
        return httpx.Response(204)
    core = client(monkeypatch, handler)
    assert await core.delete(uuid.uuid4(), uuid.UUID(doc["id"])) is True
    assert [r.method for r in requests] == ["GET", "DELETE"]
    core = client(monkeypatch, lambda r: httpx.Response(404))
    assert await core.delete(uuid.uuid4(), uuid.uuid4()) is False
    core = client(monkeypatch, lambda r: httpx.Response(403))
    with pytest.raises(CoreUnavailableError):
        await core.delete(uuid.uuid4(), uuid.uuid4())


async def test_document_create_requires_explicit_operation(monkeypatch):
    core = client(monkeypatch, lambda r: pytest.fail("operation id required before HTTP"))
    with pytest.raises(CoreUnavailableError):
        await core.create(uuid.uuid4(), JOB, "cv", "content", "en")


async def test_canary_never_sends_writes_to_core():
    primary, local = AsyncMock(), AsyncMock()
    local.create.return_value = "local-result"
    local.delete.return_value = True
    seam = FallbackDocuments(primary, local)
    assert await seam.create(uuid.uuid4(), JOB, "cv", "content", "en") == "local-result"
    assert await seam.delete(uuid.uuid4(), uuid.uuid4()) is True
    primary.create.assert_not_called()
    primary.delete.assert_not_called()

@pytest.mark.parametrize("change", [
    {"version": True},
    {"context": {"job_title": "wrong", "job_company": "Example"}},
])
async def test_document_create_rejects_incompatible_receipt(monkeypatch, change):
    doc = document(**change)
    core = client(monkeypatch, lambda r: httpx.Response(201, json=doc))
    with pytest.raises(CoreUnavailableError):
        await core.create(
            uuid.uuid4(), JOB, "cv", "Original résumé", "fr",
            job_title="Engineer", job_company="Example", operation_id=uuid.uuid4(),
        )
