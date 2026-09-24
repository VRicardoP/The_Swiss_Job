"""Frozen school marks round-trip without changing corpus, drafts or status."""

import asyncio
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from jobhunt_core.import_swissjob_feedback import apply_plan, prepare_plan
from jobhunt_core.tests.test_integration_api import db  # noqa: F401  (la fixture de la que depende school_db; pytest la resuelve en el espacio de nombres de este módulo — sin ella los tests dan error de fixture)
from jobhunt_core.tests.test_integration_api_saved_searches import (
    _seed_profile,
    _rows,
    pytestmark,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
)
from jobhunt_core.tests.test_integration_api_schools import _create, _request, SCOPES
from jobhunt_core.tests.test_integration_school_jobs import school_db, _observation  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)


def test_quarantined_school_feedback_handover_is_lossless(school_db):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = school_db
    token, consumer, pid = _seed_profile(factory, made, SCOPES)
    monitor = _create(factory, token).json()
    observation = _observation(publish_missing=False)
    job = _request(
        factory,
        token,
        f"/v1/schools/{monitor['id']}/jobs",
        "POST",
        observation,
        "observe",
    ).json()["item"]
    mark_path = f"/v1/profiles/{pid}/school-jobs/{job['id']}/feedback"
    assert (
        _request(
            factory, token, mark_path, "PUT", {"feedback": "thumbs_up"}, "old-mark"
        ).status_code
        == 200
    )
    state_path = f"/v1/profiles/{pid}/school-applications"
    state = _request(factory, token, state_path).json()["items"][0]
    item_path = state_path + "/" + state["id"]
    etag = _request(factory, token, item_path).headers["etag"]
    assert (
        _request(
            factory,
            token,
            item_path,
            "PATCH",
            {"status": "sent", "draft_content": "private draft"},
            "draft",
            etag,
        ).status_code
        == 200
    )
    before = _rows(
        factory,
        "SELECT to_jsonb(a) FROM school_applications a WHERE profile_id=:p",
        p=pid,
    )
    corpus = _rows(factory, "SELECT count(*) FROM vacancies")
    event = {"action": "opened", "timestamp": "2026-09-19T10:00:00Z"}
    source = [
        {
            "id": str(uuid.uuid4()),
            "user_id": str(pid),
            "job_hash": observation["dedup_key"],
            "url": observation["url"],
            "feedback": None,
            "feedback_implicit": [event, event],
        }
    ]

    async def run():
        async with factory() as s:
            plan = await prepare_plan(
                s,
                consumer=consumer,
                bindings={str(pid): str(pid)},
                rows=source,
                recorded_at=datetime.now(timezone.utc).isoformat(),
            )
            assert len(plan["changes"]) == 1
            await s.commit()
            await apply_plan(s, plan)
            await s.commit()
            row = (
                await s.execute(
                    sa.text(
                        "SELECT feedback,feedback_implicit,status,draft_content FROM school_applications WHERE profile_id=:p"
                    ),
                    {"p": pid},
                )
            ).one()
            assert tuple(row) == (None, [event, event], "sent", "private draft")
            assert (await apply_plan(s, plan))["replayed"] is True
            await apply_plan(s, plan, reverse=True)
            await s.commit()

    asyncio.run(run())
    assert (
        _rows(
            factory,
            "SELECT to_jsonb(a) FROM school_applications a WHERE profile_id=:p",
            p=pid,
        )
        == before
    )
    assert _rows(factory, "SELECT count(*) FROM vacancies") == corpus
