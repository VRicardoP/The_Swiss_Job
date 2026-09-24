"""Different logical runs must not fetch the same scope simultaneously."""

import asyncio
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core import runs
from jobhunt_core.harvest.runner import _still_claim_owner
from jobhunt_core.tests.test_integration_runs import db, _seed_scopes, pytestmark  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)


def test_different_run_keys_share_scope_exclusion(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, created = db
    (sid,) = _seed_scopes(factory, created, n=1)

    async def check():
        async with factory() as s:
            first = await runs.start_run(s, "exclusive-a-" + str(uuid.uuid4()))
            second = await runs.start_run(s, "exclusive-b-" + str(uuid.uuid4()))
            created["runs"].extend([first, second])
            token = await runs.claim_scope_run(s, first, sid)
            await s.commit()
            assert token is not None
        async with factory() as s:
            assert await runs.claim_scope_run(s, second, sid) is None
            await s.commit()
        async with factory() as s:
            assert await runs.finish_scope_run(s, first, sid, "ok", token)
            await s.commit()
        async with factory() as s:
            assert await runs.claim_scope_run(s, second, sid) is not None
            await s.commit()

    asyncio.run(check())


def test_new_run_revokes_expired_claim_from_old_run(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, created = db
    (sid,) = _seed_scopes(factory, created, n=1)

    async def check():
        async with factory() as s:
            first = await runs.start_run(s, "expired-a-" + str(uuid.uuid4()))
            second = await runs.start_run(s, "expired-b-" + str(uuid.uuid4()))
            created["runs"].extend([first, second])
            token = await runs.claim_scope_run(s, first, sid)
            await s.execute(
                sa.text(
                    "UPDATE source_harvest_runs SET heartbeat_at=clock_timestamp()-interval '2 hours' "
                    "WHERE run_id=:rid AND scope_id=:sid"
                ),
                {"rid": first, "sid": sid},
            )
            await s.commit()
        async with factory() as s:
            replacement = await runs.claim_scope_run(s, second, sid)
            assert replacement is not None and replacement != token
            await s.commit()
        async with factory() as s:
            assert not await _still_claim_owner(s, str(sid), token)
            assert not await runs.beat_scope_run(s, first, sid, token)
            assert not await runs.finish_scope_run(s, first, sid, "ok", token)
            await s.commit()

    asyncio.run(check())


def test_manual_fetch_excludes_another_run_before_external_io(db, monkeypatch):  # noqa: F811  (la fixture, no una redefinición)
    from contextlib import asynccontextmanager
    from jobhunt_core.harvest.provider import BaseProvider
    from jobhunt_core.harvest.types import FetchResult
    from jobhunt_core.tasks import harvest

    factory, created = db
    (sid,) = _seed_scopes(factory, created, n=1)

    @asynccontextmanager
    async def sessions():
        yield factory

    monkeypatch.setattr(harvest, "task_session_factory", sessions)

    async def check():
        entered, release = asyncio.Event(), asyncio.Event()

        class Provider(BaseProvider):
            name = "arbeitnow"

            async def fetch_new(self, params, cursor, http):
                entered.set()
                await release.wait()
                return FetchResult((), {})

        monkeypatch.setattr(harvest, "get_provider", lambda _: Provider())
        manual = asyncio.create_task(harvest._run_scope_impl(str(sid)))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            async with factory() as s:
                other = await runs.start_run(s, "scheduled-" + str(uuid.uuid4()))
                created["runs"].append(other)
                await s.commit()
                assert await runs.claim_scope_run(s, other, sid) is None
                await s.commit()
        finally:
            release.set()
            outcome = await asyncio.wait_for(manual, 5)
            async with factory() as s:
                created["runs"].extend(
                    (
                        await s.execute(
                            sa.text(
                                "SELECT run_id FROM source_harvest_runs WHERE scope_id=:sid"
                            ),
                            {"sid": sid},
                        )
                    )
                    .scalars()
                    .all()
                )
        assert outcome.status == "ok"

    asyncio.run(check())


def test_completed_retry_does_not_revoke_another_run(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, created = db
    (sid,) = _seed_scopes(factory, created, n=1)

    async def check():
        async with factory() as s:
            done = await runs.start_run(s, "done-" + str(uuid.uuid4()))
            other = await runs.start_run(s, "old-" + str(uuid.uuid4()))
            created["runs"].extend([done, other])
            first = await runs.claim_scope_run(s, done, sid)
            assert await runs.finish_scope_run(s, done, sid, "ok", first)
            token = await runs.claim_scope_run(s, other, sid)
            await s.execute(
                sa.text(
                    "UPDATE source_harvest_runs SET heartbeat_at=clock_timestamp()-interval '2 hours' "
                    "WHERE run_id=:rid AND scope_id=:sid"
                ),
                {"rid": other, "sid": sid},
            )
            await s.commit()
        async with factory() as s:
            assert await runs.claim_scope_run(s, done, sid) is None
            await s.commit()
        async with factory() as s:
            # Retrying an already-completed operation isn't a new claimant.
            assert await _still_claim_owner(s, str(sid), token)

    asyncio.run(check())


def test_simultaneous_different_runs_have_one_winner(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, created = db
    (sid,) = _seed_scopes(factory, created, n=1)

    async def check():
        async with factory() as s:
            a = await runs.start_run(s, "race-a-" + str(uuid.uuid4()))
            b = await runs.start_run(s, "race-b-" + str(uuid.uuid4()))
            created["runs"].extend([a, b])
            await s.commit()
        started = asyncio.Event()

        async def second():
            async with factory() as s:
                started.set()
                token = await runs.claim_scope_run(s, b, sid)
                await s.commit()
                return token

        async with factory() as s:
            assert await runs.claim_scope_run(s, a, sid) is not None
            pending = asyncio.create_task(second())
            try:
                await started.wait()
                await s.commit()
                assert await asyncio.wait_for(pending, 5) is None
            finally:
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(check())


@pytest.mark.parametrize("config_error", [False, True])
def test_standalone_failure_releases_claim(db, monkeypatch, config_error):  # noqa: F811  (la fixture, no una redefinición)
    from contextlib import asynccontextmanager
    from jobhunt_core.harvest.provider import BaseProvider, ProviderConfigError
    from jobhunt_core.tasks import harvest

    factory, created = db
    (sid,) = _seed_scopes(factory, created, n=1)

    @asynccontextmanager
    async def sessions():
        yield factory

    class Provider(BaseProvider):
        name = "arbeitnow"

        async def fetch_new(self, params, cursor, http):
            if config_error:
                raise ProviderConfigError("invalid fixture")
            raise RuntimeError("fixture transient")

    monkeypatch.setattr(harvest, "task_session_factory", sessions)
    monkeypatch.setattr(harvest, "get_provider", lambda _: Provider())

    async def check():
        try:
            if config_error:
                with pytest.raises(ProviderConfigError):
                    await harvest._run_scope_impl(str(sid))
            else:
                assert (await harvest._run_scope_impl(str(sid))).status == "error"
            async with factory() as s:
                rows = (
                    await s.execute(
                        sa.text(
                            "SELECT status,finished_at FROM source_harvest_runs WHERE scope_id=:sid"
                        ),
                        {"sid": sid},
                    )
                ).all()
                assert len(rows) == 1
                assert rows[0].status == "error" and rows[0].finished_at is not None
                other = await runs.start_run(s, "after-failure-" + str(uuid.uuid4()))
                created["runs"].append(other)
                assert await runs.claim_scope_run(s, other, sid) is not None
                await s.commit()
        finally:
            async with factory() as s:
                created["runs"].extend(
                    (
                        await s.execute(
                            sa.text(
                                "SELECT run_id FROM source_harvest_runs WHERE scope_id=:sid"
                            ),
                            {"sid": sid},
                        )
                    )
                    .scalars()
                    .all()
                )

    asyncio.run(check())


def test_removed_scope_cannot_gain_claim(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, created = db

    async def check():
        async with factory() as s:
            rid = await runs.start_run(s, "removed-" + str(uuid.uuid4()))
            created["runs"].append(rid)
            assert await runs.claim_scope_run(s, rid, uuid.uuid4()) is None
            await s.commit()

    asyncio.run(check())
