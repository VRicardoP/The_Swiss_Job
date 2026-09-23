"""Local fencing of prepared delivery and concurrent repeated user intent."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from models.document_delivery import DocumentDelivery
from services.documents.delivery import deliver, enqueue
from services.routing import set_routing
from tests.conftest import TestSessionLocal
from tests.test_document_delivery import prepared  # noqa: F401 -- shared real-DB fixture


@pytest.mark.anyio
async def test_concurrent_same_operation_has_one_committed_body(prepared):  # noqa: F811  (la fixture, no una redefinición)
    operation = uuid.uuid4()
    start = asyncio.Event()

    async def save(content):
        async with TestSessionLocal() as db:
            await start.wait()
            await enqueue(db, **{**prepared, "operation_id": operation, "content": content})
            await db.commit()

    tasks = [asyncio.create_task(save(content)) for content in ["first contender", "second contender"]]
    start.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
    async with TestSessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(DocumentDelivery).where(
            DocumentDelivery.operation_id == operation)) == 1
        row = await db.get(DocumentDelivery, operation)
        assert row.payload["content"] in {"first contender", "second contender"}


@pytest.mark.anyio
async def test_prepared_delivery_does_not_cross_a_routing_flip(prepared, db_session):  # noqa: F811  (la fixture, no una redefinición)
    await set_routing(db_session, "documents", "local", profile_id=prepared["user_id"])
    result = await deliver(TestSessionLocal, prepared["operation_id"], prepared["user_id"],
                           sender_factory=lambda *args: pytest.fail("must not send after authority changed"))
    assert result["status"] == "pending" and result["error"] == "authority_changed"
    row = await db_session.get(DocumentDelivery, prepared["operation_id"])
    assert row.payload["content"] == prepared["content"]


@pytest.mark.anyio
async def test_health_contract_and_document_freeze_signal(client, monkeypatch):
    from config import settings
    assert (await client.get("/health")).json() == {"status": "healthy"}
    assert (await client.get("/health/documents")).json() == {"writes": "enabled"}
    monkeypatch.setattr(settings, "DOCUMENT_WRITES_FROZEN", True)
    assert (await client.get("/health/documents")).json() == {"writes": "frozen"}
    assert (await client.get("/health")).json() == {"status": "healthy"}
