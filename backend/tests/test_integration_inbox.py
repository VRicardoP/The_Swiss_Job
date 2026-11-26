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


async def test_unknown_fields_are_ignored_and_never_stored(
    client, db_session, monkeypatch
):
    """D6: a field added by a newer core does not turn every event into a 422
    retried until dead-letter. Only metadata and the hash of the KNOWN fields
    are kept, so an unknown field changes neither what is stored nor the
    replay identity."""
    monkeypatch.setattr(settings, "CORE_INBOX_TOKEN", "synthetic-inbox-token")
    _, _, pid = await owner(client, db_session)
    body = event(pid)
    plain = copy.deepcopy(body)
    body["new_envelope_field"] = {"x": 1}
    body["event"]["new_event_field"] = "must never be stored or echoed"
    first = await client.post(URL, json=body, headers=HEADERS)
    assert first.status_code == 202, first.text
    assert first.json()["inserted"] is True
    assert "must never" not in first.text
    # Same event without the unknown fields: an idempotent replay, not a conflict.
    replay = await client.post(URL, json=plain, headers=HEADERS)
    assert replay.status_code == 202 and replay.json()["inserted"] is False
    # An unknown key inside a document payload is accepted too, and not kept.
    other = event(pid)
    other["event"]["payload"]["content"] = "must never be stored or echoed"
    assert (await client.post(URL, json=other, headers=HEADERS)).status_code == 202
    rows = (await db_session.scalars(select(IntegrationInbox))).all()
    assert len(rows) == 2
    for row in rows:
        stored = {
            c.name: getattr(row, c.name) for c in IntegrationInbox.__table__.columns
        }
        assert "must never" not in repr(stored)


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
    "change", ["consumer", "owner", "version", "deleted", "nul", "extra_but_broken"]
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
    elif change == "extra_but_broken":
        # D6: unknown fields are ignored, never a pass for a broken known one.
        body["new_envelope_field"] = 1
        body["event"]["new_event_field"] = "must never be stored or echoed"
        body["event"]["version"] = 2
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
