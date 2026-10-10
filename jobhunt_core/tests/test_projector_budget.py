"""Small, DB-free checks for the periodic projector work budget."""

import asyncio
from contextlib import asynccontextmanager
import uuid

from jobhunt_core.shadow import projector


def test_after_batch_stops_at_profile_budget(monkeypatch):
    targets = [uuid.uuid4(), uuid.uuid4()]
    evaluated = []

    @asynccontextmanager
    async def factory():
        yield object()

    async def no_embeddings(*_args, **_kwargs):
        return None

    async def all_targets(_session, _result):
        return targets

    async def evaluate(_factory, profile_id):
        evaluated.append(profile_id)

    monkeypatch.setattr(projector, "_drain_embeddings", no_embeddings)
    monkeypatch.setattr(projector, "_eval_targets", all_targets)
    monkeypatch.setattr(projector, "_evaluate_and_record", evaluate)
    result = projector._BatchResult(
        changes=0,
        upserts=0,
        closes=0,
        erased=0,
        revisions_new=1,
        corpus_changed=True,
        affected_profiles=set(),
    )

    selected = asyncio.run(projector._after_batch(factory, result, max_profiles=1))

    assert selected == targets[:1]
    assert evaluated == targets[:1]


def test_exhausted_profile_budget_skips_recovery_io():
    def factory():
        raise AssertionError("an exhausted budget must not open a DB session")

    recovered = asyncio.run(
        projector._replay_after_batch(factory, set(), max_profiles=0)
    )

    assert recovered == 0
