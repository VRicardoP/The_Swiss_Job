"""Export must include remote pages or fail, never disguise a partial export."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from config import settings
from models.generated_document import GeneratedDocument
from models.jobhunt_profile_map import JobhuntProfileMap
from models.user import User
from schemas.documents import DocumentPageResponse, GeneratedDocumentResponse
from services.documents.core_client import CoreDocuments
from services.documents.export import export_documents
from services.documents.port import CoreUnavailableError
from services.routing import set_routing
from tests.test_profile import register_and_get_token


async def _owner(client, db_session):
    token, email, _ = await register_and_get_token(client)
    uid = await db_session.scalar(select(User.id).where(User.email == email))
    db_session.add(JobhuntProfileMap(user_id=uid, core_profile_id=uuid.uuid4()))
    await db_session.commit()
    await set_routing(db_session, "documents", "core_primary", profile_id=uid)
    return uid, token


def _doc():
    return GeneratedDocumentResponse(
        id=uuid.uuid4(),
        job_hash="b" * 32,
        doc_type="cv",
        content="synthetic core output",
        language="en",
        created_at=datetime.now(timezone.utc),
    )


async def test_export_core_pages_without_transaction_and_keeps_local_output(
    client, db_session, monkeypatch
):
    uid, _ = await _owner(client, db_session)
    db_session.add(
        GeneratedDocument(
            user_id=uid,
            job_hash="a" * 32,
            doc_type="cv",
            content="retained local copy",
            language="en",
        )
    )
    await db_session.commit()
    calls = []
    documents = [_doc() for _ in range(21)]

    async def page(self, user_id, cursor=None):
        assert user_id == uid
        assert not db_session.in_transaction(), "HTTP must not retain local transaction"
        calls.append(cursor)
        return DocumentPageResponse(
            data=documents[:20] if cursor is None else documents[20:],
            next_cursor="page2" if cursor is None else None,
        )

    monkeypatch.setattr(CoreDocuments, "page", page)
    result = await export_documents(db_session, uid)
    assert calls == [None, "page2"]
    assert result["documents_authority"] == "core"
    assert len(result["documents"]) == 21
    assert result["retained_local_documents"][0]["content"] == "retained local copy"


@pytest.mark.parametrize(
    "failure", ["network", "timeout", "repeated_id", "repeated_cursor"]
)
async def test_export_core_never_returns_partial_success(
    client, db_session, monkeypatch, failure
):
    uid, token = await _owner(client, db_session)
    doc, count = _doc(), 0

    async def page(self, user_id, cursor=None):
        nonlocal count
        count += 1
        if count == 1:
            return DocumentPageResponse(data=[doc], next_cursor="page2")
        if failure == "network":
            raise CoreUnavailableError("synthetic network failure")
        if failure == "timeout":
            raise TimeoutError()
        return DocumentPageResponse(
            data=[doc if failure == "repeated_id" else _doc()], next_cursor="page2"
        )

    monkeypatch.setattr(CoreDocuments, "page", page)
    response = await client.get(
        "/api/v1/profile/export", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 503
    assert "synthetic core output" not in response.text
    assert count == 2


async def test_export_size_limit_fails_without_truncation(
    client, db_session, monkeypatch
):
    uid, token = await _owner(client, db_session)
    monkeypatch.setattr("services.documents.export.MAX_EXPORT_BYTES", 10)

    async def page(self, user_id, cursor=None):
        return DocumentPageResponse(data=[_doc()])

    monkeypatch.setattr(CoreDocuments, "page", page)
    response = await client.get(
        "/api/v1/profile/export", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 503


async def test_freeze_keeps_export_alive_and_account_intact(
    client, db_session, monkeypatch
):
    token, email, password = await register_and_get_token(client)
    monkeypatch.setattr(settings, "DOCUMENT_WRITES_FROZEN", True)
    headers = {"Authorization": f"Bearer {token}"}
    assert (
        await client.get("/api/v1/profile/export", headers=headers)
    ).status_code == 200
    assert (
        await client.request(
            "DELETE",
            "/api/v1/profile/delete-all",
            headers=headers,
            json={"password": password},
        )
    ).status_code == 503
    assert (
        await db_session.scalar(select(User.id).where(User.email == email)) is not None
    )
