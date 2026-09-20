"""Returning to an already evaluated CV must rebuild its withdrawn feed."""

import asyncio

from jobhunt_core import matching
from jobhunt_core.shadow import projector
from jobhunt_core.tests.test_integration_api import db
from jobhunt_core.tests.test_profile_snapshot_concurrency import (
    _current_snapshot,
    _deliver,
)
from jobhunt_core.tests.test_profile_snapshot_delivery import _seed, _snapshot
from jobhunt_core.tests.test_review_closure_20260907 import _pending


def test_empty_then_previous_cv_rearms_and_rebuilds_feed(db):
    factory, created = db
    pid, _, _, _ = _seed(factory, created)

    async def check():
        original = await _current_snapshot(factory, pid, True, 1)
        await _deliver(factory, pid, original)
        await projector._evaluate_and_record(factory, pid)
        assert pid not in await _pending(factory)
        async with factory() as session:
            assert (await matching.feed(session, pid))[0]
        await _deliver(factory, pid, _snapshot(2, title=None))
        async with factory() as session:
            assert not (await matching.feed(session, pid))[0]
        await _deliver(factory, pid, {**original, "version": 3})
        assert pid in await _pending(factory)
        await projector._evaluate_and_record(factory, pid)
        async with factory() as session:
            assert (await matching.feed(session, pid))[0]
        assert pid not in await _pending(factory)

    asyncio.run(check())
