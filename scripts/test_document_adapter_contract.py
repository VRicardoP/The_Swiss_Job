"""E.2 opt-in wire test: both BFF clients against real API/auth/PG, not HTTP mocks.

Run in core-migrate with -p jobhunt_core.tests.conftest (disposable DB) and mount
SwissJob/backend at /bff and ReactPortfolio/backend at /portfolio, read-only.
No production routing or credentials. Clients run in isolated Python processes;
neither BFF imports jobhunt_core. Generation, PDF and cutover are NOT certified.
"""
import os
import socket
import subprocess
import sys
import threading
import time

import pytest
import uvicorn

from jobhunt_core.tests.test_integration_api_saved_searches import db, _seed_profile


_CLIENT = r'''
import asyncio, os, sys, uuid
# Supplement missing BFF-only dependencies from an existing venv, no installs.
if os.environ.get("DOCUMENT_TEST_EXTRA_PACKAGES"):
    sys.path.append(os.environ["DOCUMENT_TEST_EXTRA_PACKAGES"])
from config import settings

pid = uuid.UUID(os.environ["DOCUMENT_TEST_PROFILE"])
app_id = uuid.uuid4()
operation = uuid.uuid4()
content = '{"name":"Synthetic résumé", "body":"Exact UTF-8"}'

if os.environ["DOCUMENT_TEST_CLIENT"] == "swissjob":
    from services.documents import core_client as module
    from services.documents.port import CoreUnavailableError as ExpectedError
    async def profile(db, user_id):
        return pid
    module.resolve_core_profile_id = profile
    core = module.CoreDocuments(object())
    owner = uuid.uuid4()
    ref = "e" * 32
    args = (owner, ref, "cv", content, "fr", "Engineer", "Example")
else:
    from services import documents_core as module
    from services.documents_core import CoreDocumentError as ExpectedError
    core = module.CoreDocuments(pid, 123)
    owner = 123
    ref = app_id
    args = (owner, ref, "cv", content, "fr", "synthetic-model", 21)

async def go():
    first = await core.create(*args, operation_id=operation)
    replay = await core.create(*args, operation_id=operation)
    assert first.id == replay.id and first.content == content
    # A new operation with identical content is a NEW generation, not a replay.
    second = await core.create(*args, operation_id=uuid.uuid4())
    assert first.id != second.id
    # Exercise the real keyset cursor, not merely one page.
    for _ in range(19):
        await core.create(*args, operation_id=uuid.uuid4())
    listed = await core.list(owner, ref)
    items = listed.data if hasattr(listed, "data") else listed
    assert len(items) == 21 and len({d.id for d in items}) == 21
    assert all(d.content == content for d in items)
    assert await core.delete(owner, first.id)
    assert not await core.delete(owner, first.id)
    try:
        await core.create(*args, operation_id=operation)
    except ExpectedError:
        pass  # deleted receipt must not resurrect the CV
    else:
        raise AssertionError("deleted generation replayed")
    if os.environ["DOCUMENT_TEST_CLIENT"] == "portfolio":
        batch_key = uuid.uuid4()
        batch_ref = uuid.uuid4()
        finished = [{"doc_type": kind, "content": content, "language": "fr",
                     "model_used": "synthetic-model", "generation_time_ms": 21}
                    for kind in ("cv", "cover_letter")]
        pair = await core.create_batch(owner, batch_ref, finished, operation_id=batch_key)
        replay = await core.create_batch(owner, batch_ref, finished, operation_id=batch_key)
        assert [d.id for d in pair] == [d.id for d in replay]
        assert [d.doc_type for d in pair] == ["cv", "cover_letter"]
        assert len(await core.list(owner, batch_ref)) == 2
        assert await core.delete(owner, pair[0].id)
        try:
            await core.create_batch(owner, batch_ref, finished, operation_id=batch_key)
        except ExpectedError:
            pass
        else:
            raise AssertionError("partial generation replayed or resurrected")
        remaining = await core.list(owner, batch_ref)
        assert [d.id for d in remaining] == [pair[1].id]
    # Correctly scoped auth is real; a foreign consumer must not see this profile.
    settings.CORE_CONSUMER_KEY = os.environ["DOCUMENT_TEST_OTHER_TOKEN"]
    try:
        await core.list(owner, ref)
    except ExpectedError:
        pass
    else:
        raise AssertionError("cross-consumer disclosure")

asyncio.run(go())
print("create/replay/new-generation/pagination/ETag-delete/ownership: passed")
'''



_JOURNAL_CLIENT = r'''
import asyncio, os, sys, uuid
sys.path.append(os.environ["DOCUMENT_TEST_EXTRA_PACKAGES"])
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.engine import make_url
from config import settings
from database import Base
from models.user import User
from models.document_delivery import DocumentDelivery
from services.documents_core import CoreDocuments, CoreDocumentError
from services.document_delivery import enqueue_generation, deliver_generation

url = os.environ["DOCUMENT_JOURNAL_DSN"]
assert make_url(url).database == os.environ["DOCUMENT_JOURNAL_DATABASE"]
assert make_url(url).database.startswith("jobhunt_suite_")
schema = os.environ["DOCUMENT_JOURNAL_SCHEMA"]
assert schema.startswith("document_delivery_") and schema.replace("_", "").isalnum()
engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
sessions = async_sessionmaker(engine, expire_on_commit=False)
pid = uuid.UUID(os.environ["DOCUMENT_TEST_PROFILE"])
operation = uuid.UUID(os.environ["DOCUMENT_JOURNAL_OPERATION"])
app_id = uuid.UUID(os.environ["DOCUMENT_JOURNAL_APPLICATION"])
settings.CORE_PROFILE_ID = str(pid)
settings.CORE_DOCUMENT_OWNER_USER_ID = 123
snapshot = {"origin": "core", "title": "Synthetic", "description": "Exact UTF-8 é"}
finished = [{"doc_type": kind, "content": '{"name":"Synthetic"}', "language": "en",
             "model_used": "synthetic", "generation_time_ms": 12}
            for kind in ("cv", "cover_letter")]

async def go():
    try:
        if os.environ["DOCUMENT_JOURNAL_PHASE"] == "prepare":
            async with engine.begin() as c:
                await c.execute(text(f'CREATE SCHEMA "{schema}"'))
                await c.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
                    User.__table__, DocumentDelivery.__table__]))
            async with sessions() as db:
                db.add(User(id=123, username="synthetic", hashed_password="synthetic"))
                await db.flush()
                await enqueue_generation(db, operation_id=operation, user_id=123, profile_id=pid,
                    application_id=app_id, request_hash="a"*64, documents=finished,
                    application_snapshot=snapshot)
                await db.commit()
            class LoseAck(CoreDocuments):
                async def create_batch(self, *args, **kwargs):
                    await super().create_batch(*args, **kwargs)  # real core COMMIT
                    raise CoreDocumentError("synthetic lost ACK")
            result = await deliver_generation(sessions, operation, 123, sender_factory=LoseAck)
            assert result["status"] == "pending"
            async with sessions() as db:
                row = await db.get(DocumentDelivery, operation)
                assert row.documents == finished and row.document_ids is None
                assert row.first_attempt_at is not None
        else:
            if os.environ["DOCUMENT_JOURNAL_PHASE"] == "verify":
                def forbidden(*args): raise AssertionError("confirmed operation emitted HTTP")
                result = await deliver_generation(sessions, operation, 123, sender_factory=forbidden)
            else:
                result = await deliver_generation(sessions, operation, 123)
            assert result["status"] == "delivered", result
            core = CoreDocuments(pid, 123)
            docs = await core.list(123, app_id)
            assert len(docs) == 2
            assert set(map(str, (d.id for d in docs))) == set(result["document_ids"])
            assert all(d.application_snapshot == snapshot for d in docs)
            async with sessions() as db:
                row = await db.get(DocumentDelivery, operation)
                assert row.documents is None and row.application_snapshot is None
                assert row.last_error is None
    finally:
        await engine.dispose()

asyncio.run(go())
print("journal phase passed:", os.environ["DOCUMENT_JOURNAL_PHASE"])
'''



_WORKFLOW_CLIENT = r'''
import asyncio, os, sys, uuid
from types import SimpleNamespace
sys.path.append(os.environ["DOCUMENT_TEST_EXTRA_PACKAGES"])
import httpx
from fastapi import FastAPI
from sqlalchemy import select, text, delete
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from config import settings
from database import Base, get_async_db
from models.user import User
from models.job_application import JobApplication
from models.generated_document import GeneratedDocument
from models.jobhunt_routing import JobhuntRouting, PROFILE_WILDCARD
from models.document_delivery import DocumentDelivery
from routers import cv_generation as router
from routers.auth import get_current_active_user
from services.cv_generation_service import get_cv_generation_service
from services.cv_profile_service import get_cv_profile_service
from services.documents_core import CoreDocuments, CoreDocumentError
from services import document_store as authority

url = os.environ["DOCUMENT_JOURNAL_DSN"]
assert make_url(url).database == os.environ["DOCUMENT_JOURNAL_DATABASE"]
assert make_url(url).database.startswith("jobhunt_suite_")
schema = os.environ["DOCUMENT_JOURNAL_SCHEMA"]
assert schema.startswith("document_delivery_") and schema.replace("_", "").isalnum()
engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
sessions = async_sessionmaker(engine, expire_on_commit=False)
pid = uuid.UUID(os.environ["DOCUMENT_TEST_PROFILE"])
settings.CORE_PROFILE_ID, settings.CORE_DOCUMENT_OWNER_USER_ID = str(pid), 123
settings.writes_frozen = False
router.limiter.enabled = False
app = FastAPI()
app.include_router(router.router, prefix="/api/v1/cv-generation")
async def db():
    async with sessions() as session:
        yield session
app.dependency_overrides[get_async_db] = db
app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(id=123)
class Profiles:
    async def get_cv_text(self, db): return "Synthetic profile"
    async def get_structured_data(self, db, language): return None
class LLM:
    calls = 0
    async def generate_cv(self, **kwargs):
        self.calls += 1
        return {"content": {"summary": "Synthetic résumé"}, "generation_time_ms": 2}
    async def generate_cover_letter(self, **kwargs):
        self.calls += 1
        return {"content": {"greeting": "Synthetic"}, "generation_time_ms": 2}
llm = LLM()
app.dependency_overrides[get_cv_generation_service] = lambda: llm
app.dependency_overrides[get_cv_profile_service] = Profiles

class LoseAck(CoreDocuments):
    first = True
    async def create_batch(self, *args, **kwargs):
        result = await super().create_batch(*args, **kwargs)
        if LoseAck.first:
            LoseAck.first = False
            raise CoreDocumentError("synthetic loss AFTER real commit")
        return result
authority.CoreDocuments = LoseAck

async def run():
    try:
        async with engine.begin() as c:
            await c.execute(text(f'CREATE SCHEMA "{schema}"'))
            await c.run_sync(lambda c: Base.metadata.create_all(c, tables=[
                User.__table__, JobApplication.__table__, GeneratedDocument.__table__,
                JobhuntRouting.__table__, DocumentDelivery.__table__,
            ]))
        async with sessions() as s:
            s.add(User(id=123, username="synthetic", hashed_password="synthetic"))
            await s.flush()
            application = JobApplication(user_id=123, title="Engineer", company="Synthetic")
            s.add(application)
            s.add(JobhuntRouting(consumer_id="portfolio", profile_id=PROFILE_WILDCARD,
                                capability="documents", mode="core_primary"))
            await s.commit()
            aid = application.id
        op = uuid.uuid4()
        body = {"application_id": str(aid), "operation_id": str(op)}
        base = "/api/v1/cv-generation"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://bff.test") as client:
            first = await client.post(base + "/generate", json=body)
            assert first.status_code == 202, first.text
            assert llm.calls == 2
            async with sessions() as s:
                row = await s.get(DocumentDelivery, op)
                assert row.documents is not None and row.delivered_at is None
                assert not (await s.execute(select(GeneratedDocument))).scalars().all()
                await s.execute(delete(JobApplication).where(JobApplication.id == aid))
                await s.commit()
            pending = await client.get(base + "/operations/")
            assert len(pending.json()) == 1
            second = await client.post(base + "/generate", json=body)
            assert second.status_code == 200, second.text
            assert llm.calls == 2
            listed = await client.get(base + "/")
            docs = listed.json()
            assert len(docs) == 2
            for doc in docs:
                assert doc["application_snapshot"]["title"] == "Engineer"
                got = await client.get(base + "/" + doc["id"])
                assert got.status_code == 200 and got.json()["content"] == doc["content"]
            third = await client.post(base + "/operations/" + str(op) + "/retry")
            assert third.status_code == 200 and llm.calls == 2
            assert (await client.get(base + "/operations/")).json() == []
            assert (await client.delete(base + "/" + docs[0]["id"])).status_code == 204
            assert (await client.post(base + "/generate", json=body)).status_code == 410
            assert len((await client.get(base + "/")).json()) == 1 and llm.calls == 2
        print("BFF HTTP + PostgreSQL + real core API: ACK loss/replay/delete/application deletion passed")
    finally:
        await engine.dispose()
asyncio.run(run())
'''

@pytest.mark.parametrize("client_name,root", [("swissjob", "/bff"), ("portfolio", "/portfolio"), ("portfolio_delivery", "/portfolio"), ("portfolio_workflow", "/portfolio")])
def test_document_adapter_over_http(db, client_name, root):
    assert os.path.isfile(root + "/config.py"), "mount the BFF source read-only"
    from sqlalchemy.engine import make_url
    from jobhunt_core.config import settings
    from jobhunt_core.tests.conftest import _suite
    # A forgotten -p must fail BEFORE creating even synthetic data in dev.
    assert _suite.get("dbname") and _suite["dbname"] == make_url(settings.CORE_DATABASE_URL).database, (
        "requires the disposable-database plugin: -p jobhunt_core.tests.conftest"
    )
    factory, created = db
    token, _, pid = _seed_profile(factory, created, ["documents:read", "documents:write"])
    other_token, _, _ = _seed_profile(factory, created, ["documents:read", "documents:write"])
    from jobhunt_core.api import deps
    from jobhunt_core.api.main import app

    async def session():
        async with factory() as s:
            yield s

    app.dependency_overrides[deps.get_session] = session
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    try:
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "test HTTP server did not start"
        env = {**os.environ, "PYTHONPATH": root, "DOCUMENT_TEST_CLIENT": client_name,
               "DOCUMENT_TEST_PROFILE": str(pid), "DOCUMENT_TEST_OTHER_TOKEN": other_token,
               "CORE_API_BASE_URL": f"http://127.0.0.1:{sock.getsockname()[1]}/v1",
               "CORE_CONSUMER_KEY": token, "REDIS_URL": "",
               "ADMIN_EMAIL": "adapter-test@example.com", "ADMIN_PASSWORD": "synthetic-only",
               "DATABASE_URL": "postgresql+asyncpg://test:test@127.0.0.1:1/unused",
               "DATABASE_URL_ASYNC": "postgresql+asyncpg://test:test@127.0.0.1:1/unused"}
        # Never load either project's private .env in this synthetic test.
        journal_url = make_url(_suite["admin_url"]).set(
            drivername="postgresql+asyncpg", database=_suite["dbname"],
        ).render_as_string(hide_password=False)
        schema = "document_delivery_" + __import__("uuid").uuid4().hex
        phases = ("prepare", "resume", "verify") if client_name == "portfolio_delivery" else ("client",)
        env.update(DOCUMENT_JOURNAL_DSN=journal_url, DOCUMENT_JOURNAL_DATABASE=_suite["dbname"],
                   DOCUMENT_JOURNAL_SCHEMA=schema, DOCUMENT_JOURNAL_OPERATION=str(__import__("uuid").uuid4()),
                   DOCUMENT_JOURNAL_APPLICATION=str(__import__("uuid").uuid4()))
        try:
            for phase in phases:
                env["DOCUMENT_JOURNAL_PHASE"] = phase
                result = subprocess.run(
                    [sys.executable, "-c", _WORKFLOW_CLIENT if client_name == "portfolio_workflow" else (_JOURNAL_CLIENT if phase != "client" else _CLIENT)],
                    cwd="/tmp", env=env, text=True, capture_output=True, timeout=60,
                )
                diagnostic = (result.stdout + result.stderr).replace(token, "<redacted>").replace(
                    other_token, "<redacted>").replace(journal_url, "<test-database>")
                assert result.returncode == 0, diagnostic
        finally:
            if client_name in ("portfolio_delivery", "portfolio_workflow"):
                import asyncio
                import sqlalchemy as sa
                from sqlalchemy.ext.asyncio import create_async_engine
                async def cleanup_journal():
                    engine = create_async_engine(journal_url)
                    try:
                        async with engine.begin() as c:
                            await c.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                    finally:
                        await engine.dispose()
                asyncio.run(cleanup_journal())
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        app.dependency_overrides.pop(deps.get_session, None)
    assert not thread.is_alive(), "test HTTP server did not stop"
