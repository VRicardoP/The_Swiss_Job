"""Prove real embedding and feed recovery on NAS core_copy, never production.

Uses the registered model/recipe and existing embedding/evaluation functions.
Requires an offline read-only model cache. No corpus harvest or holdout evaluation.
"""

import asyncio
import json
import logging
import os
import time

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.pool import NullPool

from jobhunt_core import embedding_recipes, embeddings, matching, profiles
from jobhunt_core.database import create_core_engine
from jobhunt_core.shadow import projector


async def main():
    if (
        os.environ.get("CORE_DATABASE_URL")
        != ("postgresql+asyncpg://jobhunt_core@127.0.0.1:5432/core_copy")
        or os.environ.get("HF_HUB_OFFLINE") != "1"
    ):
        raise ValueError("isolated core_copy and offline model cache required")
    started = time.monotonic()
    engine = create_core_engine(poolclass=NullPool)
    factory = async_sessionmaker(engine)
    try:
        async with factory() as db:
            ids = list(
                (
                    await db.scalars(
                        sa.text(
                            "SELECT id FROM profiles WHERE projection_version>0 AND projection_active"
                        )
                    )
                ).all()
            )
            models = await embeddings.active_models(db)
        assert ids and models
        encoded = copied_total = 0
        for model in models:
            if model.dim != embeddings.EMBED_DIM:
                continue
            async with factory() as db:
                pending = [
                    r
                    for r in await embeddings.pending_profile_revisions(
                        db, model.id, limit=200
                    )
                    if r.profile_id in ids
                ]
                copied, remaining = await embeddings.copy_profile_vectors_by_text(
                    db, model.id, pending
                )
                await db.commit()
                copied_total += copied
            if remaining:
                backend = embeddings.get_backend(model.name, model.version)
                vectors = embedding_recipes.encode_views(
                    backend,
                    [
                        embedding_recipes.profile_views(r.content, model.recipe_version)
                        for r in remaining
                    ],
                )
                async with factory() as db:
                    encoded += await embeddings.store_profile_embeddings(
                        db,
                        model.id,
                        [
                            {
                                "revision_id": r.id,
                                "profile_id": r.profile_id,
                                "vector": vector,
                            }
                            for r, vector in zip(remaining, vectors, strict=True)
                        ],
                    )
                    await db.commit()
        feeds = []
        for pid in ids:
            await projector._evaluate_and_record(factory, pid)
            async with factory() as db:
                revision = await profiles.current_revision(db, pid)
                feed, _ = await matching.feed(db, pid)
                assert feed, "recovery left an empty feed"
                stale = await db.scalar(
                    sa.text(
                        "SELECT count(*) FROM profile_vacancy_state pvs "
                        "JOIN match_evaluations e ON e.id=pvs.current_eval_id "
                        "WHERE pvs.profile_id=:p AND e.profile_revision_id<>:r"
                    ),
                    {"p": pid, "r": revision.id},
                )
                assert stale == 0, "feed refers to an obsolete revision"
                feeds.append(len(feed))
        print(
            json.dumps(
                {
                    "verdict": "profile_recovery_verified_on_copy",
                    "profiles": len(ids),
                    "encoded": encoded,
                    "copied": copied_total,
                    "served_feed_counts": feeds,
                    "stale_revision_pointers": 0,
                    "seconds": round(time.monotonic() - started, 3),
                    "live_deployment_verified": False,
                }
            )
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(main())
    except Exception as exc:
        print(json.dumps({"verdict": "failed", "type": type(exc).__name__}))
        raise SystemExit(1) from None
