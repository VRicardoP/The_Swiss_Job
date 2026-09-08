"""Bounded owner library, including documents whose jobs no longer exist."""

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import select

from models.generated_document import GeneratedDocument
from models.user import User
from tests.test_documents import _auth, _register_and_get_token


@pytest.mark.anyio
async def test_library_pages_ties_orphans_and_ownership(client, db_session):
    token, email = await _register_and_get_token(client)
    _, other_email = await _register_and_get_token(client)
    uid = await db_session.scalar(select(User.id).where(User.email == email))
    other = await db_session.scalar(select(User.id).where(User.email == other_email))
    ids = [uuid.uuid4() for _ in range(45)]
    now = datetime.now(timezone.utc)
    for identity in ids:
        db_session.add(GeneratedDocument(id=identity, user_id=uid, job_hash="gone" * 8,
            job_title="Original position", job_company="Original company", doc_type="cv",
            content="Preserved CV", language="en", created_at=now))
    db_session.add(GeneratedDocument(user_id=other, job_hash="gone" * 8, doc_type="cv",
                                     content="Private other owner", language="en"))
    await db_session.commit()
    collected, cursor, sizes = [], None, []
    while True:
        response = await client.get("/api/v1/documents", headers=_auth(token),
                                    params={"cursor": cursor} if cursor else {})
        assert response.status_code == 200, response.text
        page = response.json()
        sizes.append(len(page["data"]))
        collected.extend(uuid.UUID(d["id"]) for d in page["data"])
        assert all(d["job_title"] == "Original position" for d in page["data"])
        if page["next_cursor"] is None:
            break
        assert page["next_cursor"] != cursor
        cursor = page["next_cursor"]
    assert sizes == [20, 20, 5]
    assert collected == sorted(ids, reverse=True)
    assert len(set(collected)) == 45
    invalid = await client.get("/api/v1/documents", headers=_auth(token), params={"cursor": "bad"})
    assert invalid.status_code == 400
