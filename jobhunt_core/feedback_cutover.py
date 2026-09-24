"""Swiss feedback handover: plan -> apply -> verify, before writer activation.

SOURCE_DATABASE_URL, CORE_DATABASE_URL and FEEDBACK_FREEZE_URL are environment
only. The BFF must report writer=local,writes=frozen. School state was migrated
in E.15 and must still be core-authoritative. No command changes those switches.
`revert` is a pre-activation rollback, NOT permission to discard newer core edits.
All artifacts use the existing private, fsynced, no-overwrite writer. If a receipt
cannot be written after commit, replay the same plan with a fresh receipt path.
"""

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from jobhunt_core.database import create_core_engine
from jobhunt_core.document_cutover import private_read, private_write
from jobhunt_core.import_schools import digest
from jobhunt_core.import_swissjob_feedback import (
    FeedbackMigrationError,
    apply_plan,
    prepare_plan,
)
from jobhunt_core.school_source import lock_source


async def require_freeze():
    token = os.environ.get("FEEDBACK_FREEZE_TOKEN")
    headers = {"Authorization": "Bearer " + token} if token else {}
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.get(os.environ["FEEDBACK_FREEZE_URL"], headers=headers)
        response.raise_for_status()
        state = response.json()
    if state != {"writes": "frozen", "writer": "local"}:
        raise FeedbackMigrationError("local feedback writer must remain frozen")


async def read_source(session, tables, owners):
    matches, jobs = tables["match_results"], tables["jobs"]
    fields = ("id", "user_id", "job_hash", "feedback", "feedback_implicit")
    rows = (
        (
            await session.execute(
                sa.select(
                    *(matches.c[key] for key in fields),
                    jobs.c.url,
                )
                .join(jobs, matches.c.job_hash == jobs.c.hash)
                .where(
                    matches.c.user_id.in_(owners),
                )
                .order_by(matches.c.id)
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def run(args):
    if args.command == "plan":
        bindings, consumer = private_read(args.bindings), args.consumer
        plan = None
    else:
        plan = private_read(args.plan)
        bindings, consumer = plan["bindings"], plan["consumer"]
    await require_freeze()
    source_engine = create_async_engine(
        os.environ["SOURCE_DATABASE_URL"],
        poolclass=NullPool,
        connect_args={
            "server_settings": {
                "search_path": "public",
                "statement_timeout": "120000",
                "timezone": "UTC",
            }
        },
    )
    core_engine = create_core_engine(poolclass=NullPool)
    try:
        async with (
            async_sessionmaker(source_engine)() as source,
            async_sessionmaker(core_engine)() as core,
        ):
            async with source.begin(), core.begin():
                # Reuse the existing E.15 ownership/FK checks and source locks;
                # authority=core describes SCHOOLS, not the feedback switch.
                tables, owners = await lock_source(
                    source, "swissjob", bindings, authority="core"
                )
                rows = await read_source(source, tables, owners)
                await core.execute(sa.text("SET LOCAL lock_timeout='5s'"))
                await core.execute(sa.text("SET LOCAL statement_timeout='120s'"))
                if args.command == "plan":
                    plan = await prepare_plan(
                        core,
                        consumer=consumer,
                        bindings=bindings,
                        rows=rows,
                        recorded_at=datetime.now(timezone.utc).isoformat(),
                    )
                    await require_freeze()
                    private_write(args.plan, plan)
                    return {
                        "verdict": "sealed",
                        "seal": plan["seal"],
                        "source_rows": len(rows),
                        "target_changes": len(plan["changes"]),
                    }
                if digest(rows) != plan["source_sha256"]:
                    raise FeedbackMigrationError("source changed after sealed snapshot")
                result = await apply_plan(core, plan, reverse=args.command == "revert")
                await require_freeze()
                result.update({"seal": plan["seal"], "operation": args.command})
        private_write(args.report, result)
        return result
    finally:
        await source_engine.dispose()
        await core_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--bindings", required=True)
    plan.add_argument("--consumer", required=True)
    plan.add_argument("--plan", required=True)
    for operation in ("apply", "revert"):
        command = commands.add_parser(operation)
        command.add_argument("--plan", required=True)
        command.add_argument("--report", required=True)
    try:
        print(json.dumps(asyncio.run(run(parser.parse_args()))))
    except Exception as exc:
        # Exception values/SQL params can contain private marks or credentials.
        print(
            json.dumps(
                {
                    "verdict": "error",
                    "type": type(exc).__name__,
                    "recovery": "keep frozen; inspect/replay sealed plan before changing authority",
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
