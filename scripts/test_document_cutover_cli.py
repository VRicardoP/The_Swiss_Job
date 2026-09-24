"""CLI rehearsal in the disposable PG database; no NAS or real personal data.

Run separately with -p jobhunt_core.tests.conftest and the scripts/core mounts.
"""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import sys
import threading
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core import documents
from jobhunt_core.import_documents import digest
from jobhunt_core.tests.conftest import _suite
from jobhunt_core.tests.test_integration_api_saved_searches import db, _seed_profile  # noqa: F401  (fixture de pytest: se importa para que la resuelva por nombre)
from jobhunt_core.tests.test_integration_document_source import source_tables
from jobhunt_core.tests.test_integration_import_documents import source
from jobhunt_core.document_cutover import private_read, private_write


@pytest.mark.parametrize("origin", ["swissjob", "portfolio"])
def test_cli_seal_import_replay_and_current_state_reverse(db, tmp_path, origin):  # noqa: F811  (la fixture, no una redefinición)
    assert _suite.get("dbname", "").startswith("jobhunt_suite_")
    factory, created = db
    _, consumer, pid = _seed_profile(factory, created)
    owner = uuid.uuid4() if origin == "swissjob" else 11
    row = source(origin, owner)
    meta, users, table, _ = source_tables("public", origin)
    admin_url = sa.engine.make_url(_suite["admin_url"]).set(database=_suite["dbname"])
    admin = sa.create_engine(admin_url, poolclass=sa.pool.NullPool)
    with admin.begin() as c:
        assert not sa.inspect(c).has_table("generated_documents", schema="public")
        meta.create_all(c)
        c.execute(users.insert().values(id=owner))
        if origin == "swissjob":
            c.execute(
                meta.tables["public.jobhunt_profile_map"]
                .insert()
                .values(user_id=owner, core_profile_id=pid)
            )
        c.execute(table.insert().values(**row))
    frozen = False

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            state = {"writes": "frozen" if frozen else "enabled"}
            payload = (
                state if origin == "swissjob" else {"checks": {"schedulers": state}}
            )
            encoded = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {
        **os.environ,
        "SOURCE_DATABASE_URL": admin_url.set(
            drivername="postgresql+asyncpg"
        ).render_as_string(hide_password=False),
        "DOCUMENT_FREEZE_URL": f"http://127.0.0.1:{server.server_port}/health",
        "SOURCE_DOCUMENT_OWNER_ID": str(owner),
        "SOURCE_CORE_PROFILE_ID": str(pid),
    }
    bindings, bundle = tmp_path / "bindings.json", tmp_path / "sealed.json"
    private_write(bindings, {str(owner): str(pid)})

    def command(*arguments, ok=True):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "jobhunt_core.document_cutover",
                *map(str, arguments),
            ],
            cwd="/app",
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert (result.returncode == 0) == ok, result.stdout + result.stderr
        assert row["content"] not in result.stdout + result.stderr
        return json.loads(result.stdout)

    try:
        snapshot = (
            "snapshot",
            "--origin",
            origin,
            "--consumer",
            consumer,
            "--bindings",
            bindings,
            "--bundle",
            bundle,
        )
        command(*snapshot, ok=False)
        assert not bundle.exists()
        frozen = True
        command(*snapshot)
        assert bundle.stat().st_mode & 0o077 == 0
        with admin.begin() as c:
            c.execute(table.update().values(content="changed after seal"))
        command(
            "import",
            "--bundle",
            bundle,
            "--report",
            tmp_path / "must_not_exist.json",
            ok=False,
        )
        with admin.begin() as c:
            c.execute(table.update().values(content=row["content"]))
        command("import", "--bundle", bundle, "--report", tmp_path / "import.json")
        command("import", "--bundle", bundle, "--report", tmp_path / "replay.json")
        assert private_read(tmp_path / "replay.json")["inserted"] == []

        async def new_write():
            async with factory() as s:
                old = (
                    (
                        await s.execute(
                            sa.text("SELECT * FROM generated_documents WHERE id=:id"),
                            {"id": row["id"]},
                        )
                    )
                    .mappings()
                    .one()
                )
                await documents.delete(s, pid, row["id"], consumer)
                values = {
                    key: old[key]
                    for key in (
                        "offer_revision_id",
                        "doc_type",
                        "content",
                        "language",
                        "source_ref",
                        "context",
                        "model_used",
                        "generation_time_ms",
                    )
                }
                values["context"] = dict(values["context"])
                values["context"].pop("_migration")
                values["content"] = "New after real CLI import"
                new_id = await documents.create(s, pid, values, consumer)
                await s.commit()
                return new_id

        with admin.begin() as c:
            c.execute(
                meta.tables["public.jobhunt_routing"]
                .insert()
                .values(
                    consumer_id=origin,
                    profile_id=owner if origin == "swissjob" else pid,
                    capability="documents",
                    mode="core_primary",
                )
            )
        command(
            "import",
            "--bundle",
            bundle,
            "--report",
            tmp_path / "forbidden-reimport.json",
            ok=False,
        )
        new_id = asyncio.run(new_write())
        invalid = private_read(tmp_path / "import.json")
        invalid["documents"][0]["target_sha256"] = "0" * 64
        private_write(tmp_path / "invalid-import.json", invalid)
        command(
            "reverse",
            "--bundle",
            bundle,
            "--report",
            tmp_path / "must-not-reverse.json",
            "--import-report",
            tmp_path / "invalid-import.json",
            ok=False,
        )
        with admin.connect() as c:
            assert digest(
                [dict(c.execute(sa.select(table)).mappings().one())]
            ) == digest([row])
        command(
            "reverse",
            "--bundle",
            bundle,
            "--report",
            tmp_path / "reverse.json",
            "--import-report",
            tmp_path / "import.json",
        )
        with admin.connect() as c:
            actual = dict(c.execute(sa.select(table)).mappings().one())
        assert (
            actual["id"] == new_id and actual["content"] == "New after real CLI import"
        )
        report = private_read(tmp_path / "reverse.json")
        assert report["removed_local"] == 1 and report["sha256"] == digest([actual])
        command(
            "reverse",
            "--bundle",
            bundle,
            "--report",
            tmp_path / "reverse-replay.json",
            "--import-report",
            tmp_path / "import.json",
        )
        assert private_read(tmp_path / "reverse-replay.json")["removed_local"] == 0
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        with admin.begin() as c:
            meta.drop_all(c)
        admin.dispose()
