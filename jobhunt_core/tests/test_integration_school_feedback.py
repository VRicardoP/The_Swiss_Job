"""F cutover: a quarantined school observation still owns durable user marks."""

import asyncio
import uuid
import sqlalchemy as sa
from jobhunt_core.tests import test_integration_api as api
from jobhunt_core.tests.test_integration_school_jobs import school_db, _observation  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
from jobhunt_core.tests.test_integration_api_schools import _create, _request, SCOPES
from jobhunt_core.tests.test_integration_api_saved_searches import (
    _seed_profile,
    _rows,
    pytestmark,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
)
from jobhunt_core.tests.test_integration_api import db  # noqa: F401  (la fixture de la que depende school_db; pytest la resuelve en el espacio de nombres de este módulo — sin ella los tests dan error de fixture)


def test_quarantined_school_feedback_preserves_draft_and_has_no_fake_vacancy(school_db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = school_db
    token, _, pid = _seed_profile(f, made, [*SCOPES, "matches:read"])
    monitor = _create(f, token).json()
    observation = _observation(publish_missing=False)
    job = _request(
        f, token, f"/v1/schools/{monitor['id']}/jobs", "POST", observation, "observe"
    ).json()["item"]
    assert job["vacancy_id"] is None
    before = _rows(f, "SELECT count(*) FROM vacancies")
    base = f"/v1/profiles/{pid}/school-jobs/{job['id']}"
    put = _request(
        f, token, base + "/feedback", "PUT", {"feedback": "thumbs_up"}, "like"
    )
    assert put.status_code == 200, put.text
    assert put.json()["school_job_id"] == job["id"]
    saved = _request(f, token, f"/v1/profiles/{pid}/feedback?limit=1").json()
    assert saved["total"] == 1
    assert saved["items"][0]["vacancy_id"] is None
    assert saved["items"][0]["school_job_id"] == job["id"]
    assert saved["items"][0]["job_ref"] == observation["dedup_key"]
    assert saved["items"][0]["content"]["title"] == observation["title"]
    assert _request(f, token, f"/v1/profiles/{pid}/feedback?offset=1").json() == {
        "items": [],
        "total": 1,
    }
    states_path = f"/v1/profiles/{pid}/school-applications"
    state = _request(f, token, states_path).json()["items"][0]
    path = states_path + "/" + state["id"]
    current = _request(f, token, path)
    draft = _request(
        f,
        token,
        path,
        "PATCH",
        {"status": "sent", "draft_content": "private draft"},
        "draft",
        current.headers["etag"],
    )
    assert draft.status_code == 200, draft.text
    for key in ("opened", "opened", "another-open"):
        response = _request(
            f, token, base + "/implicit", "POST", {"action": "opened"}, key
        )
        assert response.status_code == 200, response.text
    clear = _request(f, token, base + "/feedback", "PUT", {"feedback": None}, "clear")
    assert clear.status_code == 200, clear.text
    state = _request(f, token, path).json()
    assert state["feedback"] is None
    assert len(state["feedback_implicit"]) == 2
    assert state["status"] == "sent" and state["draft_content"] == "private draft"
    assert _rows(f, "SELECT count(*) FROM vacancies") == before
    assert (
        _rows(
            f, "SELECT vacancy_id FROM profile_vacancy_state WHERE profile_id=:p", p=pid
        )
        == []
    )


def test_school_feedback_ownership_and_scope_before_mutation(school_db):  # noqa: F811  (la fixture, no una redefinición)
    f, made = school_db
    token, _, pid = _seed_profile(f, made, SCOPES)
    other, _, other_pid = _seed_profile(f, made, SCOPES)
    monitor = _create(f, token).json()
    job = _request(
        f,
        token,
        f"/v1/schools/{monitor['id']}/jobs",
        "POST",
        _observation(publish_missing=False),
        "observe",
    ).json()["item"]
    for actor, profile in ((other, pid), (other, other_pid)):
        response = _request(
            f,
            actor,
            f"/v1/profiles/{profile}/school-jobs/{job['id']}/feedback",
            "PUT",
            {"feedback": "thumbs_up"},
            "cross-tenant",
        )
        assert response.status_code == 404, response.text
    reader, _, reader_pid = _seed_profile(f, made, ["schools:read"])
    response = _request(
        f,
        reader,
        f"/v1/profiles/{reader_pid}/school-jobs/{job['id']}/feedback",
        "PUT",
        {"feedback": "thumbs_up"},
        "read-only",
    )
    assert response.status_code == 403, response.text
    assert (
        _rows(
            f,
            "SELECT id FROM school_applications WHERE profile_id IN (:p,:o,:r)",
            p=pid,
            o=other_pid,
            r=reader_pid,
        )
        == []
    )


def test_rejected_quarantine_stays_hidden_when_linked_and_clear_restores_feed(
    school_db,  # noqa: F811  (la fixture, no una redefinición)
):
    f, made = school_db
    pid, vacs, _ = api._seed_matches(f, made)
    _, _, token = api._issue(
        f, made, "tenant-match", [*SCOPES, "matches:read", "applications:write"]
    )
    vid = next(iter(vacs.values()))
    monitor = _create(f, token).json()
    job = _request(
        f,
        token,
        f"/v1/schools/{monitor['id']}/jobs",
        "POST",
        _observation(publish_missing=False),
        "observe",
    ).json()["item"]
    base = f"/v1/profiles/{pid}/school-jobs/{job['id']}"
    assert (
        _request(
            f, token, base + "/feedback", "PUT", {"feedback": "thumbs_down"}, "reject"
        ).status_code
        == 200
    )

    async def link():
        async with f() as s:
            await s.execute(
                sa.text(
                    "UPDATE school_job_details SET vacancy_id=:v,quarantine_reason=NULL WHERE id=:j"
                ),
                {"v": vid, "j": uuid.UUID(job["id"])},
            )
            await s.commit()

    asyncio.run(link())

    async def feed_ids():
        from jobhunt_core.matching import feed

        async with f() as s:
            rows, _ = await feed(s, pid)
            return {r.vacancy_id for r in rows}

    assert vid not in asyncio.run(feed_ids())
    canonical = f"/v1/profiles/{pid}/vacancies/{vid}/feedback"
    assert (
        _request(f, token, canonical, "PUT", {"feedback": None}, "clear").status_code
        == 200
    )
    assert vid in asyncio.run(feed_ids())
    assert (
        _request(
            f, token, base + "/feedback", "PUT", {"feedback": "thumbs_up"}, "positive"
        ).status_code
        == 200
    )
    saved = _request(f, token, f"/v1/profiles/{pid}/feedback").json()
    assert saved["total"] == 1  # never duplicate the canonical and school mark
    assert saved["items"][0]["school_job_id"] == job["id"]
    assert (
        _request(
            f, token, canonical, "PUT", {"feedback": "dismissed"}, "canonical-reject"
        ).status_code
        == 200
    )
    assert _request(f, token, f"/v1/profiles/{pid}/feedback").json()["total"] == 0
    assert vid not in asyncio.run(feed_ids())
