"""Manual source harvest dispatch through the authenticated Core API."""

import asyncio
import uuid
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from jobhunt_core import runs
from jobhunt_core.tests.test_integration_api import (  # noqa: F401
    _api,
    _issue,
    db,
    pytestmark,
)


def _seed_source(factory, created, *, enabled=True, scopes=2):
    source = "api_test_" + uuid.uuid4().hex[:12]

    async def go():
        async with factory() as session:
            source_id = uuid.uuid4()
            created["sources"].append(source_id)
            await session.execute(
                sa.text("INSERT INTO sources (id, name, tier) VALUES (:id, :name, 0)"),
                {"id": source_id, "name": source},
            )
            scope_ids = []
            for _ in range(scopes):
                scope_id = uuid.uuid4()
                created["scopes"].append(scope_id)
                scope_ids.append(str(scope_id))
                await session.execute(
                    sa.text(
                        "INSERT INTO harvest_scopes "
                        "(id, source_id, params, tier, enabled) "
                        "VALUES (:id, :source_id, '{}'::jsonb, 0, :enabled)"
                    ),
                    {"id": scope_id, "source_id": source_id, "enabled": enabled},
                )
            await session.commit()
            return scope_ids

    return source, asyncio.run(go())


def test_dispatch_is_scoped_and_idempotent(db):  # noqa: F811
    factory, created = db
    source, scope_ids = _seed_source(factory, created)
    _cid, _kid, token = _issue(factory, created, "harvest-writer", ["harvest:write"])
    _cid2, _kid2, read_token = _issue(
        factory, created, "harvest-reader", ["vacancies:read"]
    )
    url = f"/v1/harvest/sources/{source}/runs"

    assert _api(factory, url, token=token, method="POST").status_code == 400
    denied = _api(
        factory,
        url,
        token=read_token,
        method="POST",
        headers={"Idempotency-Key": "manual-1"},
    )
    assert denied.status_code == 403

    with patch("jobhunt_core.api.v1_harvest.run_scope_task.apply_async") as enqueue:
        accepted = _api(
            factory,
            url,
            token=token,
            method="POST",
            headers={"Idempotency-Key": "manual-1"},
        )
        replay = _api(
            factory,
            url,
            token=token,
            method="POST",
            headers={"Idempotency-Key": "manual-1"},
        )

    assert accepted.status_code == replay.status_code == 202
    assert (
        accepted.json()
        == replay.json()
        == {
            "dispatched": 2,
            "scope_ids": sorted(scope_ids),
            "source": source,
            "status": "accepted",
        }
    )
    assert enqueue.call_count == 2
    assert {
        call.kwargs["kwargs"]["scope_id"] for call in enqueue.call_args_list
    } == set(scope_ids)
    run_keys = {call.kwargs["kwargs"]["run_key"] for call in enqueue.call_args_list}
    assert len(run_keys) == 2
    assert all(key.endswith(tuple(scope_ids)) for key in run_keys)


def test_dispatch_rejects_missing_enabled_source(db):  # noqa: F811
    factory, created = db
    source, _scope_ids = _seed_source(factory, created, enabled=False, scopes=1)
    _cid, _kid, token = _issue(factory, created, "harvest-disabled", ["harvest:write"])
    with patch("jobhunt_core.api.v1_harvest.run_scope_task.apply_async") as enqueue:
        response = _api(
            factory,
            f"/v1/harvest/sources/{source}/runs",
            token=token,
            method="POST",
            headers={"Idempotency-Key": "manual-disabled"},
        )
    assert (response.status_code, response.json()["code"]) == (404, "not_found")
    enqueue.assert_not_called()


def test_dispatch_reports_live_source_run_as_busy(db):  # noqa: F811
    factory, created = db
    source, (scope_id,) = _seed_source(factory, created, scopes=1)
    _cid, _kid, token = _issue(factory, created, "harvest-busy", ["harvest:write"])

    async def claim():
        async with factory() as session:
            run_id = await runs.start_run(session, "api-busy-" + uuid.uuid4().hex)
            created["runs"].append(run_id)
            assert await runs.claim_scope_run(session, run_id, uuid.UUID(scope_id))
            await session.commit()

    asyncio.run(claim())
    with patch("jobhunt_core.api.v1_harvest.run_scope_task.apply_async") as enqueue:
        response = _api(
            factory,
            f"/v1/harvest/sources/{source}/runs",
            token=token,
            method="POST",
            headers={"Idempotency-Key": "manual-busy"},
        )
    assert (response.status_code, response.json()["code"]) == (
        409,
        "harvest_in_progress",
    )
    enqueue.assert_not_called()


def test_dispatch_does_not_cache_success_when_broker_rejects(db):  # noqa: F811
    factory, created = db
    source, _scope_ids = _seed_source(factory, created, scopes=1)
    _cid, _kid, token = _issue(factory, created, "harvest-retry", ["harvest:write"])
    url = f"/v1/harvest/sources/{source}/runs"
    headers = {"Idempotency-Key": "manual-retry"}

    with (
        patch(
            "jobhunt_core.api.v1_harvest.run_scope_task.apply_async",
            side_effect=RuntimeError("broker unavailable"),
        ),
        pytest.raises(RuntimeError, match="broker unavailable"),
    ):
        # ASGITransport re-raises app exceptions in tests; production's global
        # handler turns the same exception into the standard 500 envelope.
        _api(factory, url, token=token, method="POST", headers=headers)

    with patch("jobhunt_core.api.v1_harvest.run_scope_task.apply_async") as enqueue:
        retried = _api(factory, url, token=token, method="POST", headers=headers)
    assert retried.status_code == 202
    enqueue.assert_called_once()
