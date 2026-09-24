"""Transferred searches reject unsupported filters before any persistent change."""

import asyncio
from datetime import datetime, timedelta, timezone
import uuid

from jobhunt_core import search_execution
from jobhunt_core.tests.test_integration_api_saved_searches import (
    db,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
    pytestmark,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
    _seed_profile,
    _post,
    _rows,
    tia,
)


def test_configured_search_invalid_filter_is_400_without_partial_update(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    token, _, pid = _seed_profile(factory, made)
    created = _post(
        factory,
        token,
        {"profile_id": str(pid), "name": "search", "filters": {"q": "Python"}},
    )
    assert created.status_code == 201
    sid = uuid.UUID(created.json()["id"])

    async def configure():
        async with factory() as s:
            await search_execution.configure_execution(
                s,
                sid,
                contract=search_execution.CONTRACT,
                notify_since=datetime.now(timezone.utc) - timedelta(days=1),
            )
            await s.commit()

    asyncio.run(configure())
    before = _rows(
        factory, "SELECT to_jsonb(s) FROM saved_searches s WHERE id=:id", id=sid
    )
    response = tia._api(
        factory,
        f"/v1/saved-searches/{sid}",
        token=token,
        method="PUT",
        headers={"If-Match": created.headers["etag"]},
        json_body={
            "name": "must not persist",
            "filters": {"unknown_filter": "private value"},
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "invalid_filters"
    assert "private value" not in response.text
    assert (
        _rows(factory, "SELECT to_jsonb(s) FROM saved_searches s WHERE id=:id", id=sid)
        == before
    )


def test_create_execution_is_atomic_and_idempotent_with_detail_etag(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    token, _, pid = _seed_profile(factory, made)
    body = {
        "profile_id": str(pid),
        "name": "new",
        "filters": {"q": "Python"},
        "execution_contract": "swissjob-v1",
    }
    first = _post(factory, token, body, key="same-operation")
    assert first.status_code == 201, first.text
    assert _post(factory, token, body, key="same-operation").content == first.content
    sid = uuid.UUID(first.json()["id"])
    assert _rows(
        factory,
        "SELECT contract,enabled,run_number FROM saved_search_execution WHERE saved_search_id=:id",
        id=sid,
    ) == [("swissjob-v1", True, 0)]
    detail = tia._api(factory, f"/v1/saved-searches/{sid}", token=token)
    assert detail.status_code == 200 and detail.json() == first.json()
    assert detail.headers["etag"] == first.headers["etag"]
    second_token, _, _ = _seed_profile(factory, made)
    assert (
        tia._api(factory, f"/v1/saved-searches/{sid}", token=second_token).status_code
        == 404
    )
    body["filters"] = {"unexpected": "bad"}
    bad = _post(factory, token, body)
    assert bad.status_code == 400, bad.text
    assert _rows(
        factory, "SELECT count(*) FROM saved_searches WHERE profile_id=:id", id=pid
    ) == [(1,)]
    assert _rows(factory, "SELECT count(*) FROM saved_search_execution") == [(1,)]


def test_manual_run_requires_authority_owner_delivery_and_queue(db, monkeypatch):  # noqa: F811  (la fixture, no una redefinición)
    from unittest.mock import Mock
    from jobhunt_core.config import settings
    from jobhunt_core.celery_app import celery_app

    factory, made = db
    token, tenant, pid = _seed_profile(factory, made)
    created = _post(
        factory,
        token,
        {"profile_id": str(pid), "name": "run", "execution_contract": "swissjob-v1"},
    )
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    endpoint = f"/v1/saved-searches/{sid}/run"
    send = Mock()
    monkeypatch.setattr(celery_app, "send_task", send)
    monkeypatch.setattr(settings, "CORE_SAVED_SEARCH_EXECUTION_ENABLED", False)
    assert tia._api(factory, endpoint, token=token, method="POST").status_code == 503
    monkeypatch.setattr(settings, "CORE_SAVED_SEARCH_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "CORE_DELIVERY_HTTP_DESTINATIONS", {})
    assert tia._api(factory, endpoint, token=token, method="POST").status_code == 503
    monkeypatch.setattr(
        settings,
        "CORE_DELIVERY_HTTP_DESTINATIONS",
        {tenant: "http://never-called.invalid"},
    )
    foreign, _, _ = _seed_profile(factory, made)
    assert tia._api(factory, endpoint, token=foreign, method="POST").status_code == 404
    send.assert_not_called()
    response = tia._api(factory, endpoint, token=token, method="POST")
    assert response.status_code == 202, response.text
    assert response.json() == {"status": "dispatched", "search_id": sid}
    send.assert_called_once_with(
        "jobhunt.searches.run_one", kwargs={"search_id": sid}, retry=False
    )
    send.side_effect = RuntimeError("secret broker details must not escape")
    failure = tia._api(factory, endpoint, token=token, method="POST")
    assert failure.status_code == 503 and "secret broker" not in failure.text
    assert _rows(
        factory,
        "SELECT last_run_at,total_matches FROM saved_searches WHERE id=:id",
        id=uuid.UUID(sid),
    ) == [(None, 0)]
