"""IrishJobs handover keeps the identity, host and failure rules of its scraper.

Every case is a defect the retiring scraper already paid for in production.
"""

import asyncio
import json

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
    "companyLogoUrl": "/CompanyLogos/",
}


def page(items, extra_script=""):
    blob = json.dumps({"searchResults": {"items": items, "total": len(items)}})
    return (
        b"<html><script>var x = 1;</script>"
        + extra_script.encode()
        + b'<script>window.__PRELOADED_STATE__["app-unifiedResultlist"] = '
        + blob.encode()
        + b";</script></html>"
    )


def fetch(pages_by_host, params=None, monkeypatch=None):
    """`pages_by_host` maps hostname -> list of page bodies (or Responses)."""
    if monkeypatch:
        monkeypatch.setattr(
            "jobhunt_core.harvest.providers.native_irishjobs.PAGE_PAUSE_S", 0
        )
    seen = []

    async def run():
        def transport(request):
            seen.append(request)
            bodies = pages_by_host.get(request.url.host, [page([])])
            index = int(request.url.params["page"]) - 1
            value = bodies[index] if index < len(bodies) else page([])
            return (
                value
                if isinstance(value, httpx.Response)
                else httpx.Response(200, content=value)
            )

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
    assert (
        content["title"] == "Lead MS Fabric Architect"
        and content["company"] == "NTT Data"
    )
    assert content["location"] == "Dublin" and content["remote"] is True
    assert content["salary"] == "€60,000 - €70,000 per year", (
        "EUR text, never a CHF amount"
    )


def test_reediting_the_slug_is_a_re_sighting_not_a_clone():
    """Measured in production: 40 clone rows out of 919 before this rule."""
    before = (
        "https://www.irishjobs.ie/job/lead-ms-fabric-architect/ntt-data-job107803777"
    )
    after = "https://www.irishjobs.ie/job/lead-architect/ntt-data-job107803777"
    assert canonical_identity_url(before) == canonical_identity_url(after)


def test_the_two_hosts_are_one_source_deduplicated_by_platform_id(monkeypatch):
    other_host = {**ITEM, "url": "/job/lead-architect/ntt-data-job107803777"}
    result, seen = fetch(
        {
            "www.irishjobs.ie": [page([ITEM]), page([])],
            "www.jobs.ie": [page([other_host]), page([])],
        },
        monkeypatch=monkeypatch,
    )
    assert len(result.listings) == 1, "one opening, not two rows"
    assert {request.url.host for request in seen} == {"www.irishjobs.ie", "www.jobs.ie"}


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/job/x",  # foreign host
        "https://evil.example\\@www.irishjobs.ie/job/x",  # urllib vs browser parsing
        "https://evil.example%5C@www.irishjobs.ie/job/x",
        "https://user@www.irishjobs.ie/job/x",  # visual spoofing
        "https://www.irishjobs.ie:8443/job/x",  # port the portal never emits
        "https://[evil/job/x",  # malformed IPv6: urlsplit raises
        "javascript:alert(1)",
        "https://www.irishjobs.ie/",  # no real path
        "/job/x\tbreak",  # control characters
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
    assert result.error == "invalid_irishjobs_items"


def test_url_is_rebuilt_without_query_or_fragment(monkeypatch):
    """Each variant of the same URL used to become another row."""
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


def test_anchor_ignores_read_only_references_and_the_other_blob(monkeypatch):
    noise = (
        '<script>var a = window.__PRELOADED_STATE__["app-unifiedResultlist"];'
        'window.__PRELOADED_STATE__["google-onetap"] = {"searchResults": '
        '{"items": [{"title": "WRONG", "url": "/job/wrong-job9"}]}};</script>'
    )
    result, _ = fetch(
        {"www.irishjobs.ie": [page([ITEM], noise), page([])]}, monkeypatch=monkeypatch
    )
    assert [listing.payload["title"] for listing in result.listings] == [
        "Lead MS Fabric Architect"
    ]


def test_brace_inside_a_json_string_does_not_truncate_the_literal(monkeypatch):
    result, _ = fetch(
        {
            "www.irishjobs.ie": [
                page([{**ITEM, "title": "Architect } with brace"}]),
                page([]),
            ]
        },
        monkeypatch=monkeypatch,
    )
    assert result.listings[0].payload["title"] == "Architect } with brace"


@pytest.mark.parametrize(
    "body",
    [
        b"<html><script>var x = 1;</script></html>",  # no state script
        b'<html><script>window.__PRELOADED_STATE__["app-unifiedResultlist"] = {"a":</script></html>',
        b'<html><script>window.__PRELOADED_STATE__["app-unifiedResultlist"] = {not json}</script></html>',
        b'<html><script>window.__PRELOADED_STATE__["app-unifiedResultlist"] = {"searchResults": []}</script></html>',
        b'<html><script>window.__PRELOADED_STATE__["app-unifiedResultlist"] = {"searchResults": {"items": null}}</script></html>',
    ],
)
def test_unreadable_blob_is_a_visible_failure_not_an_empty_harvest(body, monkeypatch):
    with pytest.raises(ProviderResponseError):
        fetch({"www.irishjobs.ie": [body]}, monkeypatch=monkeypatch)


def test_empty_items_is_a_legitimate_empty_page(monkeypatch):
    """Verified live on both hosts: a search with no results ships items: []."""
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


def test_declared_budget_bounds_each_host(monkeypatch):
    many = [page([{**ITEM, "id": i, "url": f"/job/x-job{i}"}]) for i in range(5)]
    result, seen = fetch(
        {"www.irishjobs.ie": many, "www.jobs.ie": many}, {"max_pages": 2}, monkeypatch
    )
    assert len(seen) == 4 and result.complete is True and result.error is None


def test_unknown_parameter_is_a_configuration_error():
    with pytest.raises(ProviderConfigError):
        fetch(BOTH, {"query": "architect"})


def test_page_ceiling_matches_the_retiring_scraper():
    """Same safety ceiling and pace as irishjobs.py:271-272.

    The operating budget is smaller and lives in the scope: measured live, the
    platform slows down progressively (page 1 in 1.3 s, eight pages in 105 s,
    the ninth over 40 s). The retiring scraper never feels it because its
    incremental cursor stops at already-known pages; the native producer always
    starts at page one, so it declares a 3-page budget instead.
    """
    from jobhunt_core.harvest.providers import native_irishjobs

    assert native_irishjobs.MAX_PAGES == 8
    assert native_irishjobs.PAGE_PAUSE_S == 2.0
    assert native_irishjobs.REQUEST_TIMEOUT_S == 40


def test_a_pause_separates_the_two_hosts(monkeypatch):
    """Jumping to the second host right after the first is what timed out."""
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
    assert 0.02 in slept, "the host switch must be paced, not immediate"
