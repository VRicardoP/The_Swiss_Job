"""A delayed corpus link must not reverse the user's newer explicit decision."""

import asyncio
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core.tests import test_integration_api as api
from jobhunt_core.tests.test_integration_api import db, pytestmark  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
from jobhunt_core.tests.test_integration_school_jobs import school_db, _observation  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
from jobhunt_core.tests.test_integration_api_schools import _create, _request, SCOPES


@pytest.mark.parametrize(
    "school_value,canonical_value,school_first,visible,saved",
    [
        ("thumbs_down", "thumbs_up", True, True, True),
        ("thumbs_down", None, True, True, False),
        ("thumbs_up", "thumbs_down", False, True, True),
        (None, "thumbs_down", False, True, False),
    ],
)
def test_latest_intent_wins_when_observation_links_after_both_edits(
    school_db,  # noqa: F811  (la fixture, no una redefinición)
    school_value,
    canonical_value,
    school_first,
    visible,
    saved,
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
    school_path = f"/v1/profiles/{pid}/school-jobs/{job['id']}/feedback"
    canonical_path = f"/v1/profiles/{pid}/vacancies/{vid}/feedback"
    edits = [(school_path, school_value), (canonical_path, canonical_value)]
    if not school_first:
        edits.reverse()
    for n, (path, value) in enumerate(edits):
        response = _request(f, token, path, "PUT", {"feedback": value}, f"edit-{n}")
        assert response.status_code == 200, response.text

    async def link_and_read():
        from jobhunt_core.matching import feed

        async with f() as s:
            await s.execute(
                sa.text(
                    "UPDATE school_job_details SET vacancy_id=:v,quarantine_reason=NULL WHERE id=:j"
                ),
                {"v": vid, "j": uuid.UUID(job["id"])},
            )
            await s.commit()
            rows, _ = await feed(s, pid)
            return {r.vacancy_id for r in rows}

    assert (vid in asyncio.run(link_and_read())) is visible
    marks = _request(f, token, f"/v1/profiles/{pid}/feedback").json()
    assert marks["total"] == int(saved), marks
