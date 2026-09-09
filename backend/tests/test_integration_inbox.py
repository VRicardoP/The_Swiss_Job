"""Durable core receipts: authenticated, idempotent, owner-scoped and erasable."""

import copy
import uuid

import pytest
from sqlalchemy import func, select

from config import settings
from models.integration_inbox import IntegrationInbox
from models.jobhunt_profile_map import JobhuntProfileMap
from models.user import User
from tests.test_profile import register_and_get_token

URL = "/api/v1/integration/events"
HEADERS = {"Authorization": "Bearer synthetic-inbox-token"}


async def owner(client, db):
    token, email, password = await register_and_get_token(client)
    uid = await db.scalar(select(User.id).where(User.email == email))
    pid = uuid.uuid4()
    db.add(JobhuntProfileMap(user_id=uid, core_profile_id=pid))
    await db.commit()
    return token, password, pid


def event(pid, deleted=False):
    did, eid = str(uuid.uuid4()), str(uuid.uuid4())
    version = 2 if deleted else 1
    return {
        "consumer_id": "swissjob-shadow",
        "event": {
            "event_id": eid,
            "type": "document.changed",
            "aggregate": "document",
            "aggregate_id": did,
            "subject_profile_id": str(pid),
            "version": version,
            "payload": {
                "document_id": did,
                "profile_id": str(pid),
                "version": version,
                "deleted": deleted,
            },
        },
    }


@pytest.mark.parametrize("deleted", [False, True])
async def test_document_receipt_replay_and_owner_erase(
    client, db_session, monkeypatch, deleted
):
    monkeypatch.setattr(settings, "CORE_INBOX_TOKEN", "synthetic-inbox-token")
    token, password, pid = await owner(client, db_session)
    body = event(pid, deleted)
    first = await client.post(URL, json=body, headers=HEADERS)
    assert first.status_code == 202 and first.json() == {
        "accepted": True,
        "inserted": True,
    }
    # Retry after losing the first ACK: a separately committed request must deduplicate.
    repeated = await client.post(URL, json=body, headers=HEADERS)
    assert repeated.status_code == 202 and repeated.json()["inserted"] is False
    assert (
        await db_session.scalar(select(func.count()).select_from(IntegrationInbox)) == 1
    )
    row = await db_session.scalar(select(IntegrationInbox))
    assert row.subject_profile_id == pid and len(row.event_hash) == 64
    assert "payload" not in IntegrationInbox.__table__.columns
    erased = await client.request(
        "DELETE",
        "/api/v1/profile/delete-all",
        headers={"Authorization": f"Bearer {token}"},
        json={"password": password},
    )
    assert erased.status_code == 200
    assert (
        await db_session.scalar(select(func.count()).select_from(IntegrationInbox)) == 0
    )
    assert (await client.post(URL, json=body, headers=HEADERS)).status_code == 409
    assert (
        await db_session.scalar(select(func.count()).select_from(IntegrationInbox)) == 0
    )


async def test_conflicting_replay_is_not_acknowledged(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "CORE_INBOX_TOKEN", "synthetic-inbox-token")
    _, _, pid = await owner(client, db_session)
    body = event(pid)
    assert (await client.post(URL, json=body, headers=HEADERS)).status_code == 202
    changed = copy.deepcopy(body)
    changed["event"]["aggregate_id"] = str(uuid.uuid4())
    changed["event"]["payload"]["document_id"] = changed["event"]["aggregate_id"]
    assert (await client.post(URL, json=changed, headers=HEADERS)).status_code == 409
    assert (
        await db_session.scalar(select(func.count()).select_from(IntegrationInbox)) == 1
    )


@pytest.mark.parametrize(
    "change", ["consumer", "owner", "version", "deleted", "extra_content", "nul"]
)
async def test_rejects_invalid_or_foreign_event(
    client, db_session, monkeypatch, change
):
    monkeypatch.setattr(settings, "CORE_INBOX_TOKEN", "synthetic-inbox-token")
    _, _, pid = await owner(client, db_session)
    body = event(pid)
    if change == "consumer":
        body["consumer_id"] = "portfolio"
    elif change == "owner":
        foreign = str(uuid.uuid4())
        body["event"]["subject_profile_id"] = foreign
        body["event"]["payload"]["profile_id"] = foreign
    elif change == "version":
        body["event"]["version"] = 2
    elif change == "deleted":
        body["event"]["payload"]["deleted"] = True
    elif change == "extra_content":
        body["event"]["payload"]["content"] = "must never be stored or echoed"
    else:
        body["event"]["type"] = "bad\x00type"
    response = await client.post(URL, json=body, headers=HEADERS)
    assert response.status_code == (409 if change in {"consumer", "owner"} else 422)
    assert "must never" not in response.text
    assert (
        await db_session.scalar(select(func.count()).select_from(IntegrationInbox)) == 0
    )


async def test_matching_eval_key_is_not_a_document_uuid(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "CORE_INBOX_TOKEN", "synthetic-inbox-token")
    _, _, pid = await owner(client, db_session)
    body = event(pid)
    body["event"].update(
        type="match.evaluated",
        aggregate="match_evaluation",
        aggregate_id="a" * 64,
        payload={
            "eval_key": "a" * 64,
            "profile_id": str(pid),
            "vacancy_id": str(uuid.uuid4()),
        },
    )
    first = await client.post(URL, json=body, headers=HEADERS)
    assert first.status_code == 202
    assert (await client.post(URL, json=body, headers=HEADERS)).json()[
        "inserted"
    ] is False
    assert (
        await db_session.scalar(select(func.count()).select_from(IntegrationInbox)) == 1
    )


async def test_unconfigured_auth_and_body_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "CORE_INBOX_TOKEN", "")
    assert (await client.post(URL, content=b"invalid json")).status_code == 503
    monkeypatch.setattr(settings, "CORE_INBOX_TOKEN", "synthetic-inbox-token")
    assert (await client.post(URL, content=b"invalid json")).status_code == 401
    assert (
        await client.post(URL, content=b"x" * 65537, headers=HEADERS)
    ).status_code == 413
    assert (
        await client.post(URL, content=b"invalid json", headers=HEADERS)
    ).status_code == 422
