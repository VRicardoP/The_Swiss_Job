"""Budgeted CE materialization; matching remains the sole persistence boundary."""

import asyncio
import logging
import time

import sqlalchemy as sa

from jobhunt_core.ce_runtime import BudgetedScorer

logger = logging.getLogger(__name__)
scorer_factory = BudgetedScorer


async def materialize_misses(
    session_factory,
    profile_id,
    model_id,
    policy_id,
    budget_seconds=3600.0,
    batch_pairs=8,
):
    """Small resumable batches, with a killable process and shared SQL deadline.

    Start with ONE document; never infer 64 documents just to estimate cost.
    Observed cost guides admission, but timeout enforces the bound when a
    first prediction/model load hangs. Reserve margin for rollback and reaping.
    """
    from jobhunt_core import matching

    if batch_pairs < 1:
        raise ValueError("batch_pairs must be positive")
    started = time.monotonic()
    work_budget = max(0.0, budget_seconds - matching._margen_cierre(budget_seconds))
    scored, pending = 0, None
    cost_doc = cost_prep = 0.0
    scorer = scorer_factory()

    def remaining():
        return work_budget - (time.monotonic() - started)

    def backlog():
        return {
            "scored": scored,
            "remaining": pending,
            "agotado": True,
            "status": "backlog",
        }

    try:
        if not work_budget:
            return backlog()
        async with asyncio.timeout(work_budget):
            while True:
                if remaining() <= cost_prep:
                    return backlog()
                prep_started = time.monotonic()
                async with session_factory() as session:
                    computed = await matching.compute_policy_feed(
                        session,
                        profile_id,
                        model_id,
                        policy_id,
                        limit=matching.CANONICAL_EVAL_LIMIT,
                        exclude_dismissed=False,
                        ce_inference=False,
                    )
                    if computed["status"] == "ok_prep":
                        consumer = (
                            await session.execute(
                                sa.text(
                                    "SELECT c.name FROM profiles p JOIN consumers c "
                                    "ON c.id = p.consumer_id WHERE p.id = :pid"
                                ),
                                {"pid": profile_id},
                            )
                        ).scalar_one()
                cost_prep = max(cost_prep, time.monotonic() - prep_started)
                if computed["status"] != "ok_prep":
                    return {
                        "scored": scored,
                        "remaining": 0,
                        "agotado": False,
                        "status": computed["status"],
                    }
                prep = computed["prep"]
                misses = prep["misses"]
                pending = len(misses)
                if not misses:
                    return {
                        "scored": scored,
                        "remaining": 0,
                        "agotado": False,
                        "status": "ok",
                    }
                offset = 0
                while offset < len(misses):
                    n = min(
                        1 if not cost_doc else min(batch_pairs, 8), len(misses) - offset
                    )
                    if remaining() <= 0 or (
                        cost_doc and n * cost_doc * 1.25 > remaining()
                    ):
                        return backlog()
                    batch = misses[offset : offset + n]
                    batch_prep = dict(
                        prep,
                        misses=batch,
                        candidates=batch,
                        cache={},
                        documentos=prep["documentos"][offset : offset + n],
                    )
                    batch_started = time.monotonic()
                    fresh = await scorer.score(batch_prep, remaining())
                    rows = matching._ce_assemble(batch_prep, fresh)
                    async with session_factory() as session:
                        await matching._persist_eval_rows(
                            session,
                            profile_id,
                            rows,
                            computed["profile_revision_id"],
                            model_id,
                            policy_id,
                            consumer,
                        )
                        await session.commit()
                    scored += len(rows)
                    offset += n
                    pending = len(misses) - offset
                    cost_doc = max(cost_doc, (time.monotonic() - batch_started) / n)
    except TimeoutError:
        logger.warning("materialize: deadline expired; resumable backlog")
        return backlog()
    finally:
        await scorer.aclose()
