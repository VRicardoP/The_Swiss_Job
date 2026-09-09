"""Frozen document cutover tools. Run with `python -m jobhunt_core.document_cutover`.

Credentials come ONLY from SOURCE_DATABASE_URL and CORE_DATABASE_URL. Set
DOCUMENT_FREEZE_URL to the BFF's /health/documents (SwissJob) or /health/deep
(Portfolio). Input/output files must live in an existing private directory.

snapshot: seal history and explicit owner→core-profile bindings BEFORE import.
import: recheck source under locks, import atomically, persist metadata receipt.
reverse: replace the owned local collection with the frozen current core state.
Neither command changes routing, releases freeze, or deletes shared core data.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import stat
import uuid

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from jobhunt_core.database import create_core_engine
from jobhunt_core.document_source import (
    lock_source,
    reverse_sync,
    source_rows,
    verify_source,
)
from jobhunt_core.import_documents import (
    DocumentMigrationError,
    _canonical,
    digest,
    import_batch,
    prepare_batch,
)


def private_read(path):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "r") as file:
        info = os.fstat(file.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_uid != os.getuid()
        ):
            raise DocumentMigrationError(
                "input file must be private and owned by this user"
            )
        return json.load(file)


def private_write(path, value):
    path = Path(path)
    info = path.parent.stat()
    if info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise DocumentMigrationError(
            "output directory must be private and owned by this user"
        )
    content = _canonical(value).encode()
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb"
    ) as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


async def require_freeze(origin):
    url = os.environ["DOCUMENT_FREEZE_URL"]
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
    state = (
        payload
        if origin == "swissjob"
        else payload.get("checks", {}).get("schedulers", {})
    )
    if state.get("writes") != "frozen":
        raise DocumentMigrationError("BFF document writer is not frozen")


async def run(args):
    if args.command == "snapshot":
        bindings = private_read(args.bindings)
        batch = {"origin": args.origin, "consumer": args.consumer, "bindings": bindings}
    else:
        batch = private_read(args.bundle)
    if batch["origin"] == "portfolio":
        expected = {
            str(int(os.environ["SOURCE_DOCUMENT_OWNER_ID"])): str(
                uuid.UUID(os.environ["SOURCE_CORE_PROFILE_ID"])
            )
        }
        if {str(k): str(v) for k, v in batch["bindings"].items()} != expected:
            raise DocumentMigrationError(
                "bindings differ from the running Portfolio configuration"
            )
    if args.command == "reverse":
        receipt = private_read(args.import_report)
        expected_rows = [
            {
                "id": str(item["target"]["id"]),
                "profile_id": str(item["target"]["profile_id"]),
                "source_sha256": item["source_sha256"],
                "target_sha256": digest(item["target"]),
            }
            for item in batch["documents"]
        ]
        if (
            receipt.get("verdict") != "verified"
            or receipt.get("seal") != batch["seal"]
            or receipt.get("batch_id") != batch["batch_id"]
            or receipt.get("documents") != expected_rows
        ):
            raise DocumentMigrationError(
                "verified import receipt does not match this sealed batch"
            )
    await require_freeze(batch["origin"])
    source_engine = create_async_engine(
        os.environ["SOURCE_DATABASE_URL"],
        poolclass=NullPool,
        connect_args={
            "server_settings": {"search_path": "public", "statement_timeout": "60000"}
        },
    )
    core_engine = None
    try:
        async with async_sessionmaker(source_engine)() as source:
            async with source.begin():
                if args.command == "snapshot":
                    table, owners = await lock_source(
                        source, origin=args.origin, bindings=bindings, authority="local"
                    )
                    batch = prepare_batch(
                        batch_id=uuid.uuid4(),
                        origin=args.origin,
                        consumer=args.consumer,
                        bindings=bindings,
                        rows=await source_rows(source, table, owners),
                    )
                    private_write(args.bundle, batch)
                    return {
                        "verdict": "sealed",
                        "documents": len(batch["documents"]),
                        "seal": batch["seal"],
                    }
                # Preserve the same cross-database acquisition order in both directions.
                if args.command == "import":
                    await verify_source(source, batch)
                else:
                    await lock_source(
                        source,
                        origin=batch["origin"],
                        bindings=batch["bindings"],
                        authority="core",
                    )
                core_engine = create_core_engine(poolclass=NullPool)
                async with async_sessionmaker(core_engine)() as core:
                    async with core.begin():
                        await core.execute(sa.text("SET LOCAL lock_timeout='5s'"))
                        await core.execute(sa.text("SET LOCAL statement_timeout='60s'"))
                        if args.command == "import":
                            result = await import_batch(core, batch)
                        else:
                            # Revalidate seal/derivation without reimporting deleted history.
                            checked = prepare_batch(
                                batch_id=batch["batch_id"],
                                origin=batch["origin"],
                                consumer=batch["consumer"],
                                bindings=batch["bindings"],
                                rows=[item["source"] for item in batch["documents"]],
                            )
                            if checked["seal"] != batch["seal"]:
                                raise DocumentMigrationError("batch seal mismatch")
                            rows = []
                            for pid in sorted(
                                set(checked["bindings"].values()), key=str
                            ):
                                owner = await core.scalar(
                                    sa.text(
                                        "SELECT c.name FROM profiles p "
                                        "JOIN consumers c ON c.id=p.consumer_id WHERE p.id=:pid FOR UPDATE OF p"
                                    ),
                                    {"pid": pid},
                                )
                                if owner != batch["consumer"]:
                                    raise DocumentMigrationError(
                                        "core profile ownership mismatch"
                                    )
                                rows.extend(
                                    (
                                        await core.execute(
                                            sa.text(
                                                "SELECT * FROM generated_documents WHERE profile_id=:pid ORDER BY id"
                                            ),
                                            {"pid": pid},
                                        )
                                    )
                                    .mappings()
                                    .all()
                                )
                            result = await reverse_sync(
                                source,
                                rows,
                                origin=batch["origin"],
                                bindings=batch["bindings"],
                            )
                        await require_freeze(batch["origin"])
                        if args.command == "reverse":
                            # Commit local data while the core snapshot is still root-locked.
                            await source.commit()
            private_write(args.report, result)
            return {
                key: value
                for key, value in result.items()
                if key in ("verdict", "seal", "sha256")
            }
    finally:
        await source_engine.dispose()
        if core_engine is not None:
            await core_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--origin", choices=("swissjob", "portfolio"), required=True)
    snapshot.add_argument("--consumer", required=True)
    snapshot.add_argument("--bindings", required=True)
    snapshot.add_argument("--bundle", required=True)
    for command in ("import", "reverse"):
        child = sub.add_parser(command)
        child.add_argument("--bundle", required=True)
        child.add_argument("--report", required=True)
        if command == "reverse":
            child.add_argument("--import-report", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args))))
    except Exception as exc:
        # SQLAlchemy/httpx exception strings can contain credentials or CV data.
        print(
            json.dumps(
                {
                    "verdict": "failed",
                    "error_type": type(exc).__name__,
                    "action": "keep freeze; inspect privately; retry only the same sealed batch",
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
