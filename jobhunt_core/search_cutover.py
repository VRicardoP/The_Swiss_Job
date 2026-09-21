"""Frozen SwissJob searches: seed missing corpus -> plan -> apply/revert.

SOURCE_DATABASE_URL, SOURCE_REDIS_URL, CORE_DATABASE_URL and SEARCH_FREEZE_URL
come from the environment, never argv. Drain legacy search/corpus writers and
core capture/harvest before use; this command does NOT pause or flip them.
Capture the snapshot with backend/scripts/capture_search_handover.py on NAS.
Use a new --report path on retry: prepared receipts precede each seed commit;
shared corpus is retained on search revert, never deleted as a side effect.
"""
import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
import uuid

import httpx
import redis.asyncio as redis
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from jobhunt_core.database import create_core_engine
from jobhunt_core.document_cutover import private_read, private_write
from jobhunt_core.import_schools import digest
from jobhunt_core.import_search_corpus import seed_missing_offers
from jobhunt_core.import_swissjob_searches import SearchMigrationError, apply_plan, prepare_plan, resolve_pending

# Version-1 capture contract (backend/services/search_handover.py). Includes
# selection AND imported public content; no vectors or scrape refresh stamps.
JOBS_FINGERPRINT_SQL = """
    SELECT count(*) AS n, md5(coalesce(string_agg(md5(jsonb_build_array(
        hash,url,source,is_active,duplicate_of,canton,remote,language,
        seniority,contract_type,salary_min_chf,salary_max_chf,
        first_seen_at,search_vector::text,title,company,description,location,
        tags,apply_url,salary_original,salary_currency,salary_period
    )::text), '' ORDER BY hash COLLATE "C"), '')) AS digest FROM public.jobs
"""


def read_snapshot(path):
    snapshot = private_read(path)
    if snapshot.get("version") != 1 or digest({k:v for k,v in snapshot.items() if k != "seal"}) != snapshot.get("seal"):
        raise SearchMigrationError("invalid sealed source snapshot")
    return snapshot


async def require_freeze():
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.get(os.environ["SEARCH_FREEZE_URL"])
        response.raise_for_status()
        state = response.json()
    if state != {"writes": "frozen"}:
        raise SearchMigrationError("saved-search writers must remain frozen")


async def verify_source(source, markers, snapshot):
    await source.execute(sa.text("SET LOCAL lock_timeout='5s'"))
    await source.execute(sa.text("SET LOCAL statement_timeout='60s'"))
    await source.execute(sa.text("SET LOCAL timezone='UTC'"))
    await source.execute(sa.text("LOCK TABLE public.jobhunt_routing,public.jobhunt_profile_map,"
                                 "public.saved_searches,public.jobs IN SHARE MODE"))
    if await source.scalar(sa.text("SELECT current_database()")) != snapshot["source_database"]:
        raise SearchMigrationError("wrong source database")
    modes = (await source.execute(sa.text("SELECT mode FROM public.jobhunt_routing "
        "WHERE consumer_id='swissjob' AND capability='saved_searches'"))).scalars()
    if any(mode not in {"local", "shadow", "core_read"} for mode in modes):
        raise SearchMigrationError("legacy search authority already changed")
    rows = [dict(row) for row in (await source.execute(sa.text(
        "SELECT * FROM public.saved_searches ORDER BY id"))).mappings()]
    if digest(rows) != digest(snapshot["rows"]):
        raise SearchMigrationError("source searches changed")
    links = dict((await source.execute(sa.text("SELECT user_id::text,core_profile_id::text "
        "FROM public.jobhunt_profile_map WHERE user_id=ANY(:ids)"),
        {"ids": [uuid.UUID(uid) for uid in snapshot["bindings"]]})).all())
    if links != snapshot["bindings"]:
        raise SearchMigrationError("source profile mapping changed")
    fingerprint = dict((await source.execute(sa.text(JOBS_FINGERPRINT_SQL))).mappings().one())
    if fingerprint != snapshot["jobs_fingerprint"]:
        raise SearchMigrationError("source corpus changed")
    if (await markers.info("server"))["run_id"] != snapshot["redis_run_id"]:
        raise SearchMigrationError("source Redis identity changed")
    if markers.connection_pool.connection_kwargs.get("db", 0) != snapshot["redis_db"]:
        raise SearchMigrationError("wrong source Redis database")
    for sid, values in snapshot["sent"].items():
        keys = [f"saved_search:sent:{sid}:{key}" for key in values]
        actual = await markers.mget(keys) if keys else []
        if len(actual) != len(keys) or any(value not in (None,b"1") for value in actual):
            raise SearchMigrationError("invalid sent-marker response")
        if [value is not None for value in actual] != list(values.values()):
            raise SearchMigrationError("source sent markers changed")


async def run(args):
    snapshot = read_snapshot(args.snapshot)
    await require_freeze()
    source_engine = create_async_engine(os.environ["SOURCE_DATABASE_URL"], poolclass=NullPool)
    core_engine = create_core_engine(poolclass=NullPool)
    factory = async_sessionmaker(core_engine)
    markers = redis.from_url(os.environ["SOURCE_REDIS_URL"], socket_connect_timeout=2, socket_timeout=2)
    try:
        async with async_sessionmaker(source_engine)() as source, source.begin():
            await verify_source(source, markers, snapshot)
            if args.command == "seed":
                grouped = defaultdict(dict)
                for sid, candidates in snapshot["candidates"].items():
                    for row in candidates:
                        if not snapshot["sent"][sid][row["hash"]]:
                            previous = grouped[row["source"]].setdefault(row["hash"], row)
                            if previous != row:
                                raise SearchMigrationError("inconsistent captured offer")
                results = []
                for index, (name, rows) in enumerate(sorted(grouped.items())):
                    async with factory() as core, core.begin():
                        result = await seed_missing_offers(core, name, list(rows.values()))
                        receipt = {"source": name, "snapshot_seal": snapshot["seal"], **result}
                        private_write(f"{args.report}.{index}.prepared.json", receipt)
                        await require_freeze()
                    results.append(receipt)
                result = {"operation": "seed", "snapshot_seal": snapshot["seal"], "sources": results}
            else:
                async with factory() as core, core.begin():
                    await core.execute(sa.text("SET LOCAL lock_timeout='5s'"))
                    await core.execute(sa.text("SET LOCAL statement_timeout='120s'"))
                    if args.command == "plan":
                        plan = await prepare_plan(core, consumer="swissjob-shadow", bindings=snapshot["bindings"],
                            rows=snapshot["rows"], targets=private_read(args.targets),
                            pending=await resolve_pending(core,snapshot), recorded_at=datetime.now(timezone.utc).isoformat())
                        plan["snapshot_seal"] = snapshot["seal"]
                        plan["seal"] = digest({k:v for k,v in plan.items() if k != "seal"})
                        private_write(args.plan, plan)
                        result = {"operation": "plan", "seal": plan["seal"], "searches": len(plan["after"])}
                    else:
                        plan = private_read(args.plan)
                        if plan.get("snapshot_seal") != snapshot["seal"]:
                            raise SearchMigrationError("plan belongs to another snapshot")
                        result = await apply_plan(core, plan, reverse=args.command == "revert")
                        result.update(operation=args.command, seal=plan["seal"])
                    await require_freeze()
        private_write(args.report,result)
        return {"operation": args.command, "verified": True,
                "inserted_listings": sum(r["inserted_listings"] for r in result.get("sources", []))}
    finally:
        await markers.aclose()
        await source_engine.dispose()
        await core_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seed", "plan", "apply", "revert"))
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--plan")
    parser.add_argument("--targets")
    args = parser.parse_args()
    if args.command != "seed" and not args.plan:
        parser.error("--plan is required")
    if args.command == "plan" and not args.targets:
        parser.error("--targets is required for plan")
    try:
        print(json.dumps(asyncio.run(run(args))))
    except Exception as exc:
        print(json.dumps({"verdict": "error", "type": type(exc).__name__,
                          "recovery": "keep frozen; inspect prepared receipts; retry with a new report path"}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
