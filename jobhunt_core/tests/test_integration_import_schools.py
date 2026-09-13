"""Frozen school import: material equality, ownership, rollback on any drift."""

import asyncio
import copy
import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from jobhunt_core.import_schools import import_batch, snapshot, SchoolMigrationError
from jobhunt_core.tests.test_integration_school_jobs import school_db, db, pytestmark
from jobhunt_core.tests.test_integration_api_schools import SCOPES, SETTINGS
from jobhunt_core.tests.test_integration_api_saved_searches import _seed_profile, _rows


def batch(consumer, pid):
    mid, jid = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    return {
        "batch_id": uuid.uuid4(),
        "consumer": consumer,
        "monitors": [
            {
                "id": mid,
                "external_ref": "school-" + mid.hex[:10],
                "settings": SETTINGS,
                "created_at": now,
                "updated_at": now,
            }
        ],
        "jobs": [
            {
                "id": jid,
                "monitor_id": mid,
                "created_at": now,
                "updated_at": now,
                "notified": True,
                "notified_at": now,
                "observation": {
                    "publish_missing": True,
                    "title": "IT Technician",
                    "url": "https://school.example/" + jid.hex,
                    "content_hash": "a" * 64,
                    "dedup_key": jid.hex,
                    "date_detected": now,
                    "deadline": now.date(),
                },
            }
        ],
        "applications": [
            {
                "id": uuid.uuid4(),
                "profile_id": pid,
                "monitor_id": mid,
                "school_job_id": jid,
                "source_ref": "original-application",
                "status": "sent",
                "draft_content": "Original private draft",
                "context": {"notes": "Private note", "sent_at": now.isoformat()},
                "created_at": now,
                "updated_at": now,
            }
        ],
        "preferences": [{"profile_id": pid, "enabled": True}],
    }


def _run(f, operation):
    async def execute():
        async with f() as session:
            async with session.begin():
                return await operation(session)

    return asyncio.run(execute())


def test_import_schools_exact_material_idempotence_and_current_reverse_snapshot(
    school_db,
):
    f, made = school_db
    _, cid, pid = _seed_profile(f, made, SCOPES)
    name = cid
    cid = _rows(f, "SELECT id FROM consumers WHERE name=:n", n=name)[0][0]
    source = batch(name, pid)
    receipt = _run(f, lambda s: import_batch(s, source))
    assert receipt["verdict"] == "verified"
    assert all(len(ids) == 1 for ids in receipt["inserted"].values())
    replay = _run(f, lambda s: import_batch(s, source))
    assert not any(replay["inserted"].values())
    assert replay["material_sha256"] == receipt["material_sha256"]
    current = _run(f, lambda s: snapshot(s, name))
    job = current["school_job_details"][0]
    assert job["id"] == source["jobs"][0]["id"] and job["vacancy_id"] is not None
    assert (
        job["metadata"]["notified"] is True
        and job["notified_at"] == source["jobs"][0]["notified_at"]
    )
    assert (
        current["school_applications"][0]["draft_content"] == "Original private draft"
    )
    aid = source["applications"][0]["id"]
    _run(
        f,
        lambda s: s.execute(
            sa.text(
                "UPDATE school_applications SET draft_content='Edited after cutover' WHERE id=:id"
            ),
            {"id": aid},
        ),
    )
    current = _run(f, lambda s: snapshot(s, name))
    assert current["school_applications"][0]["draft_content"] == "Edited after cutover"
    with pytest.raises(SchoolMigrationError, match="drift"):
        _run(f, lambda s: import_batch(s, source))
    assert _rows(
        f, "SELECT draft_content FROM school_applications WHERE id=:id", id=aid
    ) == [("Edited after cutover",)]


@pytest.mark.parametrize("active", [True, False])
def test_history_import_replay_is_stable_without_publishing(school_db, active):
    f, made = school_db
    _, name, pid = _seed_profile(f, made, SCOPES)
    data = batch(name, pid)
    data["jobs"][0]["observation"]["publish_missing"] = False
    data["jobs"][0]["source_active"] = active
    before = _rows(f, "SELECT count(*) FROM vacancies")
    first = _run(f, lambda s: import_batch(s, data))
    second = _run(f, lambda s: import_batch(s, data))
    assert first["material_sha256"] == second["material_sha256"]
    assert not any(second["inserted"].values())
    assert _rows(f, "SELECT count(*) FROM vacancies") == before
    current = _run(f, lambda s: snapshot(s, name))
    assert current["school_job_details"][0]["quarantine_reason"] == (
        "awaiting_corpus" if active else "source_inactive"
    )


def test_import_school_drift_rolls_back_new_monitors_and_corpus_together(school_db):
    f, made = school_db
    _, cid, pid = _seed_profile(f, made, SCOPES)
    name = cid
    cid = _rows(f, "SELECT id FROM consumers WHERE name=:n", n=name)[0][0]
    first = batch(name, pid)
    _run(f, lambda s: import_batch(s, first))
    changed = copy.deepcopy(first)
    extra = batch(name, pid)
    changed["monitors"] = extra["monitors"] + changed["monitors"]
    changed["jobs"] = extra["jobs"] + changed["jobs"]
    changed["applications"][0]["draft_content"] = "Wrong historical content"
    before = _rows(f, "SELECT count(*) FROM vacancies")
    with pytest.raises(SchoolMigrationError, match="drift"):
        _run(f, lambda s: import_batch(s, changed))
    assert _rows(f, "SELECT count(*) FROM vacancies") == before
    assert _rows(
        f, "SELECT count(*) FROM school_monitors WHERE consumer_id=:c", c=cid
    ) == [(1,)]


def test_import_school_rejects_foreign_profile_before_any_insert(school_db):
    f, made = school_db
    _, cid, _ = _seed_profile(f, made, SCOPES)
    _, _, other = _seed_profile(f, made, SCOPES)
    name = cid
    cid = _rows(f, "SELECT id FROM consumers WHERE name=:n", n=name)[0][0]
    with pytest.raises(SchoolMigrationError, match="ownership"):
        _run(f, lambda s: import_batch(s, batch(name, other)))
    assert _rows(
        f, "SELECT count(*) FROM school_monitors WHERE consumer_id=:c", c=cid
    ) == [(0,)]
