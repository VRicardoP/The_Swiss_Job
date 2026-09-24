"""A UUID's spelling must not split idempotency or evade receipt erasure."""
import asyncio

from jobhunt_core.tests import test_integration_api as tia
from jobhunt_core.tests.test_integration_api_documents import _path, _seed
from jobhunt_core.tests.test_integration_api_saved_searches import db, _rows, pytestmark  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)


def test_documents_uuid_spelling_has_one_receipt_and_one_document(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, _, pid = _seed(f, made)
    body = {"doc_type": "cv", "content": "same original"}
    a = tia._api(f, _path(str(pid).upper()), token=token, method="POST",
                 headers={"Idempotency-Key": "spelling"}, json_body=body)
    b = tia._api(f, _path(pid), token=token, method="POST",
                 headers={"Idempotency-Key": "spelling"}, json_body=body)
    assert a.status_code == b.status_code == 201, (a.text, b.text)
    assert a.json()["id"] == b.json()["id"]
    did = a.json()["id"]
    for path in (_path(str(pid).upper(), did.upper()), _path(pid, did)):
        r = tia._api(f, path, token=token, method="DELETE", headers={"Idempotency-Key": "delete-spelling"})
        assert r.status_code == 204, r.text
    assert _rows(f, "SELECT route FROM idempotency_records WHERE key='spelling'") == [("POST " + _path(pid),)]


def test_documents_erase_removes_receipts_from_noncanonical_path(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, tenant, pid = _seed(f, made)
    response = tia._api(f, _path(str(pid).upper()), token=token, method="POST",
                        headers={"Idempotency-Key": "erase-spelling"},
                        json_body={"doc_type": "cv", "content": "erase"})
    assert response.status_code == 201, response.text

    async def erase():
        from jobhunt_core.shadow.projector import erase_shadow_profile
        async with f() as s:
            await erase_shadow_profile(s, "user-1", tenant)
            await s.commit()
    asyncio.run(erase())
    assert _rows(f, "SELECT route FROM idempotency_records WHERE key='erase-spelling'") == []
