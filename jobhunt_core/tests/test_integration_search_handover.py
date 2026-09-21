"""Exact search images, public IDs, owed alerts, and pre-activation rollback."""

import asyncio
from datetime import datetime, timedelta, timezone
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core import saved_searches, search_execution
from jobhunt_core.import_swissjob_searches import SearchMigrationError, prepare_plan, apply_plan
from jobhunt_core.tests.test_integration_search_execution import (
    db, pytestmark, corpus, new_search, run, state, _vacancy_state,
)


async def fixture_plan(session, made, pending_ids=()):
    old_id, pid, tenant, _ = await new_search(session, made, configured=False)
    uid, sid = uuid.uuid4(), uuid.uuid4()
    stamp = datetime.now(timezone.utc)
    row = {"id": sid, "user_id": uid, "name": "Renamed local search",
           "filters": {"q": "Python"}, "min_score": 42, "notify_frequency": "weekly",
           "notify_push": False, "is_active": True, "last_run_at": stamp-timedelta(hours=2),
           "total_matches": 31, "created_at": stamp-timedelta(days=30)}
    plan = await prepare_plan(session, consumer=tenant, bindings={str(uid): str(pid)}, rows=[row],
        targets={str(sid): str(old_id)}, pending={str(sid): list(pending_ids)}, recorded_at=stamp.isoformat())
    await session.commit()
    return plan, sid, pid, tenant


def test_handover_preserves_public_identity_and_every_value_then_reverts_exactly(db):
    factory, made = db
    corpus(factory, made, "Python one", "Python two", "Gardener")

    async def go():
        async with factory() as s:
            plan, sid, _, _ = await fixture_plan(s, made)
            assert (await apply_plan(s, plan))["changed"] == 1
            await s.commit()
            assert (await apply_plan(s, plan))["replayed"] is True
            row = (await s.execute(sa.text("SELECT to_jsonb(s) FROM saved_searches s WHERE id=:id"), {"id": sid})).scalar_one()
            for key in ("id", "name", "filters", "min_score", "notify_frequency", "notify_push", "is_active", "total_matches"):
                assert row[key] == plan["after"][0][key]
            assert (await state(s, sid)).observed == 3
            assert await s.scalar(sa.text("SELECT enabled FROM saved_search_execution WHERE saved_search_id=:id"), {"id": sid}) is False
            assert (await apply_plan(s, plan, reverse=True))["changed"] == 1
            await s.commit()
            assert (await apply_plan(s, plan, reverse=True))["replayed"] is True
            assert await s.scalar(sa.text("SELECT count(*) FROM saved_search_execution")) == 0
            assert await s.scalar(sa.text("SELECT count(*) FROM saved_search_observations")) == 0
            # Both reversions are read back against every original field by apply_plan.
    asyncio.run(go())


def test_old_pending_offer_is_not_lost_and_new_activity_blocks_stale_revert(db):
    factory, made = db
    _, items = corpus(factory, made, "Python owed", "Python already seen")
    pending = _vacancy_state(factory, items[0].external_id).vac

    async def go():
        async with factory() as s:
            await s.execute(sa.text("UPDATE vacancies SET created_at=clock_timestamp()-interval '90 days' WHERE id=:id"), {"id": pending})
            await s.commit()
            plan, sid, _, tenant = await fixture_plan(s, made, [str(pending)])
            await apply_plan(s, plan)
            await s.execute(sa.text("UPDATE saved_search_execution SET enabled=true WHERE saved_search_id=:id"), {"id": sid})
            await s.commit()
            assert (await run(s, sid, tenant))["matches"] == 1
            assert (await run(s, sid, tenant))["matches"] == 0
            before = await state(s, sid)
            assert (before.total_matches, before.events) == (32, 1)
            with pytest.raises(SearchMigrationError, match="changed"):
                await apply_plan(s, plan, reverse=True)
            assert await state(s, sid) == before
    asyncio.run(go())


def test_changed_target_aborts_without_partial_import(db):
    factory, made = db

    async def go():
        async with factory() as s:
            plan, sid, _, _ = await fixture_plan(s, made)
            old = uuid.UUID(plan["before"][0]["id"])
            await s.execute(sa.text("UPDATE saved_searches SET name='concurrent edit' WHERE id=:id"), {"id": old})
            await s.commit()
            with pytest.raises(SearchMigrationError, match="changed"):
                await apply_plan(s, plan)
            assert await s.scalar(sa.text("SELECT name FROM saved_searches WHERE id=:id"), {"id": old}) == "concurrent edit"
            assert await s.scalar(sa.text("SELECT id FROM saved_searches WHERE id=:id"), {"id": sid}) is None
            assert await s.scalar(sa.text("SELECT count(*) FROM saved_search_execution")) == 0
    asyncio.run(go())


def test_nonmatching_pending_is_rejected_instead_of_silently_consumed(db):
    factory, made = db
    _, items = corpus(factory, made, "Gardener")
    vid = _vacancy_state(factory, items[0].external_id).vac

    async def go():
        async with factory() as s:
            with pytest.raises(SearchMigrationError, match="pending source alerts"):
                await fixture_plan(s, made, [str(vid)])
            await s.rollback()
            assert await s.scalar(sa.text("SELECT count(*) FROM saved_search_execution")) == 0
    asyncio.run(go())


def test_explicit_identity_mapping_preserves_homonymous_searches(db):
    factory, made = db

    async def go():
        async with factory() as s:
            old1, pid, tenant, _ = await new_search(s, made, configured=False)
            old2 = await saved_searches.create(s, profile_id=pid, destination=tenant, values={"name": "Search"})
            await s.commit()
            uid, a, b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            stamp = datetime.now(timezone.utc)
            rows = [{"id": sid, "user_id": uid, "name": "same name", "filters": {},
                     "min_score": score, "notify_frequency": "daily", "notify_push": True,
                     "is_active": True, "last_run_at": None, "total_matches": score,
                     "created_at": stamp} for sid, score in ((a, 20), (b, 80))]
            plan = await prepare_plan(s, consumer=tenant, bindings={str(uid): str(pid)}, rows=rows,
                targets={str(a): str(old1), str(b): str(old2)}, pending={str(a): [], str(b): []}, recorded_at=stamp.isoformat())
            await s.commit()
            assert (await apply_plan(s, plan))["changed"] == 2
            await s.commit()
            assert dict((await s.execute(sa.text("SELECT id,min_score FROM saved_searches WHERE profile_id=:id"), {"id": pid})).all()) == {a: 20, b: 80}
    asyncio.run(go())
