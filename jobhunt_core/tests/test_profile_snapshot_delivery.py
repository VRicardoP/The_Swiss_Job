"""Real HTTP/PG contracts for replacing profile CDC with durable BFF snapshots."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from jobhunt_core import embeddings, matching, profiles
from jobhunt_core.shadow import projector
from jobhunt_core.tests.test_integration_api import db, _api, _issue, _seed_matches


def _snapshot(version=1, *, title="Current profile", active=True):
    return {
        "version": version,
        "active": active,
        "content": {
            "title": title,
            "cv_text": None,
            "skills": [],
            "languages": [],
            "locations": [],
            "experience_years": None,
            "salary_min": None,
            "salary_max": None,
            "remote_pref": "any",
        },
    }


def _seed(factory, created):
    pid, vacancies, read_token = _seed_matches(factory, created)
    _, _, token = _issue(
        factory, created, "tenant-match", ["profiles:write", "profiles:read"]
    )
    return pid, vacancies, token, read_token


def _put(factory, pid, token, body):
    return _api(
        factory,
        f"/v1/profiles/{pid}/source-snapshot",
        token=token,
        method="PUT",
        json_body=body,
    )


def test_snapshot_out_of_order_retry_and_conflicting_version(db):
    factory, created = db
    pid, _, token, _ = _seed(factory, created)
    assert _put(factory, pid, token, _snapshot(2)).status_code == 200
    assert _put(factory, pid, token, _snapshot(1, title="Stale")).json()["version"] == 2
    assert _put(factory, pid, token, _snapshot(2)).status_code == 200
    assert _put(factory, pid, token, _snapshot(2, title="Conflict")).status_code == 409
    current = _api(factory, f"/v1/profiles/{pid}", token=token).json()
    assert current["current_revision"]["content"]["title"] == "Current profile"


def test_empty_snapshot_withdraws_feed_without_erasing_feedback(db):
    factory, created = db
    pid, vacancies, token, read_token = _seed(factory, created)
    vid = next(iter(vacancies.values()))

    async def save():
        async with factory() as s:
            await matching.set_saved(s, pid, vid, True)
            await s.commit()

    asyncio.run(save())
    response = _put(factory, pid, token, _snapshot(title=None))
    assert response.status_code == 200, response.text

    async def check():
        async with factory() as s:
            current = await profiles.current_revision(s, pid)
            assert (
                current.content["title"] is None and current.content["cv_text"] is None
            )
            assert await s.scalar(
                sa.text(
                    "SELECT saved_at IS NOT NULL FROM profile_vacancy_state WHERE profile_id=:p AND vacancy_id=:v"
                ),
                {"p": pid, "v": vid},
            )
            assert (await matching.feed(s, pid))[0] == []
            for mid in created["models"]:
                assert pid not in [
                    r.profile_id
                    for r in await embeddings.pending_profile_revisions(s, mid)
                ]

    asyncio.run(check())
    assert (
        _api(factory, f"/v1/profiles/{pid}/matches", token=read_token).status_code
        == 200
    )


@pytest.mark.parametrize(
    "change", ["missing", "unknown", "bool_version", "negative_version", "salary"]
)
def test_snapshot_rejects_incomplete_or_invalid_input(db, change):
    factory, created = db
    pid, _, token, _ = _seed(factory, created)
    body = _snapshot()
    if change == "missing":
        body["content"].pop("cv_text")
    elif change == "unknown":
        body["content"]["surprise"] = "ignored?"
    elif change == "bool_version":
        body["version"] = True
    elif change == "negative_version":
        body["version"] = -1
    else:
        body["content"].update(salary_min=100, salary_max=10)
    assert _put(factory, pid, token, body).status_code in (400, 422)


def test_snapshot_auth_ownership_and_old_put_authority(db):
    factory, created = db
    pid, _, token, read_token = _seed(factory, created)
    _, _, foreign = _issue(factory, created, "foreign-snapshot", ["profiles:write"])
    assert _put(factory, pid, read_token, _snapshot()).status_code == 403
    assert _put(factory, pid, foreign, _snapshot()).status_code == 404
    assert _put(factory, pid, token, _snapshot()).status_code == 200
    r = _api(
        factory,
        f"/v1/profiles/{pid}",
        token=token,
        method="PUT",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json_body={"title": "Other writer"},
    )
    assert r.status_code == 409, r.text


def test_cdc_cannot_overwrite_or_erase_profile_after_handover(db):
    factory, created = db
    cid, _, token = _issue(
        factory, created, projector.SHADOW_CONSUMER, ["profiles:write"]
    )
    uid = str(uuid.uuid4())

    async def setup():
        async with factory() as s:
            pid = await profiles.upsert_profile(s, cid, uid)
            await s.commit()
            return pid

    pid = asyncio.run(setup())
    assert _put(factory, pid, token, _snapshot()).status_code == 200

    async def check():
        async with factory() as s:
            await projector._apply_profiles(
                s,
                [
                    SimpleNamespace(
                        pk=str(uuid.uuid4()),
                        op="U",
                        payload={"user_id": uid, "title": "Old CDC"},
                    )
                ],
            )
            assert (await profiles.current_revision(s, pid)).content[
                "title"
            ] == "Current profile"
            assert (
                await projector._apply_users(s, [SimpleNamespace(pk=uid, op="D")])
                == set()
            )
            assert (
                await s.scalar(
                    sa.text("SELECT id FROM profiles WHERE id=:p"), {"p": pid}
                )
                == pid
            )
            await s.commit()

    asyncio.run(check())


def test_inactive_snapshot_blocks_evaluation_and_reactivation_restores_signal(db):
    factory, created = db
    pid, _, token, _ = _seed(factory, created)
    assert _put(factory, pid, token, _snapshot(active=False)).status_code == 200

    async def check():
        async with factory() as s:
            assert (await matching.feed(s, pid))[0] == []
            rows = await embeddings.pending_profile_revisions(s, created["models"][0])
            assert pid not in [r.profile_id for r in rows]
            assert not await s.scalar(
                sa.text("SELECT projection_active FROM profiles WHERE id=:p"),
                {"p": pid},
            )

    asyncio.run(check())
    assert _put(factory, pid, token, _snapshot(2, active=True)).status_code == 200
