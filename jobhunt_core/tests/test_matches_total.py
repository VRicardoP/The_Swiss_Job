"""The matches page must carry its own total, and count it in ONE pass.

Measured on the live NAS (PREDECLARACION_PUNTO5_2026-09-22.md §5): serving 20
offers took 9.3-12.9 s because the consumer had to walk the WHOLE feed — 18
requests, 1.800 items transformed — for the sole purpose of knowing the total,
which `MatchesPageDTO` did not expose. One page costs 29 ms; the walk costs ~9 s.

The count must not become the new bottleneck: computing the effective feedback
with a correlated subquery costs 1.1-9.3 s on production data, while the batched
form already present in `feedback.py` returns the SAME number in 0.4-0.9 s.

The total is the size of the served feed, so it obeys exactly the same rules as
the page: current evaluation, vacancy alive, not dismissed or thumbed down, and
the tenant filtered in SQL. A total counting anything else would be a different
promise from the one the page keeps.
"""

import asyncio

import sqlalchemy as sa

from jobhunt_core import matching
from jobhunt_core.tests.test_integration_matching import (  # noqa: F401
    db, pytestmark, _setup, _evaluate, _feed)


def test_total_counts_exactly_what_the_page_serves(db):
    factory, created = db
    pid, mid, polid, vacs = _setup(
        factory, created, ["backend python", "data eng", "qa manual", "contable"])
    _evaluate(factory, pid, mid, polid)

    async def go():
        async with factory() as s:
            rows, _ = await matching.feed(s, pid, limit=1000)
            total = await matching.feed_total(s, pid)
            assert total == len(rows) == 4

            # Dismissing removes the offer from BOTH the page and the total.
            await matching.set_dismissed(s, pid, vacs["qa manual"], True)
            await s.commit()
        async with factory() as s:
            rows, _ = await matching.feed(s, pid, limit=1000)
            assert await matching.feed_total(s, pid) == len(rows) == 3

    asyncio.run(go())


def test_total_is_scoped_to_its_tenant(db):
    """A consumer must never be told how many matches another one has."""
    factory, created = db
    pid, mid, polid, _ = _setup(factory, created, ["backend python", "data eng"])
    _evaluate(factory, pid, mid, polid)

    async def go():
        async with factory() as s:
            owner = await s.scalar(
                sa.text("SELECT consumer_id FROM profiles WHERE id=:p"), {"p": pid})
            assert await matching.feed_total(s, pid, consumer_id=owner) == 2
            import uuid
            assert await matching.feed_total(s, pid, consumer_id=uuid.uuid4()) == 0

    asyncio.run(go())


def test_total_is_counted_in_one_pass_not_once_per_row(db):
    """Guards the 1.1-9.3 s -> 0.4-0.9 s difference measured in production.

    A correlated subquery reappears in the plan as a SubPlan executed per row.
    Asserting on the PLAN rather than on a stopwatch keeps this regression
    deterministic on a loaded two-core NAS.
    """
    factory, created = db
    pid, mid, polid, _ = _setup(factory, created, ["backend python"])
    _evaluate(factory, pid, mid, polid)

    async def go():
        async with factory() as s:
            sql, params = matching.feed_total_sql(pid)
            plan = "\n".join(row[0] for row in (
                await s.execute(sa.text("EXPLAIN " + sql), params)).all())
            assert "SubPlan" not in plan, f"correlated per-row lookup survived:\n{plan}"

    asyncio.run(go())


def test_the_feed_state_index_exists(db):
    """Only 15,4 % of profile_vacancy_state rows can belong to a feed.

    Measured on production: 35.092 rows, 5.400 with a `current_eval_id`.
    Counting a feed sequentially scanned all of them — 604 ms in EXPLAIN
    ANALYZE, `Rows Removed by Filter: 24712`. The partial index restricts the
    read to the rows that can possibly be in a feed, the same shape the project
    already uses for `ix_pvs_saved_feed_keyset`.

    This asserts the index EXISTS rather than that a plan uses it: on a test
    database of a handful of rows PostgreSQL correctly prefers a sequential
    scan, so a plan assertion here would pass with or without the fix and prove
    nothing. The plan evidence belongs to the production measurement, recorded
    in the closing act.
    """
    factory, _ = db

    async def go():
        async with factory() as s:
            definition = await s.scalar(sa.text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname='jobhunt' AND indexname=:name"),
                {"name": "ix_pvs_feed_current_eval"})
            assert definition, "the partial index for feed counting is missing"
            assert "current_eval_id IS NOT NULL" in definition
            assert "profile_id" in definition

    asyncio.run(go())
