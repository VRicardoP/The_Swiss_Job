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


@pytest.mark.parametrize("client_name,root", [("swissjob", "/bff"), ("portfolio", "/portfolio")])
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
        result = subprocess.run([sys.executable, "-c", _CLIENT], cwd="/tmp", env=env,
                                text=True, capture_output=True, timeout=60)
        diagnostic = (result.stdout + result.stderr).replace(token, "<redacted>").replace(other_token, "<redacted>")
        assert result.returncode == 0, diagnostic
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        app.dependency_overrides.pop(deps.get_session, None)
    assert not thread.is_alive(), "test HTTP server did not stop"
