"""Document portability and owner deletion must respect the cutover freeze."""

import uuid

from sqlalchemy import select

from config import settings
from models.document_delivery import DocumentDelivery
from models.generated_document import GeneratedDocument
from models.jobhunt_profile_map import JobhuntProfileMap
from models.user import User
from tests.test_profile import register_and_get_token


async def test_owner_delete_is_frozen_before_auth(client, monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENT_WRITES_FROZEN", True)
    response = await client.request(
        "DELETE", "/api/v1/profile/delete-all", json={"password": "synthetic"}
    )
    assert response.status_code == 503


async def test_owner_export_includes_orphan_documents_and_prepared_output(
    client, db_session
):
    token, email, _ = await register_and_get_token(client)
    uid = await db_session.scalar(select(User.id).where(User.email == email))
    other_token, other_email, _ = await register_and_get_token(client)
    other_uid = await db_session.scalar(
        select(User.id).where(User.email == other_email)
    )
    for owner, content in [
        (uid, "synthetic owned output"),
        (other_uid, "other owner output"),
    ]:
        db_session.add(
            GeneratedDocument(
                user_id=owner,
                job_hash="a" * 32,
                doc_type="cv",
                language="en",
                content=content,
                job_title="Deleted vacancy",
            )
        )
    operation = uuid.uuid4()
    db_session.add(
        DocumentDelivery(
            operation_id=operation,
            user_id=uid,
            profile_id=uuid.uuid4(),
            request_hash="a" * 64,
            payload_hash="b" * 64,
            payload={"content": "prepared, not acknowledged"},
        )
    )
    await db_session.commit()
    response = await client.get(
        "/api/v1/profile/export", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    exported = response.json()
    assert exported["documents"][0]["content"] == "synthetic owned output"
    assert exported["documents"][0]["job_title"] == "Deleted vacancy"
    assert exported["document_deliveries"][0]["operation_id"] == str(operation)
    assert (
        exported["document_deliveries"][0]["payload"]["content"]
        == "prepared, not acknowledged"
    )
    assert "other owner output" not in response.text
    assert "hashed_password" not in response.text
    other = await client.get(
        "/api/v1/profile/export", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert other.json()["document_deliveries"] == []


async def test_linked_owner_delete_does_not_claim_confirmed_core_erasure(
    client, db_session
):
    token, email, password = await register_and_get_token(client)
    uid = await db_session.scalar(select(User.id).where(User.email == email))
    db_session.add(JobhuntProfileMap(user_id=uid, core_profile_id=uuid.uuid4()))
    await db_session.commit()
    response = await client.request(
        "DELETE",
        "/api/v1/profile/delete-all",
        headers={"Authorization": f"Bearer {token}"},
        json={"password": password},
    )
    assert response.status_code == 200
    assert response.json()["core_erasure"] == "pending_confirmation"
    assert "permanently" not in response.json()["message"]
