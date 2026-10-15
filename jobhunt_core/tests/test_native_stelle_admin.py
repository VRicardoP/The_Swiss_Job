"""jobs.admin.ch read from its public API: no browser, and nothing invented."""

import asyncio
import json

import httpx
import pytest


def row(
    i=1,
    *,
    title="Intelligence Specialist",
    unit="VBS",
    place="Bern, Schweiz",
    home_office="false",
    pensum=("80", "100"),
    link=None,
    start="2026-09-24T08:09:27Z",
    funktion="Fachfunktion",
):
    identity = 10201990 + i
    return {
        "id": identity,
        "title": title,
        "attributes": {
            "arbeitsort": [place] if place else [],
            "verwaltungseinheit": [unit] if unit else [],
            "funktion": [funktion] if funktion else [],
            "125": [home_office],
        },
        "szas": {
            "sza_title": title,
            "sza_tasks": "<ul><li>Recherchieren</li></ul>",
            "sza_requirements": "<ul><li>Hochschulabschluss</li></ul>",
            "sza_pensum.min": pensum[0],
            "sza_pensum.max": pensum[1],
            "sza_location.city": place,
        },
        "links": {
            "directlink": link
            if link is not None
            else f"https://jobs.admin.ch/offene-stellen/role-{i}/uuid-{i}"
        },
        "start_date": start,
        "end_date": "2026-10-23T21:59:59Z",
        "language": "de",
    }


def body(rows, *, total=None, offset=0):
    return json.dumps(
        {
            "medium_id": 1000624,
            "offset": offset,
            "total": total if total is not None else len(rows),
            "jobs": rows,
        }
    )


def fetch(pages, *, params=None, monkeypatch=None):
    from jobhunt_core.harvest.providers import native_stelle_admin as module

    if monkeypatch:
        monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
        monkeypatch.setattr(module, "RETRY_BACKOFF_S", 0)
    requests = []

    async def run():
        def transport(request):
            offset = int(request.url.params.get("offset", 0))
            requests.append((offset, request.url.params.get("limit")))
            page = pages.get(offset, pages.get("default"))
            if page is None:
                return httpx.Response(200, text=body([], total=0, offset=offset))
            if isinstance(page, int):
                return httpx.Response(page, text="nope")
            return httpx.Response(200, text=page)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            result = await module.StelleAdminProvider().fetch_new(
                {} if params is None else params, None, client
            )
        return result, requests

    return asyncio.run(run())


def test_the_api_gives_what_the_browser_scraper_left_empty(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, requests = fetch({0: body([row()])}, monkeypatch=monkeypatch)

    assert result.complete and result.error is None and len(result.listings) == 1
    assert requests == [(0, "100")]
    listing = result.listings[0]
    assert listing.external_id == "id:10201991"
    assert listing.url == "https://jobs.admin.ch/offene-stellen/role-1/uuid-1"
    assert normalize_offer("stelle_admin", listing.payload) == {
        "title": "Intelligence Specialist",
        # The real hiring unit, not the scraper's constant.
        "company": "VBS",
        "description": "Recherchieren Hochschulabschluss",
        "location": "Bern, Schweiz",
        "canton": "BE",
        "remote": False,
        "tags": [],
        "salary": None,
        "employment_type": "80 - 100%",
        "prof_group": "Fachfunktion",
    }


def test_the_home_office_attribute_becomes_remote(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, _requests = fetch(
        {0: body([row(home_office="true")])}, monkeypatch=monkeypatch
    )

    assert normalize_offer("stelle_admin", result.listings[0].payload)["remote"] is True


def test_a_single_workload_is_not_printed_as_a_range(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, _requests = fetch(
        {0: body([row(pensum=("100", "100"))])}, monkeypatch=monkeypatch
    )

    assert (
        normalize_offer("stelle_admin", result.listings[0].payload)["employment_type"]
        == "100%"
    )


def test_the_portal_date_reaches_admission():
    from datetime import datetime, timezone

    from jobhunt_core.harvest.admission import publication_date

    assert publication_date(
        "stelle_admin", {"start_date": "2026-09-24T08:09:27Z"}
    ) == datetime(2026, 9, 24, 8, 9, 27, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "link",
    [
        "",
        "https://evil.test/offene-stellen/x/y",
        "https://jobs.admin.ch",
        "https://user@jobs.admin.ch/offene-stellen/x/y",
        "javascript:/offene-stellen/x/y",
        "https://jobs.admin.ch\\@evil.test/x",
    ],
)
def test_a_link_that_is_not_the_portals_own_is_refused(link, monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="no usable identities"):
        fetch({0: body([row(link=link)])}, monkeypatch=monkeypatch)


def test_a_subdomain_link_is_accepted():
    from jobhunt_core.harvest.providers.native_stelle_admin import _url

    assert _url(
        {"links": {"directlink": "https://www.jobs.admin.ch/offene-stellen/a/b"}}
    )


def test_rows_without_id_or_title_are_reported(monkeypatch):
    rows = [row(1), row(2, title=""), {"title": "x"}, "nope"]
    rows[1]["szas"]["sza_title"] = ""
    result, _requests = fetch({0: body(rows)}, monkeypatch=monkeypatch)

    assert [listing.external_id for listing in result.listings] == ["id:10201991"]
    assert not result.complete and result.error == "invalid_stelle_admin_rows"


def test_the_offsets_follow_the_announced_total(monkeypatch):
    pages = {
        0: body([row(i) for i in range(1, 4)], total=5, offset=0),
        100: body([row(i) for i in range(4, 6)], total=5, offset=100),
    }
    result, requests = fetch(pages, monkeypatch=monkeypatch)

    # The first page already covers the announced total, so there is no second.
    assert [offset for offset, _limit in requests] == [0]
    assert len(result.listings) == 3
    assert result.next_cursor["announced"] == 5


def test_a_full_page_asks_for_the_next_offset(monkeypatch):
    from jobhunt_core.harvest.providers import native_stelle_admin as module

    monkeypatch.setattr(module, "PAGE_SIZE", 2)
    pages = {
        0: body([row(1), row(2)], total=3, offset=0),
        2: body([row(3)], total=3, offset=2),
    }
    result, requests = fetch(pages, monkeypatch=monkeypatch)

    assert [offset for offset, _limit in requests] == [0, 2]
    assert len(result.listings) == 3
    assert result.complete


def test_duplicate_ids_across_pages_are_one_offer(monkeypatch):
    from jobhunt_core.harvest.providers import native_stelle_admin as module

    monkeypatch.setattr(module, "PAGE_SIZE", 2)
    pages = {
        0: body([row(1), row(2)], total=4, offset=0),
        2: body([row(2), row(3)], total=4, offset=2),
    }
    result, _requests = fetch(pages, monkeypatch=monkeypatch)

    assert sorted(listing.external_id for listing in result.listings) == [
        "id:10201991",
        "id:10201992",
        "id:10201993",
    ]


def test_an_empty_api_is_a_visible_failure(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="no offers"):
        fetch({0: body([], total=0)}, monkeypatch=monkeypatch)


@pytest.mark.parametrize("payload", ["{not json", "[1,2]", '{"total": 1}'])
def test_a_response_that_is_not_the_contract_is_a_visible_failure(payload, monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError):
        fetch({0: payload}, monkeypatch=monkeypatch)


def test_an_invalid_scope_fails_before_the_network(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderConfigError

    with pytest.raises(ProviderConfigError):
        fetch({}, params={"lang": "fr"}, monkeypatch=monkeypatch)


def test_a_transient_server_error_is_retried(monkeypatch):
    from jobhunt_core.harvest.providers import native_stelle_admin as module

    monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
    monkeypatch.setattr(module, "RETRY_BACKOFF_S", 0)
    calls = {"n": 0}

    async def run():
        def transport(_request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, text="busy")
            return httpx.Response(200, text=body([row()]))

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            return await module._read(client, 0)

    assert asyncio.run(run())["total"] == 1
    assert calls["n"] == 2
