"""Feedback cutover preserves clears, implicit multiplicity, and exact preimages."""

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from jobhunt_core.import_swissjob_feedback import (
    FeedbackMigrationError, apply_plan, prepare_plan,
)
from jobhunt_core.tests.test_integration_api import db, pytestmark
from jobhunt_core.tests.test_integration_api_feedback import seed, rows


def source_row(factory, pid, vid, **overrides):
    url = rows(factory, "SELECT url FROM source_listing_incarnations WHERE vacancy_id=:v LIMIT 1", v=vid)[0]["url"]
    return {
        "id": str(uuid.uuid4()), "user_id": str(pid), "job_hash": uuid.uuid4().hex,
        "url": url, "feedback": "thumbs_up", "feedback_implicit": [], **overrides,
    }


async def plan_for(session, pid, source):
    return await prepare_plan(
        session, consumer="tenant-match", bindings={str(pid): str(pid)}, rows=source,
        recorded_at=datetime.now(timezone.utc).isoformat(),
    )


def test_feedback_plan_import_replay_reverse_preserves_other_state(db):
    factory, pid, vid, _ = seed(db)
    event = {"action": "opened", "timestamp": "2026-09-19T10:00:00Z"}
    source = [source_row(factory, pid, vid, feedback_implicit=[event, event])]

    async def run():
        async with factory() as s:
            await s.execute(sa.text("UPDATE profile_vacancy_state SET notes='keep',saved_at=now() WHERE profile_id=:p AND vacancy_id=:v"), {"p": pid, "v": vid})
            await s.commit()
            before = (await s.execute(sa.text("SELECT to_jsonb(p) FROM profile_vacancy_state p WHERE profile_id=:p AND vacancy_id=:v"), {"p": pid, "v": vid})).scalar_one()
            plan = await plan_for(s, pid, source)
            await s.commit()  # In the command, the private plan is fsynced HERE, before apply.
            assert (await apply_plan(s, plan))["changed"] == 3
            await s.commit()
            assert (await apply_plan(s, plan))["replayed"] is True
            assert await s.scalar(sa.text("SELECT count(*) FROM profile_vacancy_events WHERE profile_id=:p AND kind='implicit'"), {"p": pid}) == 2
            assert await s.scalar(sa.text("SELECT feedback FROM profile_vacancy_state WHERE profile_id=:p AND vacancy_id=:v"), {"p": pid, "v": vid}) == "thumbs_up"
            assert (await apply_plan(s, plan, reverse=True))["changed"] == 3
            await s.commit()
            assert (await apply_plan(s, plan, reverse=True))["replayed"] is True
            after = (await s.execute(sa.text("SELECT to_jsonb(p) FROM profile_vacancy_state p WHERE profile_id=:p AND vacancy_id=:v"), {"p": pid, "v": vid})).scalar_one()
            assert before == after
            assert await s.scalar(sa.text("SELECT count(*) FROM profile_vacancy_events WHERE profile_id=:p"), {"p": pid}) == 0
    asyncio.run(run())


def test_feedback_clear_is_migrated_and_new_edit_prevents_old_rollback(db):
    factory, pid, vid, _ = seed(db)
    source = [source_row(factory, pid, vid, feedback=None)]

    async def run():
        async with factory() as s:
            await s.execute(sa.text("UPDATE profile_vacancy_state SET feedback='thumbs_down',dismissed_at=now() WHERE profile_id=:p AND vacancy_id=:v"), {"p": pid, "v": vid})
            await s.commit()
            plan = await plan_for(s, pid, source)
            assert len(plan["changes"]) == 1
            await apply_plan(s, plan)
            await s.commit()
            assert await s.scalar(sa.text("SELECT feedback IS NULL AND dismissed_at IS NULL FROM profile_vacancy_state WHERE profile_id=:p AND vacancy_id=:v"), {"p": pid, "v": vid})
            await s.execute(sa.text("UPDATE profile_vacancy_state SET feedback='applied' WHERE profile_id=:p AND vacancy_id=:v"), {"p": pid, "v": vid})
            await s.commit()
            with pytest.raises(FeedbackMigrationError, match="changed since"):
                await apply_plan(s, plan, reverse=True)
            assert await s.scalar(sa.text("SELECT feedback FROM profile_vacancy_state WHERE profile_id=:p AND vacancy_id=:v"), {"p": pid, "v": vid}) == "applied"
    asyncio.run(run())


def test_feedback_conflicting_aliases_and_unresolved_marks_abort(db):
    factory, pid, vid, _ = seed(db)
    first = source_row(factory, pid, vid)
    second = source_row(factory, pid, vid, feedback="thumbs_down")

    async def run():
        async with factory() as s:
            with pytest.raises(FeedbackMigrationError, match="conflicting"):
                await plan_for(s, pid, [first, second])
            with pytest.raises(FeedbackMigrationError, match="no core identity"):
                await plan_for(s, pid, [{**first, "url": "https://missing.example/no-listing"}])
    asyncio.run(run())


def test_feedback_plan_seal_and_ownership_are_not_optional(db):
    factory, pid, vid, _ = seed(db)
    source = [source_row(factory, pid, vid)]

    async def run():
        async with factory() as s:
            plan = await plan_for(s, pid, source)
            plan["changes"][0]["after"]["feedback"] = "dismissed"
            with pytest.raises(FeedbackMigrationError, match="seal"):
                await apply_plan(s, plan)
            with pytest.raises(FeedbackMigrationError, match="ownership"):
                await prepare_plan(s, consumer="other", bindings={str(pid): str(pid)}, rows=source,
                                   recorded_at=datetime.now(timezone.utc).isoformat())
    asyncio.run(run())
