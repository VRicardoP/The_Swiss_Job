"""E.3: CV+letter must commit together, including receipt and outbox."""
import asyncio
import uuid

import pytest

from jobhunt_core.tests import test_integration_api as tia
from jobhunt_core.tests.test_integration_api_documents import _seed, _path
from jobhunt_core.tests.test_integration_api_saved_searches import db, _rows, pytestmark


def body():
    return {"items": [
        {"doc_type": "cv", "content": '{"cv":"Résumé"}', "source_ref": "application-1"},
        {"doc_type": "cover_letter", "content": '{"letter":"Bonjour"}', "source_ref": "application-1"},
    ]}


def post(f, token, pid, payload=None, key="batch-1"):
    return tia._api(f, _path(pid) + "/batch", token=token, method="POST",
                    json_body=body() if payload is None else payload,
                    headers={} if key is None else {"Idempotency-Key": key})


def test_batch_roundtrip_canonical_replay_and_new_generation(db):
    f, made = db
    token, _, pid = _seed(f, made)
    first = post(f, token, str(pid).upper())
    assert first.status_code == 201, first.text
    assert [d["doc_type"] for d in first.json()["items"]] == ["cv", "cover_letter"]
    assert post(f, token, pid).content == first.content
    ids = [d["id"] for d in first.json()["items"]]
    receipt = _rows(f, "SELECT route,response FROM idempotency_records WHERE key='batch-1'")[0]
    assert receipt.route == "POST " + _path(pid) + "/batch"
    assert receipt.response["body"] == {"ids": ids}  # no CV copy in receipt
    assert len(_rows(f, "SELECT id FROM generated_documents WHERE profile_id=:p", p=pid)) == 2
    assert len(_rows(f, "SELECT event_id FROM integration_outbox WHERE subject_profile_id=:p", p=pid)) == 2
    changed = body()
    changed["items"][1]["content"] = "different letter"
    assert post(f, token, pid, changed).status_code == 409
    new = post(f, token, pid, key="batch-2")
    assert new.status_code == 201
    assert set(ids).isdisjoint(d["id"] for d in new.json()["items"])


def test_batch_second_outbox_failure_rolls_back_everything(db, monkeypatch):
    from jobhunt_core import outbox
    f, made = db
    token, _, pid = _seed(f, made)
    emit, calls = outbox.emit, []

    async def fail_second(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise RuntimeError("second event failed")
        return await emit(*args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(outbox, "emit", fail_second)
        with pytest.raises(RuntimeError, match="second event failed"):
            post(f, token, pid)
    assert _rows(f, "SELECT id FROM generated_documents WHERE profile_id=:p", p=pid) == []
    assert _rows(f, "SELECT event_id FROM integration_outbox WHERE subject_profile_id=:p", p=pid) == []
    assert _rows(f, "SELECT key FROM idempotency_records WHERE key='batch-1'") == []
    assert post(f, token, pid).status_code == 201  # failed attempt did not poison the key


def test_batch_missing_second_offer_has_no_partial_commit(db):
    f, made = db
    token, _, pid = _seed(f, made)
    payload = body()
    payload["items"][1]["offer_revision_id"] = str(uuid.uuid4())
    assert post(f, token, pid, payload).status_code == 404
    assert _rows(f, "SELECT id FROM generated_documents WHERE profile_id=:p", p=pid) == []
    assert _rows(f, "SELECT event_id FROM integration_outbox WHERE subject_profile_id=:p", p=pid) == []
    assert _rows(f, "SELECT key FROM idempotency_records WHERE key='batch-1'") == []


@pytest.mark.parametrize("payload", [
    {"items": []}, {"items": body()["items"] * 2},
    {"items": [body()["items"][0]] * 2},
    {"items": [{"doc_type": "cv", "content": "ok"}, {"doc_type": "cover_letter", "content": "bad\x00"}]},
    {"items": [{"doc_type": "cv", "content": "ok"}, {"doc_type": "cover_letter", "content": "é" * 500001}]},
    {"items": [{"doc_type": "cv", "content": "ok"}, {"doc_type": "cover_letter", "content": "ok", "context": {"bad": "\ud800"}}]},
    {"items": [{"doc_type": "cv", "content": "ok"}, {"doc_type": "cover_letter", "content": "ok", "context": {"huge": 1e100000}}]},
])
def test_batch_invalid_member_fails_before_any_write(db, payload):
    f, made = db
    token, _, pid = _seed(f, made)
    # Raw JSON permits testing the non-finite boundary, unlike httpx json=.
    import json
    r = tia._api(f, _path(pid) + "/batch", token=token, method="POST",
                 headers={"Idempotency-Key": "bad-batch", "Content-Type": "application/json"},
                 content=json.dumps(payload))
    assert r.status_code == 400, r.text
    assert _rows(f, "SELECT id FROM generated_documents WHERE profile_id=:p", p=pid) == []
    assert _rows(f, "SELECT key FROM idempotency_records WHERE key='bad-batch'") == []


def test_batch_ownership_scope_and_required_key(db):
    f, made = db
    token, tenant, pid = _seed(f, made)
    other, _, _ = _seed(f, made)
    assert post(f, other, pid).status_code == 404
    assert post(f, None, pid).status_code == 401
    _, _, read_only = tia._issue(f, made, tenant, ["documents:read"])
    assert post(f, read_only, pid).status_code == 403
    assert post(f, token, pid, key=None).status_code == 400
    single = post(f, token, pid, {"items": [body()["items"][0]]})
    assert single.status_code == 201 and len(single.json()["items"]) == 1


def test_batch_partial_deletion_never_resurrects_and_erase_purges_receipt(db):
    from jobhunt_core.shadow.projector import erase_shadow_profile
    f, made = db
    token, tenant, pid = _seed(f, made)
    result = post(f, token, pid)
    assert result.status_code == 201, result.text
    did = result.json()["items"][0]["id"]
    assert tia._api(f, _path(pid, did), token=token, method="DELETE").status_code == 204
    assert post(f, token, pid).status_code == 404
    assert len(_rows(f, "SELECT id FROM generated_documents WHERE profile_id=:p", p=pid)) == 1

    async def erase():
        async with f() as s:
            await erase_shadow_profile(s, "user-1", tenant)
            await s.commit()
    asyncio.run(erase())
    assert _rows(f, "SELECT key FROM idempotency_records WHERE key='batch-1'") == []
    assert _rows(f, "SELECT id FROM generated_documents WHERE profile_id=:p", p=pid) == []
