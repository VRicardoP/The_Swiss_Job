"""Prepared output survives ACK loss; retries neither regenerate nor change owners."""

from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import json
import uuid

import httpx
import pytest
from sqlalchemy import delete, func, select, update

from config import settings
from models.document_delivery import DocumentDelivery
from models.generated_document import GeneratedDocument
from models.jobhunt_profile_map import JobhuntProfileMap
from models.user import User
from services.documents.core_client import CoreDocuments
from services.routing import set_routing
from services.documents.delivery import DocumentDeliveryError, deliver, enqueue, operation_status
from tests.conftest import TestSessionLocal
from tests.test_documents_contract import seed_user


@pytest.fixture
async def prepared(db_session, monkeypatch):
    uid = await seed_user(db_session)
    pid, operation = uuid.uuid4(), uuid.uuid4()
    db_session.add(JobhuntProfileMap(user_id=uid, core_profile_id=pid))
    await db_session.commit()
    await set_routing(db_session, "documents", "core_primary", profile_id=uid)
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "test.only")
    args = dict(operation_id=operation, user_id=uid, profile_id=pid, job_hash="a" * 32,
                doc_type="cv", language="fr", content="Résumé exact", job_title="Teacher",
                job_company="School")
    await enqueue(db_session, **args)
    await db_session.commit()
    return args


def sender_factory(handler):
    return lambda pid, uid: CoreDocuments(profile_id=pid, user_id=uid, client_factory=lambda: httpx.AsyncClient(
        base_url="http://core/v1/", transport=httpx.MockTransport(handler)))


def receipt(request, profile_id, document_id):
    body = json.loads(request.content)
    return {**body, "id": str(document_id), "profile_id": str(profile_id),
            "version": 1, "async_state": "ready", "created_at": "2026-09-09T10:00:00Z",
            "output_hash": hashlib.sha256(body["content"].encode()).hexdigest()}


@pytest.mark.anyio
async def test_ack_loss_reuses_exact_persisted_output_without_open_transaction(prepared, db_session):
    calls, sessions, doc_id = [], [], uuid.uuid4()

    @asynccontextmanager
    async def tracked_factory():
        async with TestSessionLocal() as db:
            sessions.append(db)
            yield db

    def handler(request):
        assert all(not db.in_transaction() for db in sessions)
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("private CV must not leak", request=request)
        return httpx.Response(201, json=receipt(request, prepared["profile_id"], doc_id))

    args = (tracked_factory, prepared["operation_id"], prepared["user_id"])
    first = await deliver(*args, sender_factory=sender_factory(handler))
    assert first["status"] == "pending" and first["error"] == "delivery_failed"
    second = await deliver(*args, sender_factory=sender_factory(handler))
    assert second["status"] == "delivered" and second["document_id"] == doc_id
    assert calls[0].content == calls[1].content
    assert [req.headers["idempotency-key"] for req in calls] == [str(prepared["operation_id"])] * 2
    assert await deliver(*args, sender_factory=sender_factory(handler)) == second
    assert len(calls) == 2
    db_session.expire_all()
    row = await db_session.get(DocumentDelivery, prepared["operation_id"])
    assert row.payload is None and row.document_id == doc_id and row.last_error is None
    assert await db_session.scalar(select(GeneratedDocument.id)) is None


@pytest.mark.anyio
async def test_retry_window_and_freeze_send_nothing(prepared, db_session, monkeypatch):
    await db_session.execute(update(DocumentDelivery).values(first_attempt_at=func.now() - timedelta(hours=23)))
    await db_session.commit()
    no_http = sender_factory(lambda req: pytest.fail("expired operation must not be resent"))
    result = await deliver(TestSessionLocal, prepared["operation_id"], prepared["user_id"], sender_factory=no_http)
    assert result["error"] == "receipt_window_expired"
    monkeypatch.setattr(settings, "DOCUMENT_WRITES_FROZEN", True)
    result = await deliver(TestSessionLocal, prepared["operation_id"], prepared["user_id"], sender_factory=no_http)
    assert result["error"] == "writes_frozen"


@pytest.mark.anyio
async def test_operation_binding_and_request_are_immutable(prepared, db_session):
    await enqueue(db_session, **{**prepared, "content": "Concurrent generation discarded"})
    await db_session.commit()
    db_session.expire_all()
    assert (await db_session.get(DocumentDelivery, prepared["operation_id"])).payload["content"] == "Résumé exact"
    with pytest.raises(DocumentDeliveryError, match="operation_conflict"):
        await enqueue(db_session, **{**prepared, "language": "de"})
    await db_session.rollback()
    assert await operation_status(db_session, prepared["operation_id"], uuid.uuid4()) is None
    await db_session.execute(update(JobhuntProfileMap).values(core_profile_id=uuid.uuid4()))
    await db_session.commit()
    result = await deliver(TestSessionLocal, prepared["operation_id"], prepared["user_id"],
                           sender_factory=sender_factory(lambda req: pytest.fail("changed binding must not send")))
    assert result["error"] == "profile_binding_changed"


@pytest.mark.anyio
async def test_owner_erased_during_http_is_not_resurrected(prepared):
    async def handler(request):
        async with TestSessionLocal() as db:
            await db.execute(delete(User).where(User.id == prepared["user_id"]))
            await db.commit()
        return httpx.Response(201, json=receipt(request, prepared["profile_id"], uuid.uuid4()))

    result = await deliver(TestSessionLocal, prepared["operation_id"], prepared["user_id"],
                           sender_factory=sender_factory(handler))
    assert result["status"] == "pending" and result["error"] == "owner_unavailable"
    async with TestSessionLocal() as db:
        assert await db.get(DocumentDelivery, prepared["operation_id"]) is None


@pytest.mark.anyio
@pytest.mark.parametrize("content", ["", "bad\x00value", "bad\ud800value", "x" * 1_000_001], ids=["empty", "nul", "surrogate", "oversize"])
async def test_invalid_output_is_not_queued(prepared, db_session, content):
    with pytest.raises(DocumentDeliveryError, match="invalid_content"):
        await enqueue(db_session, **{**prepared, "operation_id": uuid.uuid4(), "content": content})
