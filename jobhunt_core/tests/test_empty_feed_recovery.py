"""A valid empty feed records recovery without re-enqueuing the profile."""

import asyncio

from jobhunt_core import matching
from jobhunt_core.tasks import matching as matching_tasks
from jobhunt_core.tests.test_integration_matching import db, _setup  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)


def test_valid_empty_feed_records_attempt_and_drains_recovery(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, created = db
    pid, _, _, _ = _setup(factory, created, ["python developer", "python engineer"])

    async def pending():
        async with factory() as session:
            return await matching_tasks._recovery_targets(session, set())

    async def check():
        await matching_tasks._evaluate_and_record(factory, pid)
        assert pid not in await pending()
        async with factory() as session:
            await matching.declare_profile_exclusions(
                session,
                pid,
                [
                    {"kind": "title_contains", "pattern": "python"},
                ],
            )
            await session.commit()
        assert pid in await pending()
        await matching_tasks._evaluate_and_record(factory, pid)
        async with factory() as session:
            rows, _ = await matching.feed(session, pid)
            assert rows == []
        assert pid not in await pending(), (
            "Empty feed published but recovery attempt not recorded"
        )

    asyncio.run(check())
