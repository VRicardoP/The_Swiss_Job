"""Regresiones permanentes de invalidación, publicación y presupuesto."""

import asyncio
import time
from types import SimpleNamespace

import sqlalchemy as sa

from jobhunt_core import matching
from jobhunt_core.shadow import projector
from jobhunt_core.tests.test_integration_matching import db, _setup  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)


async def _declare(factory, pid):
    # Mismo orden transaccional que PUT /profiles/{id}/exclusions.
    async with factory() as s:
        await s.execute(
            sa.text("SELECT id FROM profiles WHERE id=:p FOR UPDATE"), {"p": pid}
        )
        await matching.declare_profile_exclusions(
            s, pid, [{"kind": "title_contains", "pattern": "python"}]
        )
        await s.commit()


async def _pending(factory):
    async with factory() as s:
        result = await s.execute(
            sa.text(projector._RECOVERY_NEEDED_SQL).bindparams(
                sa.bindparam("excluded", expanding=True),
                sa.bindparam("evaluated", expanding=True),
            ),
            {
                "excluded": [projector._NO_PROFILE],
                "evaluated": [projector._NO_PROFILE],
                "dim": 384,
                "cap": 200,
            },
        )
        return [r.id for r in result.all()]


def test_exclusion_change_must_rearm_recovery(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, created = db
    pid, mid, pol, _ = _setup(factory, created, ["python developer", "python engineer"])

    async def check():
        await projector._evaluate_and_record(factory, pid)
        assert pid not in await _pending(factory)
        await _declare(factory, pid)
        async with factory() as s:
            rows, _ = await matching.feed(s, pid)
        assert len(rows) == 2
        assert pid in await _pending(factory), (
            "Dos ofertas excluidas siguen servidas SIN señal de recuperación"
        )

    asyncio.run(check())


def test_exclusion_change_during_preparation_must_fence_publication(db, monkeypatch):  # noqa: F811  (la fixture, no una redefinición)
    factory, created = db
    pid, mid, pol, _ = _setup(factory, created, ["python developer", "python engineer"])
    original = matching.compute_policy_feed
    fired = False

    async def prepare_then_edit(*args, **kwargs):
        nonlocal fired
        result = await original(*args, **kwargs)
        if not fired:
            fired = True
            await _declare(factory, pid)
        return result

    monkeypatch.setattr(matching, "compute_policy_feed", prepare_then_edit)

    async def check():
        result = await matching.evaluate_profile(factory, pid, mid, pol)
        async with factory() as s:
            rows, _ = await matching.feed(s, pid)
        assert result["status"] == "descartado_por_deriva", (
            f"Publica snapshot anterior a exclusiones: {result}, feed_n={len(rows)}"
        )

    asyncio.run(check())


def test_first_cold_batch_must_respect_budget(monkeypatch):
    clock = [0.0]
    scored = []
    items = list(range(64))

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, *args, **kwargs):
            return SimpleNamespace(scalar_one=lambda: "consumer")

        async def commit(self):
            pass

    async def prepare(*args, **kwargs):
        clock[0] += 135.0
        remaining = [i for i in items if i not in scored]
        return {
            "status": "ok_prep",
            "profile_revision_id": "rev",
            "prep": {"misses": remaining, "documentos": remaining},
        }

    def infer(prep):
        clock[0] += 28.0 * len(prep["misses"])
        return {i: i for i in prep["misses"]}

    async def persist(s, p, rows, *args):
        scored.extend(rows)

    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(matching, "compute_policy_feed", prepare)
    monkeypatch.setattr(matching, "_ce_score_misses", infer)
    monkeypatch.setattr(matching, "_ce_assemble", lambda prep, fresh: list(fresh))
    monkeypatch.setattr(matching, "_persist_eval_rows", persist)
    result = asyncio.run(
        matching.materialize_misses(Session, "p", "m", "pol", budget_seconds=1200)
    )
    assert clock[0] <= 1200, (
        f"Primera tanda fría: {clock[0]} segundos simulados, resultado={result}"
    )
