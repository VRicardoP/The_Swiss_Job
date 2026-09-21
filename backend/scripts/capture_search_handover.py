"""Export a frozen search snapshot privately; no alerts or commits to user data.

Run with the BFF environment AFTER draining old executors and corpus writers.
The output belongs on the NAS in a private directory. It is input to the core
handover, not permission to flip routing. Never print the bundle on a terminal.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid


def seal_snapshot(snapshot):
    def convert(value):
        if isinstance(value, datetime) and value.tzinfo is not None:
            return value.astimezone(timezone.utc).isoformat()
        if isinstance(value, uuid.UUID):
            return str(value)
        raise TypeError("unsupported snapshot value")

    content = json.dumps(snapshot, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False, default=convert)
    wire = json.loads(content)
    wire["seal"] = hashlib.sha256(content.encode()).hexdigest()
    return wire


def private_write(path, snapshot):
    path = Path(path)
    info = path.parent.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("private owned output directory required")
    with os.fdopen(os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600), "w") as out:
        json.dump(snapshot, out, ensure_ascii=False, allow_nan=False, sort_keys=True)
        out.flush()
        os.fsync(out.fileno())
    directory = os.open(path.parent, os.O_RDONLY|os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


async def run(path):
    import redis.asyncio as redis
    from sqlalchemy import text
    from config import settings
    from database import task_session
    from services.search_handover import capture

    markers = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
    try:
        async with task_session() as db:
            run_id = (await markers.info("server"))["run_id"]
            snapshot = await capture(db, markers, settings)
            snapshot["source_database"] = await db.scalar(text("SELECT current_database()"))
            if (await markers.info("server"))["run_id"] != run_id:
                raise ValueError("Redis restarted during capture")
            snapshot["redis_run_id"] = run_id
            snapshot["redis_db"] = markers.connection_pool.connection_kwargs.get("db", 0)
            snapshot = seal_snapshot(snapshot)
            private_write(path, snapshot)
            # table locks are released by session cleanup; no user data mutated.
            return {"seal": snapshot["seal"], "searches": len(snapshot["rows"]),
                    "pending": sum(not sent for values in snapshot["sent"].values() for sent in values.values()),
                    "written": True}
    finally:
        await markers.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    try:
        print(json.dumps(asyncio.run(run(parser.parse_args().out))))
    except Exception as exc:
        print(json.dumps({"verdict": "error", "type": type(exc).__name__, "written": False}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
