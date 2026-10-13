"""Compare Core facet pages with the existing BFF view using aggregate output only.

This operator command is read-only. It never prints user IDs, profile IDs, job
identities, titles, descriptions, or URLs. Run it inside the production BFF
image so it uses the same mappings, credentials, overlays, and DTO conversion as
the application.
"""

import asyncio
import json
import time

import sqlalchemy as sa

from database import async_session
from services.matching.core_client import CoreMatching
from services.schools.presentation import overlay_school_results


def _job_refs(items: list[dict]) -> set[str]:
    return {str(item["match"].job_hash) for item in items}


def aggregate_comparison(
    facets: dict,
    baseline: list[dict],
    category_refs: dict[str, set[str]],
    watchlist_refs: set[str],
) -> dict:
    baseline_refs = _job_refs(baseline)
    category_union: set[str] = set()
    category_memberships = 0
    counts = {row["id"]: row["count"] for row in facets["categories"]}
    mismatch_l1 = 0
    for category, refs in category_refs.items():
        category_union.update(refs)
        category_memberships += len(refs)
        mismatch_l1 += abs(counts[category] - len(refs))
    visible_watchlist = _job_refs(
        [item for item in baseline if item.get("school") is not None]
    )
    return {
        "facet_total": facets["total"],
        "baseline_total": len(baseline_refs),
        "category_union_total": len(category_union),
        "category_duplicate_memberships": category_memberships - len(category_union),
        "category_identity_mismatch": len(baseline_refs ^ category_union),
        "category_count_mismatch_l1": mismatch_l1,
        "facet_watchlist": facets["watchlist_count"],
        "visible_watchlist": len(visible_watchlist),
        "watchlist_identity_mismatch": len(visible_watchlist ^ watchlist_refs),
    }


async def main() -> None:
    reports = []
    async with async_session() as db:
        users = (
            (
                await db.execute(
                    sa.text("SELECT user_id FROM jobhunt_profile_map ORDER BY user_id")
                )
            )
            .scalars()
            .all()
        )
        for profile_index, user_id in enumerate(users, 1):
            started = time.perf_counter()
            matching = CoreMatching(db)
            facets = await matching.facets(user_id)
            baseline, _ = await matching.results(user_id, limit=3000)
            baseline = await overlay_school_results(db, user_id, baseline)
            category_refs = {}
            for row in facets["categories"]:
                items, _ = await matching.results(
                    user_id, limit=3000, category=row["id"]
                )
                category_refs[row["id"]] = _job_refs(items)
            watchlist, _ = await matching.results(
                user_id, limit=3000, category="__watchlist"
            )
            report = aggregate_comparison(
                facets, baseline, category_refs, _job_refs(watchlist)
            )
            report["profile_index"] = profile_index
            report["elapsed_s"] = round(time.perf_counter() - started, 3)
            reports.append(report)
    print(json.dumps({"profiles": len(reports), "reports": reports}, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
