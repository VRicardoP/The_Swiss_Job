"""Router workflow with real local transactions and mocked HTTP boundary only."""

import hashlib
import json
import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from config import settings
from models.document_delivery import DocumentDelivery
from models.generated_document import GeneratedDocument
from models.job import Job
from models.jobhunt_profile_map import JobhuntProfileMap
from models.user import User
from services.routing import set_routing
from tests.test_documents import _auth, _gemini_on, _insert_job, _register_and_get_token, _set_cv_text


@pytest.mark.anyio
async def test_core_ack_loss_retries_without_job_cv_or_provider(client, db_session, monkeypatch):
    token, email = await _register_and_get_token(client)
    await _set_cv_text(db_session, email)
    job_hash = await _insert_job(db_session)
    uid = await db_session.scalar(select(User.id).where(User.email == email))
    pid, doc_id, operation = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(JobhuntProfileMap(user_id=uid, core_profile_id=pid))
    await db_session.commit()
    await set_routing(db_session, "documents", "core_primary", profile_id=uid)
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "test.only")
    provider = _gemini_on("Prepared exact output")
    monkeypatch.setattr("routers.documents._get_gemini", lambda: provider)
    requests, remote = [], {}

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            response = {**body, "id": str(doc_id), "profile_id": str(pid), "version": 1,
                        "async_state": "ready", "created_at": "2026-09-09T10:00:00Z",
                        "output_hash": hashlib.sha256(body["content"].encode()).hexdigest()}
            key = request.headers["Idempotency-Key"]
            if key not in remote:
                remote[key] = response
                raise httpx.ReadTimeout("ACK lost", request=request)
            assert remote[key] == response
            return httpx.Response(201, json=response)
        if request.method == "GET":
            return httpx.Response(200, json=remote[str(operation)])
        pytest.fail("unexpected HTTP method")

    monkeypatch.setattr("services.documents.core_client.default_client_factory", lambda: httpx.AsyncClient(
        base_url="http://core/v1/", transport=httpx.MockTransport(handler)))
    body = {"job_hash": job_hash, "doc_type": "cv", "operation_id": str(operation)}
    first = await client.post("/api/v1/documents/generate", headers=_auth(token), json=body)
    assert first.status_code == 202 and first.json()["status"] == "pending"
    pending = await client.get("/api/v1/documents/operations", headers=_auth(token))
    assert pending.status_code == 200 and pending.json()["total"] == 1
    assert pending.json()["data"][0]["operation_id"] == str(operation)
    assert "Prepared exact output" not in pending.text
    assert await db_session.scalar(select(GeneratedDocument.id)) is None
    # A retry MUST NOT revalidate generation inputs or call a provider again.
    await db_session.execute(delete(Job).where(Job.hash == job_hash))
    await db_session.commit()
    monkeypatch.setattr("routers.documents._get_gemini", lambda: pytest.fail("must not regenerate"))
    second = await client.post("/api/v1/documents/generate", headers=_auth(token), json=body)
    assert second.status_code == 202 and second.json()["status"] == "delivered"
    assert second.json()["document_id"] == str(doc_id)
    assert (await client.get("/api/v1/documents/operations", headers=_auth(token))).json() == {"data": [], "total": 0}
    assert provider.get_chat_response.await_count == 1 and len(requests) == 2
    fetched = await client.get(f"/api/v1/documents/item/{doc_id}", headers=_auth(token))
    assert fetched.status_code == 200 and fetched.json()["content"] == "Prepared exact output"
    conflict = await client.post("/api/v1/documents/generate", headers=_auth(token), json={**body, "language": "de"})
    assert conflict.status_code == 409
    db_session.expire_all()
    assert (await db_session.get(DocumentDelivery, operation)).payload is None
    stranger, _ = await _register_and_get_token(client)
    denied = await client.get(f"/api/v1/documents/operations/{operation}", headers=_auth(stranger))
    assert denied.status_code == 404
    assert (await client.get("/api/v1/documents/operations", headers=_auth(stranger))).json() == {"data": [], "total": 0}
    monkeypatch.setattr(settings, "DOCUMENT_WRITES_FROZEN", True)
    assert (await client.get(f"/api/v1/documents/item/{doc_id}", headers=_auth(token))).status_code == 200
    assert (await client.post(f"/api/v1/documents/operations/{operation}/retry", headers=_auth(token))).status_code == 503


@pytest.mark.anyio
async def test_owner_deleted_during_inference_cannot_publish_local(client, db_session, monkeypatch):
    token, email = await _register_and_get_token(client)
    await _set_cv_text(db_session, email)
    job_hash = await _insert_job(db_session)
    uid = await db_session.scalar(select(User.id).where(User.email == email))
    await db_session.commit()
    monkeypatch.setattr("routers.documents._get_gemini", lambda: _gemini_on())

    async def generate(*args, **kwargs):
        await db_session.execute(delete(User).where(User.id == uid))
        await db_session.commit()
        return "must not survive account deletion"

    monkeypatch.setattr("routers.documents.DocumentGeneratorService.generate_cv", generate)
    response = await client.post("/api/v1/documents/generate", headers=_auth(token),
                                 json={"job_hash": job_hash, "doc_type": "cv"})
    assert response.status_code == 403
    assert await db_session.scalar(select(GeneratedDocument.id)) is None
