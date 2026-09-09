"""Synthetic HTTP receiver for test_document_inbox_contract.py; never a live tool."""

import asyncio
import os
import socket
import sys
import uuid

sys.path.append(os.environ["DOCUMENT_TEST_EXTRA_PACKAGES"])
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from fastapi import FastAPI
import uvicorn

from config import settings
from database import Base
from models.user import User
from models.integration_inbox import IntegrationInbox
from routers import integration_inbox

url = os.environ["DOCUMENT_JOURNAL_DSN"]
schema = os.environ["DOCUMENT_JOURNAL_SCHEMA"]
assert sa.engine.make_url(url).database.startswith("jobhunt_suite_")
assert schema.startswith("document_inbox_") and schema.replace("_", "").isalnum()
engine = create_async_engine(
    url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}}
)
sessions = async_sessionmaker(engine, expire_on_commit=False)
pid = uuid.UUID(os.environ["DOCUMENT_TEST_PROFILE"])
swiss = os.environ["DOCUMENT_TEST_CLIENT"] == "swissjob"
uid = uuid.UUID(os.environ["DOCUMENT_TEST_USER"]) if swiss else 123
settings.CORE_INBOX_TOKEN = "synthetic-inbox-contract-only"
tables = [User.__table__, IntegrationInbox.__table__]
if swiss:
    from models.jobhunt_profile_map import JobhuntProfileMap

    tables.append(JobhuntProfileMap.__table__)
else:
    settings.CORE_PROFILE_ID = str(pid)
    settings.CORE_DOCUMENT_OWNER_USER_ID = uid
    settings.CORE_INBOX_CONSUMER = "portfolio"


async def initialize():
    async with engine.begin() as c:
        await c.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        await c.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    async with sessions() as s:
        if await s.get(User, uid) is None:
            values = {
                "id": uid,
                "email": "synthetic-inbox@example.invalid",
                "hashed_password": "synthetic",
            }
            if not swiss:
                values["username"] = "synthetic-inbox"
            s.add(User(**values))
            await s.flush()
            if swiss:
                s.add(JobhuntProfileMap(user_id=uid, core_profile_id=pid))
            await s.commit()


async def get_db():
    async with sessions() as s:
        yield s


asyncio.run(initialize())
app = FastAPI()
if swiss:
    app.dependency_overrides[integration_inbox.get_db] = get_db
    app.include_router(integration_inbox.router)
else:
    app.dependency_overrides[integration_inbox.get_async_db] = get_db
    app.include_router(integration_inbox.router, prefix="/api/v1/integration/inbox")
sock = socket.socket(fileno=int(os.environ["DOCUMENT_SERVER_FD"]))
server = uvicorn.Server(
    uvicorn.Config(app, lifespan="off", log_level="error", access_log=False)
)
server.run(sockets=[sock])
