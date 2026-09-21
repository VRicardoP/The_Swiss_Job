"""Saved-search handover invariants against real PostgreSQL, without delivery."""

import asyncio
from datetime import datetime, timedelta, timezone
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core import outbox, profiles, saved_searches, search_execution as execution
from jobhunt_core.harvest.providers import legacy_shadow
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.harvest.types import RawListing
from jobhunt_core.tests.test_integration_offer import db, pytestmark, _seed, _sink, _vacancy_state


async def new_search(session, made, *, filters=None, configured=True, enabled=True, name="Search"):
    tenant = "search-" + uuid.uuid4().hex[:12]
    cid = await profiles.ensure_consumer(session, tenant)
    made["consumers"].append(cid)
    pid = await profiles.upsert_profile(session, cid, "owner")
    sid = await saved_searches.create(session, profile_id=pid, destination=tenant, values={
        "name": name, "filters": {"q": "Python"} if filters is None else filters,
        "notify_frequency": "daily", "notify_push": True, "min_score": 99,
    })
    since = datetime.now(timezone.utc) - timedelta(days=7)
    if configured:
        await execution.configure_execution(session, sid, contract=execution.CONTRACT,
                                             notify_since=since, enabled=enabled)
    await session.commit()
    return sid, pid, tenant, since


def corpus(factory, made, *titles):
    source = "legacy:search-run-" + uuid.uuid4().hex[:8]
    legacy_shadow.ensure_registered(source)
    scope = _seed(factory, made, source)
    listings = [listing(str(n), title) for n, title in enumerate(titles)]
    _sink(factory, scope, listings)
    return scope, listings


def listing(ref, title="Python developer"):
    return RawListing(ref, f"https://search-run.test/{ref}", {"title": title, "company_name": "Example"})


async def run(session, sid, tenant, *, force=True):
    result = await execution.execute_search(session, sid, destinations={tenant}, force=force)
    await session.commit()
    return result


async def state(session, sid):
    return (await session.execute(sa.text("""
        SELECT ss.total_matches, ss.last_run_at,
          (SELECT count(*) FROM saved_search_observations o WHERE o.saved_search_id=ss.id) AS observed,
          (SELECT count(*) FROM integration_outbox o WHERE o.aggregate_id=CAST(ss.id AS text)
             AND o.type='saved_search.matches') AS events
        FROM saved_searches ss WHERE id=:id
    """), {"id": sid})).one()


def test_observation_counters_and_outbox_are_idempotent(db):
    factory, made = db
    corpus(factory, made, "Python developer", "Gardener")

    async def go():
        async with factory() as s:
            sid, pid, tenant, _ = await new_search(s, made)
            assert await run(s, sid, tenant) == {"status": "ok", "observed": 2, "matches": 1}
            assert await run(s, sid, tenant) == {"status": "ok", "observed": 0, "matches": 0}
            st = await state(s, sid)
            assert (st.total_matches, st.observed, st.events) == (1, 2, 1)
            event = (await s.execute(sa.text("""
                SELECT o.payload, d.destination FROM integration_outbox o
                JOIN integration_outbox_deliveries d ON d.event_id=o.event_id
                WHERE o.subject_profile_id=:pid AND o.type='saved_search.matches'
            """), {"pid": pid})).one()
            assert event.destination == tenant
            assert event.payload == {"search_id": str(sid), "profile_id": str(pid),
                                     "search_name": "Search", "match_count": 1, "notify_push": True}
    asyncio.run(go())


def test_old_offers_and_filter_edits_do_not_manufacture_novelty(db):
    factory, made = db
    _, listings = corpus(factory, made, "Python developer", "Gardener", "Python old")
    old = _vacancy_state(factory, listings[2].external_id).vac

    async def go():
        async with factory() as s:
            await s.execute(sa.text("UPDATE vacancies SET created_at=clock_timestamp()-interval '30 days' WHERE id=:v"), {"v": old})
            sid, _, tenant, _ = await new_search(s, made)
            assert (await run(s, sid, tenant))["matches"] == 1
            row = await saved_searches.fetch_owned(s, sid, made["consumers"][0], for_update=True)
            await saved_searches.update(s, row, {"filters": {"q": "Gardener"}}, tenant)
            await s.commit()
            assert (await run(s, sid, tenant))["matches"] == 0
            assert (await state(s, sid)).observed == 3
    asyncio.run(go())


@pytest.mark.parametrize("condition", ["unconfigured", "disabled", "inactive_search", "inactive_profile", "inactive_consumer"])
def test_no_execution_without_explicit_active_authority(db, condition):
    factory, made = db
    corpus(factory, made, "Python developer")

    async def go():
        async with factory() as s:
            sid, pid, tenant, _ = await new_search(s, made, configured=condition != "unconfigured",
                                                   enabled=condition != "disabled")
            if condition == "inactive_search":
                await s.execute(sa.text("UPDATE saved_searches SET is_active=false WHERE id=:id"), {"id": sid})
            elif condition == "inactive_profile":
                # The projection check requires an authoritative version/hash for inactive.
                await s.execute(sa.text("UPDATE profiles SET projection_active=false,projection_version=1,projection_hash=:hash WHERE id=:id"), {"id": pid, "hash": "a"*64})
            elif condition == "inactive_consumer":
                await s.execute(sa.text("UPDATE consumers SET active=false WHERE id=:id"), {"id": made["consumers"][0]})
            await s.commit()
            result = await run(s, sid, tenant)
            assert result["status"] in {"disabled", "not_found"}
            st = await state(s, sid)
            assert st.last_run_at is None and (st.observed, st.total_matches, st.events) == (0, 0, 0)
    asyncio.run(go())


def test_missing_real_destination_does_not_consume_or_mark(db):
    factory, made = db
    corpus(factory, made, "Python developer")

    async def go():
        async with factory() as s:
            sid, _, _, _ = await new_search(s, made)
            with pytest.raises(ValueError, match="HTTP inbox"):
                await execution.execute_search(s, sid, destinations=set())
            await s.rollback()
            st = await state(s, sid)
            assert st.last_run_at is None and st.observed == st.events == 0
    asyncio.run(go())


def test_outbox_failure_rolls_back_consumption_and_retry_delivers(db, monkeypatch):
    factory, made = db
    corpus(factory, made, "Python developer")
    original = outbox.emit

    async def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure after ledger/counters")

    async def go():
        async with factory() as s:
            sid, _, tenant, _ = await new_search(s, made)
            monkeypatch.setattr(outbox, "emit", fail)
            with pytest.raises(RuntimeError, match="synthetic"):
                await execution.execute_search(s, sid, destinations={tenant})
            await s.rollback()
            st = await state(s, sid)
            assert st.last_run_at is None and (st.total_matches, st.observed, st.events) == (0, 0, 0)
            monkeypatch.setattr(outbox, "emit", original)
            assert (await run(s, sid, tenant))["matches"] == 1
    asyncio.run(go())


def test_late_harvest_commit_is_not_lost_by_timestamp(db):
    factory, made = db
    scope, _ = corpus(factory, made, "Gardener")

    async def go():
        async with factory() as s, factory() as harvester:
            sid, _, tenant, _ = await new_search(s, made)
            # Uncommitted offer whose timestamp predates the search's mark.
            await RawListingSink().handle(harvester, scope, (listing("late"),))
            assert (await run(s, sid, tenant))["matches"] == 0
            await harvester.commit()
            assert (await run(s, sid, tenant))["matches"] == 1
            assert (await run(s, sid, tenant))["matches"] == 0
    asyncio.run(go())


def test_two_executors_serialize_without_duplicate_events(db):
    factory, made = db
    corpus(factory, made, "Python developer")

    async def go():
        async with factory() as first, factory() as second:
            sid, _, tenant, _ = await new_search(first, made)
            result = await execution.execute_search(first, sid, destinations={tenant})
            first_pid = await first.scalar(sa.text("SELECT pg_backend_pid()"))
            second_pid = await second.scalar(sa.text("SELECT pg_backend_pid()"))
            waiting = asyncio.create_task(run(second, sid, tenant))
            # Prove blocking with the actual backend pid, not a sleep alone.
            async with factory() as monitor:
                async with asyncio.timeout(5):
                    while True:
                        assert not waiting.done()
                        blocked = await monitor.scalar(sa.text(
                            "SELECT :blocker = ANY(pg_blocking_pids(:waiter))"
                        ), {"blocker": first_pid, "waiter": second_pid})
                        if blocked:
                            break
                        await asyncio.sleep(0.01)
            await first.commit()
            other = await asyncio.wait_for(waiting, timeout=5)
            assert result["matches"] == 1 and other["matches"] == 0
            assert (await state(first, sid)).events == 1
    asyncio.run(go())


def test_schedule_and_manual_run_and_authority_disable(db):
    factory, made = db
    corpus(factory, made, "Python developer")

    async def go():
        async with factory() as s:
            sid, _, tenant, since = await new_search(s, made)
            assert sid in await execution.due_search_ids(s)
            assert (await run(s, sid, tenant, force=False))["matches"] == 1
            assert sid not in await execution.due_search_ids(s)
            assert (await run(s, sid, tenant, force=False))["status"] == "not_due"
            assert (await run(s, sid, tenant))["status"] == "ok"
            await execution.configure_execution(s, sid, contract=execution.CONTRACT, notify_since=since, enabled=False)
            await s.commit()
            assert (await run(s, sid, tenant))["status"] == "disabled"
            with pytest.raises(ValueError, match="already fixed"):
                await execution.configure_execution(s, sid, contract=execution.CONTRACT,
                                                    notify_since=since-timedelta(days=1), enabled=True)
            await s.rollback()
            assert (await state(s, sid)).observed == 1
    asyncio.run(go())


def test_search_delete_cascades_derived_state(db):
    factory, made = db
    corpus(factory, made, "Python developer")

    async def go():
        async with factory() as s:
            sid, _, tenant, _ = await new_search(s, made)
            await run(s, sid, tenant)
            await s.execute(sa.text("DELETE FROM saved_searches WHERE id=:id"), {"id": sid})
            await s.commit()
            for table in ("saved_search_execution", "saved_search_observations"):
                assert await s.scalar(sa.text(f"SELECT count(*) FROM {table} WHERE saved_search_id=:id"), {"id": sid}) == 0
    asyncio.run(go())
