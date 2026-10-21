"""IrishJobs handover keeps identity, host, budget and failure guarantees."""

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from jobhunt_core.harvest.normalize import normalize_offer
from jobhunt_core.harvest.provider import ProviderConfigError, ProviderResponseError
from jobhunt_core.harvest.providers.native_irishjobs import (
    IrishJobsProvider,
    canonical_identity_url,
)

ITEM = {
    "id": 107803777,
    "title": "Lead MS Fabric Architect",
    "companyName": "NTT Data",
    "location": "Dublin",
    "url": "/job/lead-ms-fabric-architect/ntt-data-job107803777",
    "textSnippet": "<p>Azure &amp; Fabric</p>",
    "salary": "€60,000 - €70,000 per year",
    "datePosted": "2026-09-20T08:00:00Z",
}


def page(items):
    return {"items": items, "meta": {"total": len(items)}}


def fetch(pages_by_host, params=None, monkeypatch=None):
    """Map each hostname to result-list JSON objects or httpx responses."""
    if monkeypatch:
        monkeypatch.setattr(
            "jobhunt_core.harvest.providers.native_irishjobs.PAGE_PAUSE_S", 0
        )
    seen = []

    async def run():
        def transport(request):
            seen.append(request)
            payload = json.loads(request.content)
            search_url = urlsplit(payload["url"])
            bodies = pages_by_host.get(search_url.hostname, [page([])])
            index = int(parse_qs(search_url.query)["page"][0]) - 1
            value = bodies[index] if index < len(bodies) else page([])
            if isinstance(value, httpx.Response):
                return value
            if isinstance(value, bytes):
                return httpx.Response(200, content=value)
            return httpx.Response(200, json=value)

        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            return await IrishJobsProvider().fetch_new(
                params if params is not None else {}, None, http
            )

    return asyncio.run(run()), seen


BOTH = {"www.irishjobs.ie": [page([ITEM]), page([])], "www.jobs.ie": [page([])]}


def test_offer_keeps_platform_identity_real_url_and_canonical_content(monkeypatch):
    result, _ = fetch(BOTH, monkeypatch=monkeypatch)
    (listing,) = result.listings
    assert listing.external_id == "url:https://www.irishjobs.ie/job/job107803777"
    assert listing.url == (
        "https://www.irishjobs.ie/job/lead-ms-fabric-architect/ntt-data-job107803777"
    )
    assert listing.payload == ITEM
    content = normalize_offer("irishjobs", listing.payload)
    assert content["title"] == "Lead MS Fabric Architect"
    assert content["company"] == "NTT Data"
    assert content["location"] == "Dublin" and content["remote"] is True
    assert content["salary"] == "€60,000 - €70,000 per year"


def test_reediting_the_slug_is_a_re_sighting_not_a_clone():
    before = (
        "https://www.irishjobs.ie/job/lead-ms-fabric-architect/ntt-data-job107803777"
    )
    after = "https://www.irishjobs.ie/job/lead-architect/ntt-data-job107803777"
    assert canonical_identity_url(before) == canonical_identity_url(after)


def test_two_sites_use_public_json_contract_and_deduplicate_platform_id(monkeypatch):
    other_site = {**ITEM, "url": "/job/lead-architect/ntt-data-job107803777"}
    result, seen = fetch(
        {
            "www.irishjobs.ie": [page([ITEM]), page([])],
            "www.jobs.ie": [page([other_site]), page([])],
        },
        monkeypatch=monkeypatch,
    )
    assert len(result.listings) == 1
    assert {request.url.host for request in seen} == {
        "www.irishjobs.ie",
        "www.jobs.ie",
    }
    assert {request.url.path for request in seen} == {
        "/public-api/resultlist/unifiedResultlist"
    }
    assert all(request.method == "POST" for request in seen)
    assert all(request.headers["accept"] == "application/json" for request in seen)
    contracts = {
        (urlsplit(body["url"]).hostname, body["siteId"])
        for body in (json.loads(request.content) for request in seen)
    }
    assert contracts == {("www.irishjobs.ie", 301), ("www.jobs.ie", 302)}
    assert all(
        set(body) == {"url", "lang", "siteId", "userData"} and body["userData"] == {}
        for body in (json.loads(request.content) for request in seen)
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/job/x",
        "https://evil.example\\@www.irishjobs.ie/job/x",
        "https://evil.example%5C@www.irishjobs.ie/job/x",
        "https://user@www.irishjobs.ie/job/x",
        "https://www.irishjobs.ie:8443/job/x",
        "https://[evil/job/x",
        "javascript:alert(1)",
        "https://www.irishjobs.ie/",
        "/job/x\tbreak",
    ],
)
def test_unusable_url_drops_only_that_offer(url, monkeypatch):
    rows = [{**ITEM, "url": url}, {**ITEM, "id": 2, "url": "/job/other-job2"}]
    result, _ = fetch(
        {"www.irishjobs.ie": [page(rows), page([])]}, monkeypatch=monkeypatch
    )
    assert [listing.url for listing in result.listings] == [
        "https://www.irishjobs.ie/job/other-job2"
    ]
    # DT-148 E1: el ilegible se cuenta en el cursor, el barrido sigue completo.
    assert result.complete and result.error is None
    assert result.next_cursor["invalid"] == 1


def test_url_is_rebuilt_without_query_or_fragment(monkeypatch):
    result, _ = fetch(
        {
            "www.irishjobs.ie": [
                page([{**ITEM, "url": "/job/x-job1?utm_source=x#top"}]),
                page([]),
            ]
        },
        monkeypatch=monkeypatch,
    )
    assert result.listings[0].url == "https://www.irishjobs.ie/job/x-job1"


def test_unexpected_scalar_in_a_field_degrades_that_offer_only(monkeypatch):
    rows = [{**ITEM, "title": 42}, {**ITEM, "id": 2, "url": "/job/other-job2"}]
    result, _ = fetch(
        {"www.irishjobs.ie": [page(rows), page([])]}, monkeypatch=monkeypatch
    )
    assert len(result.listings) == 1


@pytest.mark.parametrize(
    "body",
    [
        b"<html>not JSON</html>",
        b"{not json}",
        json.dumps([]).encode(),
        json.dumps({"items": None}).encode(),
    ],
)
def test_unreadable_result_is_a_visible_failure_not_empty_harvest(body, monkeypatch):
    with pytest.raises(ProviderResponseError):
        fetch({"www.irishjobs.ie": [body]}, monkeypatch=monkeypatch)


def test_empty_items_is_a_legitimate_empty_page(monkeypatch):
    result, _ = fetch(
        {"www.irishjobs.ie": [page([])], "www.jobs.ie": [page([])]},
        monkeypatch=monkeypatch,
    )
    assert result.listings == () and result.complete is True and result.error is None


def test_page_full_of_unparseable_items_is_a_failure(monkeypatch):
    with pytest.raises(ProviderResponseError):
        fetch(
            {
                "www.irishjobs.ie": [
                    page([{"title": "x"}, {"url": "/job/y-job1"}]),
                    page([]),
                ]
            },
            monkeypatch=monkeypatch,
        )


def test_late_failure_preserves_the_valid_prefix(monkeypatch):
    result, _ = fetch(
        {"www.irishjobs.ie": [page([ITEM]), httpx.Response(503)]},
        monkeypatch=monkeypatch,
    )
    assert len(result.listings) == 1 and result.complete is False


def test_first_site_failure_still_harvests_second_site(monkeypatch):
    result, seen = fetch(
        {
            "www.irishjobs.ie": [httpx.Response(403)],
            "www.jobs.ie": [page([ITEM]), page([])],
        },
        {"max_pages": 3},
        monkeypatch,
    )
    assert {request.url.host for request in seen} == {
        "www.irishjobs.ie",
        "www.jobs.ie",
    }
    assert len(result.listings) == 1
    assert result.listings[0].url.startswith("https://www.jobs.ie/")
    assert result.complete is False and result.error is not None


def test_both_sites_failing_does_not_become_an_empty_success(monkeypatch):
    with pytest.raises(httpx.HTTPStatusError):
        fetch(
            {
                "www.irishjobs.ie": [httpx.Response(403)],
                "www.jobs.ie": [httpx.Response(503)],
            },
            monkeypatch=monkeypatch,
        )


def test_declared_budget_bounds_each_site(monkeypatch):
    many = [page([{**ITEM, "id": i, "url": f"/job/x-job{i}"}]) for i in range(5)]
    result, seen = fetch(
        {"www.irishjobs.ie": many, "www.jobs.ie": many}, {"max_pages": 2}, monkeypatch
    )
    assert len(seen) == 4 and result.complete is True and result.error is None


def test_response_byte_budget_is_enforced(monkeypatch):
    monkeypatch.setattr(
        "jobhunt_core.harvest.providers.native_irishjobs.MAX_RESPONSE_BYTES", 10
    )
    with pytest.raises(ProviderResponseError, match="byte budget"):
        fetch(BOTH, monkeypatch=monkeypatch)


def test_unknown_parameter_is_a_configuration_error():
    with pytest.raises(ProviderConfigError):
        fetch(BOTH, {"query": "architect"})


def test_page_ceiling_and_pacing_match_retiring_scraper():
    from jobhunt_core.harvest.providers import native_irishjobs

    assert native_irishjobs.MAX_PAGES == 8
    assert native_irishjobs.PAGE_PAUSE_S == 2.0
    assert native_irishjobs.REQUEST_TIMEOUT_S == 40


def test_a_pause_separates_the_two_sites(monkeypatch):
    slept = []
    monkeypatch.setattr(
        "jobhunt_core.harvest.providers.native_irishjobs.PAGE_PAUSE_S", 0.01
    )

    async def record(seconds):
        slept.append(seconds)

    monkeypatch.setattr(
        "jobhunt_core.harvest.providers.native_irishjobs.asyncio.sleep", record
    )
    fetch(
        {"www.irishjobs.ie": [page([ITEM])], "www.jobs.ie": [page([])]},
        {"max_pages": 1},
    )
    assert 0.02 in slept
