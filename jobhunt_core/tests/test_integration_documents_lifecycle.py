"""E.1 boundaries: real foreign keys, immutable content and simultaneous requests."""
import asyncio
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core.tests.test_integration_api_documents import _path, _post, _seed
from jobhunt_core.tests.test_integration_api_saved_searches import db, _rows, pytestmark  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)


def test_documents_offer_purge_keeps_content_and_profile_delete_fails_closed(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, _, pid = _seed(f, made)
    vid, oid = uuid.uuid4(), uuid.uuid4()

    async def execute(sql, **params):
        async with f() as s:
            await s.execute(sa.text(sql), params)
            await s.commit()

    asyncio.run(execute("INSERT INTO vacancies(id) VALUES (:v)", v=vid))
    try:
        asyncio.run(execute(
            "INSERT INTO offer_revisions(id,vacancy_id,content_hash,text_hash,content) "
            "VALUES (:o,:v,:h,:h,'{}')", o=oid, v=vid, h="a" * 64))
        response = _post(f, token, pid, {
            "doc_type": "cover_letter", "content": "Permanent original",
            "offer_revision_id": str(oid), "context": {"job_title": "Original title"},
        })
        assert response.status_code == 201, response.text
        did = uuid.UUID(response.json()["id"])
        with pytest.raises(sa.exc.DBAPIError, match="immutable"):
            asyncio.run(execute("UPDATE generated_documents SET content='changed' WHERE id=:d", d=did))
        with pytest.raises(sa.exc.IntegrityError):
            asyncio.run(execute("DELETE FROM profiles WHERE id=:p", p=pid))
        asyncio.run(execute("DELETE FROM offer_revisions WHERE id=:o", o=oid))
        row = _rows(f, "SELECT content,output_hash,offer_revision_id,context "
                      "FROM generated_documents WHERE id=:d", d=did)[0]
        assert row.content == response.json()["content"]
        assert row.output_hash == response.json()["output_hash"]
        assert row.offer_revision_id is None and row.context == {"job_title": "Original title"}
    finally:
        asyncio.run(execute("DELETE FROM offer_revisions WHERE id=:o", o=oid))
        asyncio.run(execute("DELETE FROM vacancies WHERE id=:v", v=vid))


def test_documents_missing_offer_does_not_write(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, _, pid = _seed(f, made)
    response = _post(f, token, pid, {
        "doc_type": "cv", "content": "cv", "offer_revision_id": str(uuid.uuid4()),
    })
    assert response.status_code == 404
    assert _rows(f, "SELECT id FROM generated_documents WHERE profile_id=:p", p=pid) == []
    assert _rows(f, "SELECT event_id FROM integration_outbox WHERE subject_profile_id=:p", p=pid) == []


@pytest.mark.parametrize("fields", [
    {"content": "é" * 500001},
    {"context": {str(i): 1e-200 for i in range(400)}},
])
def test_documents_storage_byte_limits_return_400(db, fields):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, _, pid = _seed(f, made)
    response = _post(f, token, pid, {"doc_type": "cv", "content": "cv", **fields})
    assert response.status_code == 400, response.text
    assert _rows(f, "SELECT id FROM generated_documents WHERE profile_id=:p", p=pid) == []


def test_documents_concurrent_same_key_creates_once(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, _, pid = _seed(f, made)

    async def concurrent():
        from httpx import ASGITransport, AsyncClient
        from jobhunt_core.api import deps
        from jobhunt_core.api.main import app

        async def session():
            async with f() as s:
                yield s

        app.dependency_overrides[deps.get_session] = session
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                return await asyncio.gather(*[
                    client.post(_path(pid), json={"doc_type": "cv", "content": "same"},
                                headers={"Authorization": f"Bearer {token}",
                                         "Idempotency-Key": "concurrent-document"})
                    for _ in range(2)
                ])
        finally:
            app.dependency_overrides.pop(deps.get_session, None)

    a, b = asyncio.run(concurrent())
    assert a.status_code == b.status_code == 201, (a.text, b.text)
    assert a.json() == b.json()
    assert len(_rows(f, "SELECT id FROM generated_documents WHERE profile_id=:p", p=pid)) == 1
    assert len(_rows(f, "SELECT event_id FROM integration_outbox WHERE subject_profile_id=:p", p=pid)) == 1
