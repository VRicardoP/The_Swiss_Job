"""E.1: document storage against real PG; no LLM, PDF engine or live writer."""

import asyncio
import hashlib
import uuid

import pytest

from jobhunt_core.tests import test_integration_api as tia
from jobhunt_core.tests.test_integration_api_saved_searches import (
    db,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
    _rows,
    _seed_profile,
    pytestmark,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
)

SCOPES = ["documents:read", "documents:write"]


def _seed(factory, created):
    return _seed_profile(factory, created, SCOPES)


def _path(pid, did=None):
    return f"/v1/profiles/{pid}/documents" + (f"/{did}" if did else "")


def _post(factory, token, pid, body=None, key="auto"):
    headers = (
        {}
        if key is None
        else {"Idempotency-Key": str(uuid.uuid4()) if key == "auto" else key}
    )
    return tia._api(
        factory,
        _path(pid),
        token=token,
        method="POST",
        headers=headers,
        json_body=body
        if body is not None
        else {
            "doc_type": "cv",
            "content": '{"name": "Résumé"}',
            "language": "fr",
            "source_ref": "local-application",
            "context": {"job_title": "Engineer"},
        },
    )


def test_documents_roundtrip_replay_and_content_free_receipt(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, tenant, pid = _seed(f, made)
    first = _post(f, token, pid, key="document-1")
    assert first.status_code == 201, first.text
    doc = first.json()
    assert doc["output_hash"] == hashlib.sha256(doc["content"].encode()).hexdigest()
    assert doc["version"] == 1 and doc["async_state"] == "ready"
    replay = _post(f, token, pid, key="document-1")
    assert (
        replay.content == first.content
        and replay.headers["etag"] == first.headers["etag"]
    )
    receipt = _rows(
        f, "SELECT response FROM idempotency_records WHERE key='document-1'"
    )[0][0]
    assert receipt["body"] == {"id": doc["id"]}  # no second copy of the CV
    conflict = _post(
        f, token, pid, {"doc_type": "cv", "content": "different"}, "document-1"
    )
    assert conflict.status_code == 409
    get = tia._api(f, _path(pid, doc["id"]), token=token)
    assert get.json() == doc
    cached = tia._api(
        f,
        _path(pid, doc["id"]),
        token=token,
        headers={"If-None-Match": get.headers["etag"]},
    )
    assert cached.status_code == 304
    events = _rows(
        f,
        "SELECT o.payload,d.destination FROM integration_outbox o "
        "JOIN integration_outbox_deliveries d USING(event_id) "
        "WHERE o.subject_profile_id=:p AND o.type='document.changed'",
        p=pid,
    )
    assert len(events) == 1 and events[0].destination == tenant
    assert "content" not in events[0].payload


def test_documents_delete_never_replays_erased_content(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, _, pid = _seed(f, made)
    first = _post(f, token, pid, key="late-create")
    assert first.status_code == 201, first.text
    url = _path(pid, first.json()["id"])
    stale = tia._api(
        f, url, token=token, method="DELETE", headers={"If-Match": '"wrong"'}
    )
    assert stale.status_code == 412
    delete = tia._api(
        f,
        url,
        token=token,
        method="DELETE",
        headers={"If-Match": first.headers["etag"], "Idempotency-Key": "delete-1"},
    )
    assert delete.status_code == 204 and delete.content == b""
    assert tia._api(f, url, token=token).status_code == 404
    assert _post(f, token, pid, key="late-create").status_code == 404
    assert (
        tia._api(
            f,
            url,
            token=token,
            method="DELETE",
            headers={"Idempotency-Key": "delete-1"},
        ).status_code
        == 204
    )
    assert _rows(
        f,
        "SELECT version FROM integration_outbox WHERE subject_profile_id=:p "
        "AND type='document.changed' ORDER BY version",
        p=pid,
    ) == [(1,), (2,)]


def test_documents_ownership_scopes_and_immutable_body(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, tenant, pid = _seed(f, made)
    other, _, other_pid = _seed(f, made)
    first = _post(f, token, pid)
    assert first.status_code == 201, first.text
    did = first.json()["id"]
    for path in (_path(pid), _path(pid, did), _path(other_pid, did)):
        assert tia._api(f, path, token=other).status_code == 404
    assert _post(f, other, pid).status_code == 404
    assert tia._api(f, _path(pid, did), token=other, method="DELETE").status_code == 404
    assert tia._api(f, _path(pid)).status_code == 401
    _, _, no_scope = tia._issue(f, made, tenant, ["profiles:read"])
    assert tia._api(f, _path(pid), token=no_scope).status_code == 403
    assert _post(f, no_scope, pid).status_code == 403
    assert (
        tia._api(
            f, _path(pid, did), token=token, method="PUT", json_body={}
        ).status_code
        == 405
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"content": "bad\x00content"},
        {"context": {"nested": "bad\x00value"}},
        {"context": []},
        {"generation_time_ms": -1},
        {"doc_type": "executable"},
        {"language": "too-long"},
        {"content": None},
        {"output_hash": "caller-controlled"},
    ],
)
def test_documents_invalid_body_is_rejected_without_write(db, bad):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, _, pid = _seed(f, made)
    response = _post(f, token, pid, {"doc_type": "cv", "content": "valid", **bad})
    assert response.status_code == 400, response.text
    assert (
        _rows(f, "SELECT id FROM generated_documents WHERE profile_id=:p", p=pid) == []
    )
    assert (
        _rows(
            f,
            "SELECT key FROM idempotency_records WHERE route=:r",
            r="POST " + _path(pid),
        )
        == []
    )


def test_documents_require_key_and_pagination_has_no_ghost_page(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, _, pid = _seed(f, made)
    assert _post(f, token, pid, key=None).status_code == 400
    ids = []
    for _ in range(4):
        r = _post(f, token, pid)
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    page = tia._api(
        f, _path(pid) + "?limit=2&source_ref=local-application", token=token
    ).json()
    assert len(page["items"]) == 2 and page["next_cursor"]
    following = tia._api(
        f, _path(pid) + "?limit=2&cursor=" + page["next_cursor"], token=token
    ).json()
    assert len(following["items"]) == 2 and following["next_cursor"] is None
    assert {d["id"] for d in page["items"] + following["items"]} == set(ids)
    assert tia._api(f, _path(pid) + "?cursor=broken", token=token).status_code == 400


def test_documents_erase_profile_purges_content_events_and_receipts(db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, tenant, pid = _seed(f, made)
    other, _, other_pid = _seed(f, made)
    assert _post(f, token, pid).status_code == 201
    other_doc = _post(f, other, other_pid)
    assert other_doc.status_code == 201

    async def erase():
        from jobhunt_core.shadow.projector import erase_shadow_profile

        async with f() as s:
            assert await erase_shadow_profile(s, "user-1", tenant) == pid
            await s.commit()

    asyncio.run(erase())
    assert (
        _rows(f, "SELECT id FROM generated_documents WHERE profile_id=:p", p=pid) == []
    )
    assert (
        _rows(
            f,
            "SELECT event_id FROM integration_outbox WHERE subject_profile_id=:p",
            p=pid,
        )
        == []
    )
    assert (
        _rows(
            f,
            "SELECT key FROM idempotency_records WHERE route=:r",
            r="POST " + _path(pid),
        )
        == []
    )
    assert (
        tia._api(f, _path(other_pid, other_doc.json()["id"]), token=other).status_code
        == 200
    )


def test_documents_outbox_failure_rolls_back_document_and_reservation(db, monkeypatch):  # noqa: F811  (la fixture, no una redefinición)
    f, made = db
    token, _, pid = _seed(f, made)
    from jobhunt_core import outbox

    async def fail(*args, **kwargs):
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(outbox, "emit", fail)
    with pytest.raises(RuntimeError, match="injected event failure"):
        _post(f, token, pid)
    assert (
        _rows(f, "SELECT id FROM generated_documents WHERE profile_id=:p", p=pid) == []
    )
    assert (
        _rows(
            f,
            "SELECT key FROM idempotency_records WHERE route=:r",
            r="POST " + _path(pid),
        )
        == []
    )
