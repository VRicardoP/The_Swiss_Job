"""Manual source harvest dispatch through the authenticated Core API."""

import asyncio
import json
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


def _seed_source(factory, created, *, enabled=True, scopes=2, source_name=None):
    source = source_name or "api_test_" + uuid.uuid4().hex[:12]

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


def _seed_listing(factory, created, source, *, content, raw, age_seconds=0):
    async def go():
        async with factory() as session:
            source_id = await session.scalar(
                sa.text("SELECT id FROM sources WHERE name=:name"), {"name": source}
            )
            ids = {
                name: uuid.uuid4()
                for name in ("listing", "vacancy", "inc", "raw", "offer")
            }
            external_id = "ext-" + ids["listing"].hex[:12]
            url = f"https://example.test/{external_id}"
            digest = ids["listing"].hex
            await session.execute(
                sa.text(
                    "INSERT INTO source_listings "
                    "(id, source_id, external_id, url_normalized) "
                    "VALUES (:id, :source, :external, :url)"
                ),
                {
                    "id": ids["listing"],
                    "source": source_id,
                    "external": external_id,
                    "url": url,
                },
            )
            await session.execute(
                sa.text("INSERT INTO vacancies (id) VALUES (:id)"),
                {"id": ids["vacancy"]},
            )
            await session.execute(
                sa.text(
                    "INSERT INTO source_listing_incarnations "
                    "(id, source_listing_id, vacancy_id, seq, url, first_seen_at, last_seen_at) "
                    "VALUES (:id, :listing, :vacancy, 1, :url, "
                    "clock_timestamp()-make_interval(secs => :age), "
                    "clock_timestamp()-make_interval(secs => :age))"
                ),
                {
                    "id": ids["inc"],
                    "listing": ids["listing"],
                    "vacancy": ids["vacancy"],
                    "url": url,
                    "age": age_seconds,
                },
            )
            await session.execute(
                sa.text(
                    "INSERT INTO source_listing_revisions "
                    "(id, incarnation_id, content_hash, raw) "
                    "VALUES (:id, :inc, :hash, CAST(:raw AS jsonb))"
                ),
                {
                    "id": ids["raw"],
                    "inc": ids["inc"],
                    "hash": digest,
                    "raw": json.dumps(raw),
                },
            )
            await session.execute(
                sa.text(
                    "INSERT INTO offer_revisions "
                    "(id, vacancy_id, content_hash, text_hash, content) "
                    "VALUES (:id, :vacancy, :hash, :text_hash, CAST(:content AS jsonb))"
                ),
                {
                    "id": ids["offer"],
                    "vacancy": ids["vacancy"],
                    "hash": digest,
                    "text_hash": "text-" + digest,
                    "content": json.dumps(content),
                },
            )
            await session.execute(
                sa.text(
                    "UPDATE vacancies SET current_offer_revision_id=:offer, "
                    "primary_incarnation_id=:inc WHERE id=:vacancy"
                ),
                {
                    "offer": ids["offer"],
                    "inc": ids["inc"],
                    "vacancy": ids["vacancy"],
                },
            )
            await session.commit()
            return external_id, ids["inc"]

    return asyncio.run(go())


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


def test_dispatch_forwards_jsearch_manual_query(db):  # noqa: F811
    factory, created = db
    source, (scope_id,) = _seed_source(
        factory, created, scopes=1, source_name="jsearch"
    )
    _cid, _kid, token = _issue(
        factory, created, "jsearch-manual-writer", ["harvest:write"]
    )

    with patch("jobhunt_core.api.v1_harvest.run_scope_task.apply_async") as enqueue:
        response = _api(
            factory,
            f"/v1/harvest/sources/{source}/runs?query=data%20scientist",
            token=token,
            method="POST",
            headers={"Idempotency-Key": "jsearch-data-scientist"},
        )

    assert response.status_code == 202
    assert enqueue.call_args.kwargs["kwargs"] == {
        "scope_id": scope_id,
        "run_key": enqueue.call_args.kwargs["kwargs"]["run_key"],
        "jsearch_query": "data scientist",
    }


def test_dispatch_rejects_jsearch_query_for_other_sources(db):  # noqa: F811
    factory, created = db
    source, _scope_ids = _seed_source(factory, created, scopes=1)
    _cid, _kid, token = _issue(
        factory, created, "non-jsearch-query-writer", ["harvest:write"]
    )

    with patch("jobhunt_core.api.v1_harvest.run_scope_task.apply_async") as enqueue:
        response = _api(
            factory,
            f"/v1/harvest/sources/{source}/runs?query=data%20scientist",
            token=token,
            method="POST",
            headers={"Idempotency-Key": "non-jsearch-query"},
        )

    assert (response.status_code, response.json()["code"]) == (
        400,
        "unsupported_parameter",
    )
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


def test_source_aggregate_is_bounded_and_uses_latest_raw(db):  # noqa: F811
    factory, created = db
    source, (scope_id,) = _seed_source(factory, created, scopes=1)
    newest_external, newest_inc = _seed_listing(
        factory,
        created,
        source,
        content={
            "title": "Python Engineer",
            "company": "ACME",
            "description": "x" * 700,
            "location": "Berlin",
            "remote": True,
            "tags": ["Python", "API"],
        },
        raw={
            "category": "Software Development",
            "job_type": "full_time",
            "job_types": ["Old"],
            "publication_date": "2026-10-10T12:00:00Z",
        },
    )
    _seed_listing(
        factory,
        created,
        source,
        content={
            "title": "Platform Engineer",
            "company": "Beta",
            "location": "Zurich",
            "remote": False,
            "tags": ["Python"],
            "canton": "ZH",
        },
        raw={
            "jobIndustry": ["Education", 4, {"invalid": True}],
            "jobType": ["Contract", 4, {"invalid": True}],
            "pubDate": "2026-10-11T12:00:00Z",
        },
        age_seconds=60,
    )

    async def update_state_and_raw():
        async with factory() as session:
            await session.execute(
                sa.text(
                    "INSERT INTO source_scope_state "
                    "(scope_id, cursor, last_complete_at, consecutive_failures) "
                    "VALUES (:scope, '{}'::jsonb, clock_timestamp(), 0)"
                ),
                {"scope": uuid.UUID(scope_id)},
            )
            await session.execute(
                sa.text(
                    "INSERT INTO source_listing_revisions "
                    "(incarnation_id, content_hash, raw, fetched_at) "
                    "VALUES (:inc, :hash, CAST(:raw AS jsonb), "
                    "clock_timestamp()+interval '1 second')"
                ),
                {
                    "inc": newest_inc,
                    "hash": uuid.uuid4().hex,
                    "raw": json.dumps(
                        {
                            "category": "Software Development",
                            "job_type": "full_time",
                            "job_types": ["Full-time", 4, {"invalid": True}],
                            "publication_date": "2026-10-10T12:00:00Z",
                        }
                    ),
                },
            )
            await session.commit()

    asyncio.run(update_state_and_raw())
    _cid, _kid, token = _issue(factory, created, "aggregate-reader", ["vacancies:read"])
    url = f"/v1/harvest/sources/{source}/aggregate?item_limit=2&top_n=10"
    response = _api(factory, url, token=token)
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == source
    assert body["total"] == 2
    assert body["enabled_scopes"] == body["observed_scopes"] == 1
    assert body["max_consecutive_failures"] == 0
    assert body["update_in_progress"] is False
    assert body["counts"] == {
        "locations": {"Berlin": 1, "Zurich": 1},
        "tags": {"API": 1, "Python": 2},
        "companies": {"ACME": 1, "Beta": 1},
        "publishers": {},
        "categories": {"education": 1, "software-development": 1},
        "job_types": {"Contract": 1, "Full-time": 1},
        "cantons": {"ZH": 1},
        "prof_groups": {},
        "remote": 1,
        "onsite": 1,
    }
    newest = next(
        item for item in body["items"] if item["external_id"] == newest_external
    )
    assert newest["job_types"] == ["Full-time"]
    assert newest["job_type"] == "full_time"
    assert newest["category"] == "Software Development"
    assert len(newest["description"]) == 500
    jobicy_shape = next(item for item in body["items"] if item != newest)
    assert jobicy_shape["categories"] == ["Education"]
    assert jobicy_shape["job_types"] == ["Contract"]
    assert jobicy_shape["published_at"] == "2026-10-11T12:00:00Z"

    bounded = _api(
        factory,
        f"/v1/harvest/sources/{source}/aggregate?item_limit=1&top_n=1",
        token=token,
    )
    assert bounded.status_code == 200
    assert bounded.json()["total"] == 2
    assert len(bounded.json()["items"]) == 1
    assert all(
        len(counts) <= 1
        for key, counts in bounded.json()["counts"].items()
        if isinstance(counts, dict)
    )

    unchanged = _api(
        factory,
        url,
        token=token,
        headers={"If-None-Match": response.headers["etag"]},
    )
    assert unchanged.status_code == 304


def test_source_aggregate_preserves_himalayas_display_fields(db):  # noqa: F811
    factory, created = db
    source, _scope_ids = _seed_source(factory, created, scopes=1)
    external_id, _incarnation = _seed_listing(
        factory,
        created,
        source,
        content={
            "title": "Remote editor",
            "company": "School",
            "description": "<p>Raw HTML</p>",
            "location": "Worldwide",
            "remote": True,
            "tags": ["Writing", "Writing"],
            "salary_min": 10.5,
            "salary_max": 20,
            "salary_currency": "EUR",
            "employment_type": "Contract",
            "logo": "https://cdn.example/logo.png",
        },
        raw={
            "categories": ["Content Writing", "Editing"],
            "pubDate": 1791950000,
        },
    )
    _client_id, _key_id, token = _issue(
        factory, created, "himalayas-reader", ["vacancies:read"]
    )

    response = _api(
        factory,
        f"/v1/harvest/sources/{source}/aggregate?item_limit=1&top_n=10",
        token=token,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["categories"] == {
        "content-writing": 1,
        "editing": 1,
    }
    item = body["items"][0]
    assert item["external_id"] == external_id
    assert item["categories"] == ["Content Writing", "Editing"]
    assert item["published_at"] == 1791950000
    assert item["salary_min"] == 10.5
    assert item["salary_max"] == 20
    assert item["salary_currency"] == "EUR"
    assert item["job_type"] == "Contract"
    assert item["logo"] == "https://cdn.example/logo.png"


def test_source_aggregate_preserves_ictjobs_professional_groups(db):  # noqa: F811
    factory, created = db
    source, _scope_ids = _seed_source(factory, created, scopes=1)
    _external_id, _incarnation = _seed_listing(
        factory,
        created,
        source,
        content={
            "title": "System Engineer",
            "company": "",
            "location": "Bern",
            "tags": ["Cloud"],
            "prof_group": "System Engineering",
        },
        raw={"date": "2026-10-14T00:23:00"},
    )
    _client_id, _key_id, token = _issue(
        factory, created, "ictjobs-reader", ["vacancies:read"]
    )

    response = _api(
        factory,
        f"/v1/harvest/sources/{source}/aggregate?item_limit=1&top_n=10",
        token=token,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["prof_groups"] == {"System Engineering": 1}
    assert body["items"][0]["prof_group"] == "System Engineering"


def test_jsearch_aggregate_preserves_raw_description(db):  # noqa: F811
    factory, created = db
    source, _scope_ids = _seed_source(factory, created, scopes=1, source_name="jsearch")
    _seed_listing(
        factory,
        created,
        source,
        content={
            "title": "Teacher",
            "company": "School",
            "description": "provider spacing",
            "location": "Remote",
            "tags": [],
        },
        raw={"job_description": "  provider spacing  "},
    )
    _client_id, _key_id, token = _issue(
        factory, created, "jsearch-aggregate-reader", ["vacancies:read"]
    )

    response = _api(
        factory,
        "/v1/harvest/sources/jsearch/aggregate?item_limit=1&top_n=1",
        token=token,
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["description"] == "  provider spacing  "


def test_source_aggregate_requires_read_scope_and_known_source(db):  # noqa: F811
    factory, created = db
    source, _scope_ids = _seed_source(factory, created, scopes=1)
    _cid, _kid, writer = _issue(factory, created, "aggregate-writer", ["harvest:write"])
    denied = _api(factory, f"/v1/harvest/sources/{source}/aggregate", token=writer)
    assert denied.status_code == 403

    _cid2, _kid2, reader = _issue(
        factory, created, "aggregate-missing", ["vacancies:read"]
    )
    missing = _api(
        factory, "/v1/harvest/sources/no_such_source/aggregate", token=reader
    )
    assert (missing.status_code, missing.json()["code"]) == (404, "not_found")
