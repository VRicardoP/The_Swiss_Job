"""Publication/recovery and transaction invariants of the profile authority handover."""

import asyncio
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from jobhunt_core import matching, profiles
from jobhunt_core.api.v1_profile_snapshot import (
    ProfileSnapshotWrite,
    put_source_snapshot,
)
from jobhunt_core.shadow import projector
from jobhunt_core.tests.test_integration_api import db  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
from jobhunt_core.tests.test_profile_snapshot_delivery import _seed, _put, _snapshot
from jobhunt_core.tests.test_review_closure_20260907 import _pending


async def _current_snapshot(factory, pid, active, version):
    async with factory() as s:
        content = dict((await profiles.current_revision(s, pid)).content)
    content.pop("target_roles", None)
    return {"version": version, "active": active, "content": content}


async def _deliver(factory, pid, body):
    async with factory() as s:
        cid = await s.scalar(
            sa.text("SELECT consumer_id FROM profiles WHERE id=:p"), {"p": pid}
        )
        return await put_source_snapshot(
            pid,
            ProfileSnapshotWrite(**body),
            session=s,
            principal=SimpleNamespace(consumer_id=cid),
        )


def test_inactive_during_inference_fences_unchanged_revision(db, monkeypatch):  # noqa: F811  (la fixture, no una redefinición)
    factory, created = db
    pid, _, _, _ = _seed(factory, created)
    original = matching.compute_policy_feed

    async def deactivate_after_read(*args, **kwargs):
        result = await original(*args, **kwargs)
        await _deliver(factory, pid, await _current_snapshot(factory, pid, False, 1))
        async with factory() as s:
            assert str((await profiles.current_revision(s, pid)).id) == str(
                result["profile_revision_id"]
            )
        return result

    monkeypatch.setattr(matching, "compute_policy_feed", deactivate_after_read)

    async def check():
        result = await matching.evaluate_profile(
            factory, pid, created["models"][0], created["policies"][0]
        )
        assert result["status"] == "descartado_por_deriva", result
        async with factory() as s:
            assert (await matching.feed(s, pid))[0] == []

    asyncio.run(check())


def test_inactive_not_in_recovery_and_reactivation_rearms_same_revision(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, created = db
    pid, _, _, _ = _seed(factory, created)

    async def check():
        await projector._evaluate_and_record(factory, pid)
        assert pid not in await _pending(factory)
        await _deliver(factory, pid, await _current_snapshot(factory, pid, False, 1))
        assert pid not in await _pending(factory)
        await _deliver(factory, pid, await _current_snapshot(factory, pid, True, 2))
        assert pid in await _pending(factory)

    asyncio.run(check())


def test_snapshot_failure_rolls_back_revision_and_authority_together(db, monkeypatch):  # noqa: F811  (la fixture, no una redefinición)
    factory, created = db
    pid, _, _, _ = _seed(factory, created)
    original = profiles.save_profile_revision

    async def fail_after_revision(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("controlled failure before authority write")

    monkeypatch.setattr(profiles, "save_profile_revision", fail_after_revision)

    async def check():
        async with factory() as s:
            before = (await profiles.current_revision(s, pid)).id
        with pytest.raises(RuntimeError, match="controlled failure"):
            await _deliver(factory, pid, _snapshot())
        async with factory() as s:
            assert (await profiles.current_revision(s, pid)).id == before
            assert (
                await s.scalar(
                    sa.text("SELECT projection_version FROM profiles WHERE id=:p"),
                    {"p": pid},
                )
                == 0
            )

    asyncio.run(check())


def test_source_snapshot_preserves_core_only_target_roles(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, created = db
    pid, _, token, _ = _seed(factory, created)

    async def edit():
        async with factory() as s:
            content = dict((await profiles.current_revision(s, pid)).content)
            content["target_roles"] = ["Core configured role"]
            await profiles.save_profile_revision(s, pid, content)
            await s.commit()

    asyncio.run(edit())
    assert _put(factory, pid, token, _snapshot()).status_code == 200

    async def check():
        async with factory() as s:
            assert (await profiles.current_revision(s, pid)).content[
                "target_roles"
            ] == ["Core configured role"]

    asyncio.run(check())
