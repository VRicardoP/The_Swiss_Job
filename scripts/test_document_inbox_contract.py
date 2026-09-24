"""Real core event → production HTTP transport → isolated BFF/PG inbox, twice.

Uses the same mounts/disposable-database plugin as test_document_adapter_contract.
All identities/content are synthetic; no live environment files or background tasks.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

import httpx
import pytest
import sqlalchemy as sa

from jobhunt_core import documents, delivery
from jobhunt_core.http_delivery import HttpDestinationTransport
from jobhunt_core.tests.test_integration_api_saved_searches import db, _seed_profile  # noqa: F401  (fixture de pytest: se importa para que la resuelva por nombre)
from jobhunt_core.tests.conftest import _suite


@pytest.mark.parametrize(
    "name,root,consumer",
    [("swissjob", "/bff", "swissjob-shadow"), ("portfolio", "/portfolio", "portfolio")],
)
def test_inbox_concurrent_delivery_and_process_restart(db, name, root, consumer):  # noqa: F811  (la fixture, no una redefinición)
    assert _suite.get("dbname", "").startswith("jobhunt_suite_")
    factory, created = db
    _, original_consumer, pid = _seed_profile(factory, created)
    schema = "document_inbox_" + uuid.uuid4().hex
    admin_url = sa.engine.make_url(_suite["admin_url"]).set(database=_suite["dbname"])
    admin = sa.create_engine(admin_url, poolclass=sa.pool.NullPool)

    async def make_events():
        async with factory() as s:
            await s.execute(
                sa.text("UPDATE consumers SET name=:new WHERE name=:old"),
                {"new": consumer, "old": original_consumer},
            )
            values = {
                "offer_revision_id": None,
                "doc_type": "cv",
                "content": "Synthetic private document",
                "language": "en",
                "source_ref": "a" * 32,
                "context": {},
                "model_used": None,
                "generation_time_ms": None,
            }
            did = await documents.create(s, pid, values, consumer)
            await documents.delete(s, pid, did, consumer)
            await s.commit()
            rows = (
                await s.execute(
                    sa.text(
                        "SELECT * FROM integration_outbox WHERE subject_profile_id=:pid ORDER BY version"
                    ),
                    {"pid": pid},
                )
            ).all()
            return [delivery.event_dict(row) for row in rows]

    events = asyncio.run(make_events())
    assert len(events) == 2 and [e["version"] for e in events] == [1, 2]
    assert all("content" not in e["payload"] for e in events)
    events.append(
        {
            "event_id": str(uuid.uuid4()),
            "type": "match.evaluated",
            "aggregate": "match_evaluation",
            "aggregate_id": "a" * 64,
            "subject_profile_id": str(pid),
            "version": 1,
            "payload": {
                "eval_key": "a" * 64,
                "profile_id": str(pid),
                "vacancy_id": str(uuid.uuid4()),
            },
        }
    )
    env = {
        **os.environ,
        "PYTHONPATH": root,
        "DOCUMENT_TEST_CLIENT": name,
        "DOCUMENT_TEST_PROFILE": str(pid),
        "DOCUMENT_TEST_USER": str(uuid.uuid4()),
        "DOCUMENT_JOURNAL_SCHEMA": schema,
        "DOCUMENT_JOURNAL_DSN": admin_url.set(
            drivername="postgresql+asyncpg"
        ).render_as_string(hide_password=False),
        "CORE_CONSUMER_KEY": "synthetic-only",
        "CORE_INBOX_TOKEN": "synthetic-inbox-contract-only",
        "ADMIN_EMAIL": "inbox@example.invalid",
        "ADMIN_PASSWORD": "synthetic-only",
        "DATABASE_URL": "postgresql+asyncpg://test:test@127.0.0.1:1/unused",
        "DATABASE_URL_ASYNC": "postgresql+asyncpg://test:test@127.0.0.1:1/unused",
    }
    code = Path(__file__).with_name("document_inbox_contract_server.py").read_text()
    try:
        for restart in range(2):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            base = f"http://127.0.0.1:{sock.getsockname()[1]}"
            env["DOCUMENT_SERVER_FD"] = str(sock.fileno())
            process = subprocess.Popen(
                [sys.executable, "-c", code],
                cwd="/tmp",
                env=env,
                pass_fds=(sock.fileno(),),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                with httpx.Client(timeout=5) as client:
                    deadline = time.monotonic() + 15
                    ready = False
                    while process.poll() is None and time.monotonic() < deadline:
                        try:
                            ready = client.get(base + "/probe").status_code == 404
                            if ready:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(0.02)
                    assert ready, "synthetic BFF receiver did not start"
                    url = base + (
                        "/api/v1/integration/events"
                        if name == "swissjob"
                        else "/api/v1/integration/inbox"
                    )
                    transport = HttpDestinationTransport(
                        {consumer: url},
                        "synthetic-inbox-contract-only",
                        5,
                        client=client,
                    )
                    # Concurrent duplicates plus a fresh process model lost ACK/restart.
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        list(
                            pool.map(
                                lambda event: transport(consumer, event), events * 8
                            )
                        )
                    changed = copy.deepcopy(events[0])
                    changed["aggregate_id"] = uuid.uuid4()
                    changed["payload"]["document_id"] = str(changed["aggregate_id"])
                    with pytest.raises(httpx.HTTPStatusError) as error:
                        transport(consumer, changed)
                    assert error.value.response.status_code == 409
                    assert client.post(url, content=b"invalid").status_code == 401
                with admin.connect() as c:
                    count = c.execute(
                        sa.text(f'SELECT count(*) FROM "{schema}".integration_inbox')
                    ).scalar_one()
                    assert count == 3, (restart, count)
            finally:
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=5)
                sock.close()
                assert process.returncode in (0, -15), (stdout + stderr).replace(
                    env["DOCUMENT_JOURNAL_DSN"], "<test-db>"
                )
    finally:
        with admin.begin() as c:
            c.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
