"""Frozen school cutover. Credentials are environment-only, artifacts private.

SOURCE_DATABASE_URL, CORE_DATABASE_URL, SCHOOL_FREEZE_URL are required. Portfolio
also checks SOURCE_SCHOOL_OWNER_ID and SOURCE_CORE_PROFILE_ID against the sealed
binding. This command never flips routing, unfreezes a writer, or deletes core
corpus data. A lost post-commit receipt is recovered by replaying the same batch
while both writers remain frozen.
"""

import argparse
import asyncio
import json
import os
import uuid
from datetime import datetime, timezone

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from jobhunt_core.database import create_core_engine
from jobhunt_core.document_cutover import private_read, private_write
from jobhunt_core.import_schools import (
    SchoolMigrationError,
    canonical,
    digest,
    import_batch,
    snapshot,
)
from jobhunt_core.school_source import lock_source, read_source, reverse_sync, to_batch


async def require_freeze(origin):
    headers = {}
    if os.environ.get("SCHOOL_FREEZE_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["SCHOOL_FREEZE_TOKEN"]
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.get(os.environ["SCHOOL_FREEZE_URL"], headers=headers)
        response.raise_for_status()
        payload = response.json()
    state = (
        payload
        if origin == "swissjob"
        else payload.get("checks", {}).get("schedulers", {})
    )
    if state.get("writes") != "frozen":
        raise SchoolMigrationError("school source writer is not frozen")
    if (
        origin == "portfolio"
        and state.get("background_schedulers_enabled") is not False
    ):
        raise SchoolMigrationError("Portfolio schedulers must be quiesced for cutover")


def _write(path, value):
    private_write(path, json.loads(canonical(value)))


def _bindings(origin, bindings):
    result = {str(uid): str(uuid.UUID(str(pid))) for uid, pid in bindings.items()}
    if origin == "portfolio":
        expected = {
            str(int(os.environ["SOURCE_SCHOOL_OWNER_ID"])): str(
                uuid.UUID(os.environ["SOURCE_CORE_PROFILE_ID"])
            )
        }
        if result != expected:
            raise SchoolMigrationError(
                "Portfolio binding differs from running owner configuration"
            )
    return result


def _derived(envelope):
    return to_batch(
        batch_id=envelope["batch_id"],
        origin=envelope["origin"],
        consumer=envelope["consumer"],
        bindings=envelope["bindings"],
        source=envelope["source"],
        catalog=envelope["catalog"],
        catalog_stamp=envelope["catalog_stamp"],
    )


def _check_source(envelope, raw, command):
    original = envelope["source"]
    if command != "reverse" or envelope["origin"] != "swissjob":
        if digest(raw) != digest(original):
            raise SchoolMigrationError(
                "local school data changed after the source freeze"
            )
        return
    # The Swiss public catalogue keeps harvesting after the school-state flip.
    # Rollback owns only the school status/draft and preference fields. Never
    # mistake fresh public job metadata for an unauthorized durable writer.
    for table, key in (("match_results", "id"), ("user_profiles", "user_id")):
        expected = {str(row[key]): row for row in original[table]}
        current = {str(row[key]): row for row in raw[table]}
        if any(
            identity not in current or digest(row) != digest(current[identity])
            for identity, row in expected.items()
        ):
            raise SchoolMigrationError(
                "local school durable changed after the source freeze"
            )
        extras = [row for identity, row in current.items() if identity not in expected]
        if any(
            table != "match_results"
            or row["application_status"] != "detected"
            or row["draft_letter"]
            for row in extras
        ):
            raise SchoolMigrationError(
                "unexpected local school durable after the source freeze"
            )


async def run(args):
    if args.command == "snapshot":
        envelope = {
            "batch_id": str(uuid.uuid4()),
            "origin": args.origin,
            "consumer": args.consumer,
            "bindings": _bindings(args.origin, private_read(args.bindings)),
            "catalog": private_read(args.catalog) if args.catalog else None,
            "catalog_stamp": datetime.now(timezone.utc),
        }
    else:
        envelope = private_read(args.bundle)
        seal = envelope.pop("seal", None)
        if not seal or digest(envelope) != seal:
            raise SchoolMigrationError("school source seal mismatch")
        _bindings(envelope["origin"], envelope["bindings"])
        if digest(_derived(envelope)) != digest(envelope["prepared"]):
            raise SchoolMigrationError("sealed source/target derivation mismatch")
        if args.command == "reverse":
            receipt = private_read(args.import_report)
            if (
                receipt.get("verdict") != "verified"
                or receipt.get("batch_id") != envelope["batch_id"]
                or receipt.get("consumer") != envelope["consumer"]
                or receipt.get("source_sha256") != digest(_derived(envelope))
            ):
                raise SchoolMigrationError(
                    "verified receipt does not belong to this batch"
                )
    origin, bindings = envelope["origin"], envelope["bindings"]
    await require_freeze(origin)
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
    core_engine = None
    try:
        async with async_sessionmaker(source_engine)() as source:
            async with source.begin():
                tables, owners = await lock_source(
                    source,
                    origin,
                    bindings,
                    authority="core" if args.command == "reverse" else "local",
                )
                raw = await read_source(
                    source, tables, origin, owners, envelope["catalog"]
                )
                if args.command == "snapshot":
                    envelope["source"] = raw
                    envelope["prepared"] = _derived(envelope)
                    envelope["seal"] = digest(envelope)
                    await require_freeze(origin)
                    _write(args.bundle, envelope)
                    return {
                        "verdict": "sealed",
                        "seal": envelope["seal"],
                        "rows": {name: len(rows) for name, rows in raw.items()},
                    }
                _check_source(envelope, raw, args.command)
                core_engine = create_core_engine(poolclass=NullPool)
                async with async_sessionmaker(core_engine)() as core:
                    async with core.begin():
                        await core.execute(sa.text("SET LOCAL lock_timeout='5s'"))
                        await core.execute(
                            sa.text("SET LOCAL statement_timeout='120s'")
                        )
                        if args.command == "import":
                            result = await import_batch(core, _derived(envelope))
                        else:
                            for pid in sorted(bindings.values()):
                                owner = await core.scalar(
                                    sa.text(
                                        "SELECT c.name FROM profiles p JOIN consumers c "
                                        "ON c.id=p.consumer_id WHERE p.id=:pid FOR UPDATE OF p"
                                    ),
                                    {"pid": uuid.UUID(pid)},
                                )
                                if owner != envelope["consumer"]:
                                    raise SchoolMigrationError(
                                        "core profile ownership mismatch"
                                    )
                            await core.execute(
                                sa.text(
                                    "LOCK TABLE school_monitors,school_job_details,"
                                    "school_applications,school_profile_preferences IN SHARE ROW EXCLUSIVE MODE"
                                )
                            )
                            current = await snapshot(core, envelope["consumer"])
                            result = await reverse_sync(
                                source,
                                tables,
                                origin,
                                bindings,
                                current,
                                original_monitors=_derived(envelope)["monitors"],
                            )
                        await require_freeze(origin)
                        if args.command == "reverse":
                            await (
                                source.commit()
                            )  # Keep the core snapshot locked until the local commit.
                _write(args.report, result)
                return {"verdict": result["verdict"], "batch_id": envelope["batch_id"]}
    finally:
        await source_engine.dispose()
        if core_engine is not None:
            await core_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    seal = commands.add_parser("snapshot")
    seal.add_argument("--origin", required=True, choices=("swissjob", "portfolio"))
    seal.add_argument("--consumer", required=True)
    seal.add_argument("--bindings", required=True)
    seal.add_argument("--catalog")
    seal.add_argument("--bundle", required=True)
    for operation in ("import", "reverse"):
        cmd = commands.add_parser(operation)
        cmd.add_argument("--bundle", required=True)
        cmd.add_argument("--report", required=True)
        if operation == "reverse":
            cmd.add_argument("--import-report", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args))))
    except SchoolMigrationError as exc:
        print(json.dumps({"verdict": "error", "reason": str(exc)}))
        raise SystemExit(1) from None
    except Exception as exc:
        # SQL/validation tracebacks can contain private drafts or credentials.
        print(json.dumps({"verdict": "error", "type": type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
