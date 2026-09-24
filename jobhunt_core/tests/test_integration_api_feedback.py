"""F: durable feedback must work for native vacancies without legacy rows."""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core.tests import test_integration_api as api
from jobhunt_core.tests.test_integration_api import db  # noqa: F401

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"), reason="requires isolated PostgreSQL"
)


def seed(db, scopes=("applications:write", "matches:read")):  # noqa: F811  (la fixture, no una redefinición)
    factory, created = db
    pid, vacs, _ = api._seed_matches(factory, created)
    _, _, token = api._issue(factory, created, "tenant-match", list(scopes))
    return factory, pid, next(iter(vacs.values())), token


def rows(factory, statement, **params):
    async def run():
        async with factory() as session:
            return (await session.execute(sa.text(statement), params)).mappings().all()

    return asyncio.run(run())


def test_feedback_roundtrip_dismiss_clear_and_idempotent_event(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, pid, vid, token = seed(db)
    path = f"/v1/profiles/{pid}/vacancies/{vid}/feedback"
    before = rows(
        factory,
        "SELECT current_eval_id FROM profile_vacancy_state WHERE profile_id=:p AND vacancy_id=:v",
        p=pid,
        v=vid,
    )[0]
    kwargs = dict(
        token=token,
        method="PUT",
        json_body={"feedback": "thumbs_down"},
        headers={"Idempotency-Key": "reject-one"},
    )
    one = api._api(factory, path, **kwargs)
    two = api._api(factory, path, **kwargs)
    assert one.status_code == two.status_code == 200
    assert one.json() == two.json()
    state = rows(
        factory,
        "SELECT * FROM profile_vacancy_state WHERE profile_id=:p AND vacancy_id=:v",
        p=pid,
        v=vid,
    )[0]
    assert state["feedback"] == "thumbs_down" and state["dismissed_at"] is not None
    assert state["current_eval_id"] == before["current_eval_id"]
    assert (
        len(
            rows(
                factory,
                "SELECT id FROM profile_vacancy_events WHERE profile_id=:p AND kind='feedback'",
                p=pid,
            )
        )
        == 1
    )
    assert (
        api._api(
            factory, path, token=token, method="PUT", json_body={"feedback": None}
        ).status_code
        == 200
    )
    state = rows(
        factory,
        "SELECT feedback,dismissed_at FROM profile_vacancy_state WHERE profile_id=:p AND vacancy_id=:v",
        p=pid,
        v=vid,
    )[0]
    assert state["feedback"] is None and state["dismissed_at"] is None


def test_feedback_scopes_and_ownership_fail_before_writing(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, pid, vid, token = seed(db, scopes=("matches:read",))
    path = f"/v1/profiles/{pid}/vacancies/{vid}/feedback"
    args = dict(method="PUT", json_body={"feedback": "thumbs_up"})
    assert api._api(factory, path, token=token, **args).status_code == 403
    _, _, other = api._issue(
        factory, db[1], "other-feedback-tenant", ["applications:write"]
    )
    assert api._api(factory, path, token=other, **args).status_code == 404
    assert (
        rows(
            factory, "SELECT id FROM profile_vacancy_events WHERE profile_id=:p", p=pid
        )
        == []
    )


def test_implicit_append_is_idempotent_and_does_not_overwrite_explicit(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, pid, vid, token = seed(db)
    base = f"/v1/profiles/{pid}/vacancies/{vid}"
    assert (
        api._api(
            factory,
            base + "/feedback",
            token=token,
            method="PUT",
            json_body={"feedback": "thumbs_up"},
        ).status_code
        == 200
    )
    for key in ("open", "open", "view"):
        response = api._api(
            factory,
            base + "/implicit",
            token=token,
            method="POST",
            json_body={"action": "opened"},
            headers={"Idempotency-Key": key},
        )
        assert response.status_code == 200
    assert (
        len(
            rows(
                factory,
                "SELECT id FROM profile_vacancy_events WHERE profile_id=:p AND kind='implicit'",
                p=pid,
            )
        )
        == 2
    )
    assert (
        rows(
            factory,
            "SELECT feedback FROM profile_vacancy_state WHERE profile_id=:p AND vacancy_id=:v",
            p=pid,
            v=vid,
        )[0]["feedback"]
        == "thumbs_up"
    )


def test_feedback_absent_vacancy_and_invalid_values_are_not_success(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, pid, vid, token = seed(db)
    path = f"/v1/profiles/{pid}/vacancies/{uuid.uuid4()}/feedback"
    assert (
        api._api(
            factory,
            path,
            token=token,
            method="PUT",
            json_body={"feedback": "dismissed"},
        ).status_code
        == 404
    )
    path = f"/v1/profiles/{pid}/vacancies/{vid}/feedback"
    for body in (
        {"feedback": "arbitrary"},
        {"feedback": "thumbs_up", "profile_id": str(uuid.uuid4())},
    ):
        assert (
            api._api(
                factory, path, token=token, method="PUT", json_body=body
            ).status_code
            == 400
        )


def test_feedback_conflicting_replay_does_not_change_state(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, pid, vid, token = seed(db)
    path = f"/v1/profiles/{pid}/vacancies/{vid}/feedback"
    args = dict(
        token=token, method="PUT", headers={"Idempotency-Key": "same-operation"}
    )
    assert (
        api._api(factory, path, json_body={"feedback": "thumbs_up"}, **args).status_code
        == 200
    )
    assert (
        api._api(
            factory, path, json_body={"feedback": "thumbs_down"}, **args
        ).status_code
        == 409
    )
    assert (
        rows(
            factory,
            "SELECT feedback FROM profile_vacancy_state WHERE profile_id=:p AND vacancy_id=:v",
            p=pid,
            v=vid,
        )[0]["feedback"]
        == "thumbs_up"
    )
    assert (
        len(
            rows(
                factory,
                "SELECT id FROM profile_vacancy_events WHERE profile_id=:p",
                p=pid,
            )
        )
        == 1
    )


def test_archived_feedback_can_be_cleared_without_reopening_corpus(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, pid, vid, token = seed(db)
    path = f"/v1/profiles/{pid}/vacancies/{vid}/feedback"
    assert (
        api._api(
            factory,
            path,
            token=token,
            method="PUT",
            json_body={"feedback": "dismissed"},
        ).status_code
        == 200
    )

    async def archive():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE vacancies SET archived_at=now() WHERE id=:v"),
                {"v": vid},
            )
            await session.commit()

    asyncio.run(archive())
    assert (
        api._api(
            factory, path, token=token, method="PUT", json_body={"feedback": None}
        ).status_code
        == 200
    )
    assert (
        rows(factory, "SELECT archived_at FROM vacancies WHERE id=:v", v=vid)[0][
            "archived_at"
        ]
        is not None
    )
    # No attachment remains: an unrelated new write cannot revive this job.
    assert (
        api._api(
            factory,
            path,
            token=token,
            method="PUT",
            json_body={"feedback": "thumbs_up"},
        ).status_code
        == 404
    )


def test_feedback_events_are_erased_with_profile_and_replay_stays_closed(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, pid, vid, token = seed(db, scopes=("applications:write", "profiles:write"))
    base = f"/v1/profiles/{pid}/vacancies/{vid}"
    args = dict(
        token=token,
        method="PUT",
        json_body={"feedback": "thumbs_down"},
        headers={"Idempotency-Key": "before-erasure"},
    )
    assert api._api(factory, base + "/feedback", **args).status_code == 200
    assert (
        api._api(
            factory,
            base + "/implicit",
            token=token,
            method="POST",
            json_body={"action": "opened"},
        ).status_code
        == 200
    )
    assert (
        api._api(
            factory, f"/v1/profiles/{pid}", token=token, method="DELETE"
        ).status_code
        == 200
    )
    assert (
        rows(
            factory, "SELECT id FROM profile_vacancy_events WHERE profile_id=:p", p=pid
        )
        == []
    )
    assert (
        rows(
            factory,
            "SELECT profile_id FROM profile_vacancy_state WHERE profile_id=:p",
            p=pid,
        )
        == []
    )
    assert api._api(factory, base + "/feedback", **args).status_code == 404


@pytest.mark.parametrize("duration", [-1, True, "500", 2**63])
def test_invalid_implicit_duration_never_reaches_events(db, duration):  # noqa: F811  (la fixture, no una redefinición)
    factory, pid, vid, token = seed(db)
    response = api._api(
        factory,
        f"/v1/profiles/{pid}/vacancies/{vid}/implicit",
        token=token,
        method="POST",
        json_body={"action": "view_time", "duration_ms": duration},
    )
    assert response.status_code == 400
    assert (
        rows(
            factory, "SELECT id FROM profile_vacancy_events WHERE profile_id=:p", p=pid
        )
        == []
    )


def test_positive_feedback_listing_survives_archive_and_missing_evaluation(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, pid, vid, token = seed(db)
    path = f"/v1/profiles/{pid}/vacancies/{vid}/feedback"
    assert (
        api._api(
            factory,
            path,
            token=token,
            method="PUT",
            json_body={"feedback": "thumbs_up"},
        ).status_code
        == 200
    )

    async def archive():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE vacancies SET archived_at=now() WHERE id=:v"),
                {"v": vid},
            )
            await session.execute(
                sa.text(
                    "UPDATE profile_vacancy_state SET current_eval_id=NULL WHERE profile_id=:p AND vacancy_id=:v"
                ),
                {"p": pid, "v": vid},
            )
            await session.commit()

    asyncio.run(archive())
    response = api._api(factory, f"/v1/profiles/{pid}/feedback?limit=1", token=token)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1 and len(body["items"]) == 1
    assert body["items"][0]["vacancy_id"] == str(vid)
    assert body["items"][0]["feedback"] == "thumbs_up"
    assert body["items"][0]["score_final"] == 0
    assert api._api(
        factory, f"/v1/profiles/{pid}/feedback?offset=1", token=token
    ).json() == {"total": 1, "items": []}
    _, _, other = api._issue(factory, db[1], "other-feedback-reader", ["matches:read"])
    assert (
        api._api(factory, f"/v1/profiles/{pid}/feedback", token=other).status_code
        == 404
    )
