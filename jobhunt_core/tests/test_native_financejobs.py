"""Financejobs handover keeps the identity and failure rules of its scraper.

Every case here is a defect the retiring scraper already paid for; losing one
at handover would reintroduce it. See backend/scrapers/financejobs.py.
"""

import asyncio
import json

import httpx
import pytest

from jobhunt_core.harvest.normalize import normalize_offer
from jobhunt_core.harvest.provider import ProviderConfigError, ProviderResponseError
from jobhunt_core.harvest.providers.native_financejobs import FinancejobsProvider


def page(jobs, path=("pageProps", "jobsSSR")):
    node = {"jobs": jobs}
    for key in reversed(path):
        node = {key: node}
    body = json.dumps({"props": node})
    return (b'<html><body><script id="__NEXT_DATA__" type="application/json">'
            + body.encode() + b"</script></body></html>")


def fetch(pages, params=None, monkeypatch=None):
    if monkeypatch:
        monkeypatch.setattr(
            "jobhunt_core.harvest.providers.native_financejobs.PAGE_PAUSE_S", 0)
    seen = []

    async def run():
        def transport(request):
            seen.append(request)
            value = pages[int(request.url.params["page"]) - 1]
            return value if isinstance(value, httpx.Response) else httpx.Response(200, content=value)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            return await FinancejobsProvider().fetch_new(
                params if params is not None else {}, None, http)
    return asyncio.run(run()), seen


JOB = {"jobId": 14697110, "title": "Senior Analyst", "companyName": "Bank AG",
       "location": "Zürich", "description": "<p>Risk &amp; controlling</p>",
       "datePosted": "2026-09-20T08:00:00Z", "salary": "120000"}


def test_offer_keeps_portal_identity_url_and_canonical_content(monkeypatch):
    result, _ = fetch([page([JOB]), page([])], monkeypatch=monkeypatch)
    listing, = result.listings
    assert listing.external_id == "id:14697110"
    assert listing.url == "https://www.financejobs.ch/de/job/14697110"
    assert listing.payload == JOB, "raw reaches the sink unchanged"
    content = normalize_offer("financejobs", listing.payload)
    assert content["title"] == "Senior Analyst" and content["company"] == "Bank AG"
    # Entities are NOT decoded, exactly as the retiring writer leaves them
    # (both strip_html_tags are the same function): decoding here would
    # change the embedded text and move every vector of this source.
    assert content["description"] == "Risk &amp; controlling"
    assert content["location"] == "Zürich" and content["canton"] == "ZH"
    assert content["remote"] is False
    assert content["salary"] is None, "amounts stay in raw until a mapped enrichment"


@pytest.mark.parametrize("job_id,expected", [
    ({"jobId": "00042"}, "id:42"),                 # decimal canonicalised
    ({"jobId": "42"}, "id:42"),
    ({"jcJobId": "cbbceba0-ab30-4f23-91fc-9fe4cf3bc8a0"},
     "id:cbbceba0-ab30-4f23-91fc-9fe4cf3bc8a0"),
])
def test_accepted_identity_forms_match_the_portal(job_id, expected, monkeypatch):
    result, _ = fetch([page([{**JOB, "jobId": None, **job_id}]), page([])],
                      monkeypatch=monkeypatch)
    assert result.listings[0].external_id == expected


@pytest.mark.parametrize("job", [
    {"jobId": True},                     # bool is not an id
    {"jobId": "42?utm=x"},               # would fabricate a second URL
    {"jobId": "../admin"},
    {"jobId": "١٤"},                     # unicode digits the portal never emits
    {"jobId": "9" * 3000},               # would overflow the stored URL
    {"jobId": -1},
    {"jcJobId": "CBBCEBA0-AB30-4F23-91FC-9FE4CF3BC8A0"},   # not canonical
    {"jobId": 1, "title": "  <b></b> "},                   # empty after cleaning
])
def test_unusable_identity_or_title_skips_only_that_offer(job, monkeypatch):
    rows = [{**JOB, "jobId": None, "jcJobId": None, **job}, {**JOB, "jobId": 7}]
    result, _ = fetch([page(rows), page([])], monkeypatch=monkeypatch)
    assert [listing.external_id for listing in result.listings] == ["id:7"]
    assert result.error == "invalid_financejobs_items"


def test_blank_company_degrades_exactly_like_a_missing_one(monkeypatch):
    """Otherwise the same posting gets two identities and stops updating."""
    blank, _ = fetch([page([{**JOB, "companyName": "  <b></b> "}]), page([])],
                     monkeypatch=monkeypatch)
    missing, _ = fetch([page([{**JOB, "companyName": None}]), page([])],
                       monkeypatch=monkeypatch)
    assert normalize_offer("financejobs", blank.listings[0].payload)["company"] == \
        normalize_offer("financejobs", missing.listings[0].payload)["company"] == "Unknown"


def test_historic_props_shape_is_still_read(monkeypatch):
    result, _ = fetch([page([JOB], ("initialProps", "pageProps", "jobsSSR")), page([])],
                      monkeypatch=monkeypatch)
    assert len(result.listings) == 1


@pytest.mark.parametrize("body", [
    b"<html><body>no next data here</body></html>",
    b'<script id="__NEXT_DATA__">{"props":{"pageProps":{}}}</script>',   # shape gone
    b'<script id="__NEXT_DATA__">{"props":{"pageProps":{"jobsSSR":{"jobs":null}}}}</script>',
    b'<script id="__NEXT_DATA__">[1,2,3]</script>',
    b'<script id="__NEXT_DATA__">{not json</script>',
])
def test_unreadable_page_is_a_visible_failure_not_an_empty_harvest(body, monkeypatch):
    """The original defect was answering '0 offers' to a shape we cannot read."""
    with pytest.raises(ProviderResponseError):
        fetch([body], monkeypatch=monkeypatch)


def test_page_full_of_unparseable_offers_is_a_failure_too(monkeypatch):
    with pytest.raises(ProviderResponseError):
        fetch([page([{"jobId": None, "title": "x"}, {"jobId": None, "title": "y"}]),
               page([])], monkeypatch=monkeypatch)


def test_empty_list_is_a_legitimate_end_of_pagination(monkeypatch):
    result, seen = fetch([page([JOB]), page([])], monkeypatch=monkeypatch)
    assert len(seen) == 2 and result.complete is True and result.error is None


def test_late_failure_preserves_the_valid_prefix(monkeypatch):
    result, _ = fetch([page([JOB]), httpx.Response(503)], monkeypatch=monkeypatch)
    assert len(result.listings) == 1 and result.complete is False
    assert result.error == "HTTPStatusError"


def test_first_page_failure_is_raised_not_swallowed(monkeypatch):
    with pytest.raises(httpx.HTTPStatusError):
        fetch([httpx.Response(503)], monkeypatch=monkeypatch)


def test_declared_budget_bounds_the_sweep(monkeypatch):
    result, seen = fetch([page([JOB])] * 5, {"max_pages": 2}, monkeypatch)
    assert len(seen) == 2 and result.pages_fetched == 2
    assert result.complete is True and result.error is None


def test_unknown_parameter_is_a_configuration_error():
    with pytest.raises(ProviderConfigError):
        fetch([page([JOB])], {"query": "analyst"})
