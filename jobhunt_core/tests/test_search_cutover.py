"""Operational input/freeze/source fences, with no external network or writes."""

import asyncio
from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock, MagicMock
import uuid

import httpx
import pytest

from jobhunt_core import search_cutover as ctl
from jobhunt_core.document_cutover import private_write
from jobhunt_core.import_schools import canonical, digest
from jobhunt_core.import_swissjob_searches import SearchMigrationError


def test_snapshot_seal_is_required_and_round_trips_typed_values(tmp_path):
    snapshot = {
        "version": 1,
        "rows": [{"id": uuid.uuid4(), "created_at": datetime.now(timezone.utc)}],
    }
    snapshot["seal"] = digest(snapshot)
    path = tmp_path / "snapshot.json"
    private_write(path, snapshot)
    assert ctl.read_snapshot(path) == json.loads(canonical(snapshot))
    snapshot["rows"].clear()
    bad = tmp_path / "tampered.json"
    private_write(bad, snapshot)
    with pytest.raises(SearchMigrationError, match="sealed"):
        ctl.read_snapshot(bad)


@pytest.mark.parametrize(
    "state,status,passes",
    [
        ({"writes": "frozen"}, 200, True),
        ({"writes": "enabled"}, 200, False),
        ({}, 200, False),
        ({"writes": "frozen"}, 503, False),
    ],
)
def test_freeze_is_live_and_fail_closed(monkeypatch, state, status, passes):
    client_type = httpx.AsyncClient
    monkeypatch.setenv("SEARCH_FREEZE_URL", "https://freeze.invalid/health/searches")
    monkeypatch.setattr(
        ctl.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, json=state)
            ),
        ),
    )
    if passes:
        asyncio.run(ctl.require_freeze())
    else:
        with pytest.raises((SearchMigrationError, httpx.HTTPError)):
            asyncio.run(ctl.require_freeze())


@pytest.mark.parametrize(
    "drift",
    [
        None,
        "database",
        "search",
        "bindings",
        "corpus",
        "redis",
        "redis_db",
        "marker",
        "authority",
    ],
)
def test_source_revalidation_rejects_every_material_drift(drift):
    uid, pid, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    row = {
        "id": sid,
        "user_id": uid,
        "created_at": datetime.now(timezone.utc),
        "filters": {"q": "Python"},
    }
    snapshot = {
        "source_database": "source_db",
        "rows": json.loads(canonical([row])),
        "bindings": {str(uid): str(pid)},
        "jobs_fingerprint": {"n": 2, "digest": "same"},
        "redis_run_id": "instance-one",
        "redis_db": 0,
        "sent": {str(sid): {"sent": True, "pending": False}},
    }
    db = AsyncMock()
    db.scalar.return_value = "other_db" if drift == "database" else "source_db"
    statements = []

    async def execute(statement, *args):
        sql = str(statement)
        statements.append(sql)
        result = MagicMock()
        if "SELECT mode" in sql:
            result.scalars.return_value = (
                ["core_primary"] if drift == "authority" else ["local"]
            )
        elif "SELECT * FROM public.saved_searches" in sql:
            result.mappings.return_value = [] if drift == "search" else [row]
        elif "SELECT user_id::text" in sql:
            result.all.return_value = (
                [] if drift == "bindings" else [(str(uid), str(pid))]
            )
        elif "AS digest FROM public.jobs" in sql:
            result.mappings.return_value.one.return_value = {
                "n": 2,
                "digest": "other" if drift == "corpus" else "same",
            }
        return result

    db.execute.side_effect = execute
    markers = AsyncMock()
    markers.connection_pool.connection_kwargs = {"db": 1 if drift == "redis_db" else 0}
    markers.info.return_value = {
        "run_id": "other" if drift == "redis" else "instance-one"
    }
    markers.mget.return_value = [None, None] if drift == "marker" else [b"1", None]
    if drift:
        with pytest.raises(SearchMigrationError):
            asyncio.run(ctl.verify_source(db, markers, snapshot))
    else:
        asyncio.run(ctl.verify_source(db, markers, snapshot))
        markers.mget.assert_awaited_once_with(
            [f"saved_search:sent:{sid}:sent", f"saved_search:sent:{sid}:pending"]
        )
    assert all(sql.lstrip().startswith(("SELECT", "SET", "LOCK")) for sql in statements)
    assert not db.commit.called
