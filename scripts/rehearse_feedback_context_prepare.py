"""Temporary read credential for the existing isolated NAS feedback rehearsal."""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa

from jobhunt_core import credentials
from jobhunt_core.database import SessionLocal, engine
from jobhunt_core.document_cutover import private_read, private_write

ROOT = Path("/evidence")


async def main():
    assert (
        os.environ["CORE_DATABASE_URL"]
        == "postgresql+asyncpg://jobhunt_core@127.0.0.1:5432/core_copy"
    )
    target = ROOT / "feedback-context.private.json"
    async with SessionLocal() as session:
        if sys.argv[1:] == ["revoke"]:
            state = private_read(target)
            await credentials.revoke_credential(
                session, state["env"]["CORE_CONSUMER_KEY"].split(".")[0]
            )
            await session.commit()
            print("copy-only context credential revoked")
            return
        assert not target.exists(), "already prepared"
        state = private_read(ROOT / "feedback-recovery.private.json")
        assert (
            state["env"]["DATABASE_URL"]
            == "postgresql+asyncpg://swissjob@127.0.0.1:5432/source_copy"
        )
        cid = await session.scalar(
            sa.text("SELECT id FROM consumers WHERE name='swissjob-shadow' AND active")
        )
        assert cid is not None
        key, secret = await credentials.create_credential(
            session,
            cid,
            ["matches:read"],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        )
        state["env"]["CORE_CONSUMER_KEY"] = key + "." + secret
        private_write(target, state)
        await session.commit()
        print(
            json.dumps({"copy_context_credential": True, "production_changed": False})
        )


async def run():
    try:
        await main()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(run())
    except Exception as exc:
        print(json.dumps({"verified": False, "type": type(exc).__name__}))
        raise SystemExit(1) from None
