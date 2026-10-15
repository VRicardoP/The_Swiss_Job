"""Mosaic on Workday: the externalPath guard chain, link by link."""

import asyncio
import json

import httpx
import pytest

from jobhunt_core.harvest.providers.native_isp_workday import external_path_issue

GOOD_PATH = "/job/Mosaic-School--Ecole-Mosaic-Switzerland-Geneva/Shadow_JR213711"


def posting(
    i=1,
    *,
    title="Shadow - Assistant",
    path=None,
    location="Mosaic School / Ecole Mosaic, Switzerland, Geneva",
    bullet=None,
):
    value = {
        "title": title,
        # A distinct path per offer, like the real API: two offers sharing a
        # URL are ONE listing for the sink, which is the VD.1 guard at work.
        "externalPath": f"{GOOD_PATH}{i}" if path is None else path,
        "locationsText": location,
        "postedOn": "Posted 16 Days Ago",
        "bulletFields": [f"JR21371{i}"] if bullet is None else bullet,
    }
    return value


def body(postings, total=None):
    return json.dumps(
        {
            "total": total if total is not None else len(postings),
            "jobPostings": postings,
            "facets": [],
            "userAuthenticated": False,
        }
    )


EMPTY = body([])


def fetch(pages, *, params=None, monkeypatch=None):
    from jobhunt_core.harvest.providers import native_isp_workday as module

    if monkeypatch:
        monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
        monkeypatch.setattr(module, "RETRY_BACKOFF_S", 0)
    requests = []

    async def run():
        def transport(request):
            payload = json.loads(request.content)
            requests.append(payload["offset"])
            page = pages.get(payload["offset"], pages.get("default", EMPTY))
            if isinstance(page, int):
                return httpx.Response(page, text="nope")
            return httpx.Response(200, text=page)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            result = await module.ISPWorkdayProvider().fetch_new(
                {} if params is None else params, None, client
            )
        return result, requests

    return asyncio.run(run())


def test_a_matching_offer_keeps_the_producers_shape(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, requests = fetch({0: body([posting()])}, monkeypatch=monkeypatch)

    assert result.complete and result.error is None and requests == [0]
    listing = result.listings[0]
    assert listing.external_id == "mosaic_geneva:JR213711"
    assert listing.url == (
        "https://internationalschools.wd3.myworkdayjobs.com/en-US/ISPCareers"
        + GOOD_PATH
        + "1"
    )
    assert normalize_offer("swiss_schools_isp", listing.payload) == {
        "title": "Shadow - Assistant",
        "company": "Mosaic Ecole",
        "description": None,
        "location": "Mosaic School / Ecole Mosaic, Switzerland, Geneva",
        "remote": False,
        "tags": ["education", "international school", "mosaic_geneva"],
        "salary": None,
        "language": "en",
        "canton": "GE",
    }
    # postedOn is a relative bucket, never a date: no published_at is invented.
    assert "published_at" not in listing.payload


def test_other_schools_of_the_shared_tenant_are_not_ours(monkeypatch):
    rows = [posting(1), posting(2, location="ISP Madrid, Spain")]
    result, _requests = fetch({0: body(rows)}, monkeypatch=monkeypatch)

    assert [listing.external_id for listing in result.listings] == [
        "mosaic_geneva:JR213711"
    ]
    # Zero matches on a shared tenant is legitimate, so no error is raised.
    assert result.complete and result.error is None


def test_no_match_at_all_is_a_verified_empty(monkeypatch):
    result, _requests = fetch(
        {0: body([posting(1, location="ISP Madrid, Spain")])}, monkeypatch=monkeypatch
    )

    assert result.listings == () and result.complete
    assert result.next_cursor["verified_empty"] is True


@pytest.mark.parametrize(
    "path",
    [
        "?job=123",
        "#job",
        "/not-a-job",
        "job/x",
        "//evil.test/job/x",
        "https://evil.test/job/x",
        "javascript:/job/x",
        "/job/",
        "/job/x/",
        "/job//x",
        "/job/../admin",
        "/job/./x",
        "/job/x\\y",
        "/job/x y",
        "/job/x\ty",
        "/job/x%2Fy",
        "/job/x%5Cy",
        "/job/%2E%2E/admin",
        "/job/%252E%252E/admin",
        "/job/%25252E",
        "/job/x%",
        "/job/x%2",
        "/job/x%GG",
        "/job/x%00y",
        "/job/x%C0%AFy",
        "/job/x%ED%A0%80y",
        "/job/\ud800",
    ],
)
def test_a_path_that_is_not_an_offer_path_is_refused(path):
    assert external_path_issue(path) is not None


@pytest.mark.parametrize(
    "path",
    [
        GOOD_PATH,
        "/job/City/Role_JR1",
        "/job/Gen%C3%A8ve/Role_JR1",
        "/job/Role%20Name_JR1",
    ],
)
def test_a_real_offer_path_is_accepted(path):
    assert external_path_issue(path) is None


def test_a_bad_path_degrades_only_that_item_and_is_reported(monkeypatch):
    rows = [posting(1), posting(2, path="?job=123"), posting(3, title="  ")]
    result, _requests = fetch({0: body(rows)}, monkeypatch=monkeypatch)

    assert [listing.external_id for listing in result.listings] == [
        "mosaic_geneva:JR213711"
    ]
    assert result.next_cursor["degraded_items"] == 2
    assert not result.complete and result.error == "degraded_isp_items"


def test_a_degenerate_locations_field_does_not_take_the_batch_down(monkeypatch):
    rows = [posting(1), posting(2, location=["a list"]), {"nope": 1}]
    result, _requests = fetch({0: body(rows)}, monkeypatch=monkeypatch)

    assert len(result.listings) == 1
    assert result.next_cursor["degraded_items"] == 1


def test_a_degenerate_bulletfields_still_emits_the_offer(monkeypatch):
    result, _requests = fetch(
        {0: body([posting(1, bullet="nope")])}, monkeypatch=monkeypatch
    )

    # source_id falls back to the already validated path.
    assert result.listings[0].external_id == f"mosaic_geneva:{GOOD_PATH}1"


@pytest.mark.parametrize(
    "payload", ["{not json", "null", "[1,2]", '{"total": 1}', '{"jobPostings": "x"}']
)
def test_a_body_that_is_not_the_contract_is_a_visible_failure(payload, monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError):
        fetch({0: payload}, monkeypatch=monkeypatch)


def test_a_page_of_non_objects_is_a_visible_failure(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="none is an object"):
        fetch({0: body(["a", "b"])}, monkeypatch=monkeypatch)


def test_a_full_page_asks_for_the_next_offset(monkeypatch):
    from jobhunt_core.harvest.providers import native_isp_workday as module

    monkeypatch.setattr(module, "PAGE_SIZE", 2)
    pages = {0: body([posting(1), posting(2)]), 2: body([posting(3)])}
    result, requests = fetch(pages, monkeypatch=monkeypatch)

    assert requests == [0, 2]
    assert len(result.listings) == 3


def test_an_invalid_scope_fails_before_the_network(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderConfigError

    with pytest.raises(ProviderConfigError):
        fetch({}, params={"searchText": "x"}, monkeypatch=monkeypatch)
