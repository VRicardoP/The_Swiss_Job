"""Documents outlive jobs, but never their owner; no LLM holds a DB transaction."""

import pytest
from sqlalchemy import delete, select

from config import settings
from database import get_db
from main import app
from models.generated_document import GeneratedDocument
from models.job import Job
from models.user import User
from services.documents.local import LocalDocuments
from tests.conftest import TestSessionLocal
from tests.test_documents import _auth, _gemini_on, _insert_job, _register_and_get_token, _set_cv_text


@pytest.mark.anyio
async def test_document_survives_job_removal_with_original_snapshot(client, db_session):
    _, email = await _register_and_get_token(client)
    user = await db_session.scalar(select(User).where(User.email == email))
    job_hash = await _insert_job(db_session)
    store = LocalDocuments(db_session)
    doc = await store.create(user.id, job_hash, "cv", "original content", "en",
                             job_title="Original title", job_company="Original company")
    job = await db_session.get(Job, job_hash)
    job.title, job.company = "Changed title", "Changed company"
    await db_session.commit()
    current = await store.list(user.id, job_hash)
    assert current.data[0].job_title == "Original title"
    await db_session.execute(delete(Job).where(Job.hash == job_hash))
    await db_session.commit()
    stored = await store.list(user.id, job_hash)
    assert stored.total == 1
    assert stored.data[0] == doc
    user_id = user.id
    await db_session.execute(delete(User).where(User.id == user_id))
    await db_session.commit()
    assert await db_session.get(GeneratedDocument, doc.id) is None


@pytest.mark.anyio
async def test_generation_releases_transaction_before_llm(client, db_session, monkeypatch):
    token, email = await _register_and_get_token(client)
    await _set_cv_text(db_session, email)
    job_hash = await _insert_job(db_session)
    sessions = []

    async def tracked_db():
        async with TestSessionLocal() as db:
            sessions.append(db)
            yield db

    provider = _gemini_on()

    async def generate(*args, **kwargs):
        assert sessions and not sessions[-1].in_transaction()
        return "Finished without a database transaction"

    monkeypatch.setattr("routers.documents.DocumentGeneratorService.generate_cv", generate)
    monkeypatch.setattr("routers.documents._get_gemini", lambda: provider)
    original = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = tracked_db
    try:
        response = await client.post("/api/v1/documents/generate", headers=_auth(token),
                                     json={"job_hash": job_hash, "doc_type": "cv"})
    finally:
        app.dependency_overrides[get_db] = original
    assert response.status_code == 200, response.text


@pytest.mark.anyio
@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/v1/documents/generate", {"job_hash": "x" * 32, "doc_type": "cv"}),
    ("DELETE", "/api/v1/documents/00000000-0000-0000-0000-000000000001", None),
])
async def test_document_freeze_precedes_auth_and_database(client, monkeypatch, method, path, body):
    monkeypatch.setitem(settings.__dict__, "DOCUMENT_WRITES_FROZEN", True)

    async def forbidden_db():
        pytest.fail("freeze must run before authentication/database I/O")
        yield

    original = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = forbidden_db
    try:
        response = await client.request(method, path, json=body)
    finally:
        app.dependency_overrides[get_db] = original
    assert response.status_code == 503


@pytest.mark.anyio
async def test_freeze_during_generation_prevents_late_local_write(client, db_session, monkeypatch):
    token, email = await _register_and_get_token(client)
    await _set_cv_text(db_session, email)
    job_hash = await _insert_job(db_session)
    provider = _gemini_on()

    async def generate(*args, **kwargs):
        monkeypatch.setitem(settings.__dict__, "DOCUMENT_WRITES_FROZEN", True)
        return "Must not be persisted after freeze"

    provider.get_chat_response.side_effect = generate
    monkeypatch.setattr("routers.documents._get_gemini", lambda: provider)
    response = await client.post("/api/v1/documents/generate", headers=_auth(token),
                                 json={"job_hash": job_hash, "doc_type": "cv"})
    assert response.status_code == 503
    assert await db_session.scalar(select(GeneratedDocument.id)) is None
