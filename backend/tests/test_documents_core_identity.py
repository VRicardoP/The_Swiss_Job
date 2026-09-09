"""An offer served with its core UUID must support the existing document flow."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from models.job import Job
from services.catalog import CoreUnavailableError
from tests.test_documents import _auth, _gemini_on, _register_and_get_token, _set_cv_text


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["ok", "missing", "unavailable"])
async def test_document_for_core_offer_without_local_job(
    client, db_session, monkeypatch, outcome
):
    token, email = await _register_and_get_token(client)
    await _set_cv_text(db_session, email)
    ref = str(uuid.uuid4())
    catalog = SimpleNamespace(get=AsyncMock())
    catalog.get.return_value = SimpleNamespace(
        title="Core-only position", company="Core company", description="Core content",
        tags=["python"],
    ) if outcome == "ok" else None
    if outcome == "unavailable":
        catalog.get.side_effect = CoreUnavailableError("unavailable")
    resolver = AsyncMock(return_value=catalog)
    monkeypatch.setattr("routers.documents.resolve_catalog", resolver, raising=False)
    monkeypatch.setattr("routers.documents._get_gemini", _gemini_on)
    generate = AsyncMock(return_value="Generated from the existing core offer")
    monkeypatch.setattr("routers.documents.DocumentGeneratorService.generate_cv", generate)

    response = await client.post("/api/v1/documents/generate", headers=_auth(token),
                                 json={"job_hash": ref, "doc_type": "cv"})
    assert response.status_code == {"ok": 200, "missing": 404, "unavailable": 503}[outcome], response.text
    catalog.get.assert_awaited_once_with(ref)
    if outcome != "ok":
        generate.assert_not_awaited()
        return
    body = response.json()
    assert body["job_hash"] == ref
    assert body["job_title"] == "Core-only position"
    assert generate.await_args.kwargs["job_description"] == "Core content"
    listed = await client.get(f"/api/v1/documents/{ref}", headers=_auth(token))
    assert listed.status_code == 200 and listed.json()["data"] == [body]
    assert await db_session.scalar(select(Job).where(Job.hash == ref)) is None
    other, _ = await _register_and_get_token(client)
    assert (await client.get(f"/api/v1/documents/item/{body['id']}", headers=_auth(other))).status_code == 404
    assert (await client.delete(f"/api/v1/documents/{body['id']}", headers=_auth(token))).status_code == 204


@pytest.mark.parametrize("ref", ["x" * 33, "x" * 36, "{00000000-0000-0000-0000-000000000001}"])
def test_document_reference_rejects_noncanonical_long_ids(ref):
    from pydantic import ValidationError
    from schemas.documents import GenerateDocumentRequest

    with pytest.raises(ValidationError):
        GenerateDocumentRequest(job_hash=ref, doc_type="cv")
