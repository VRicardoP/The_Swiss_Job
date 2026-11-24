"""TES is read from its jobs sitemap: Swiss URLs only, identity preserved, failures visible."""

import asyncio

import httpx
import pytest

SWISS = "https://www.tes.com/jobs/vacancy/teacher-of-mathematics-switzerland-2359179"
LASTMOD = "2026-11-11T09:30:00.000Z"


def entry(url=SWISS, lastmod=LASTMOD):
    date = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
    return (
        f"<url>\n    <loc>{url}</loc>\n    {date}\n"
        "    <changefreq>daily</changefreq>\n    <priority>0.7</priority>\n  </url>"
    )


def sitemap(*entries):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n  '
        + "\n  ".join(entries)
        + "\n</urlset>"
    )


def fetch(body, *, params=None, monkeypatch=None):
    from jobhunt_core.harvest.providers import native_tes as module

    if monkeypatch:
        monkeypatch.setattr(module, "RETRY_BACKOFF_S", 0)
    requests = []

    async def run():
        def transport(request):
            requests.append(str(request.url))
            if isinstance(body, int):
                return httpx.Response(body, text="nope")
            return httpx.Response(200, text=body)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            result = await module.TESProvider().fetch_new(
                {} if params is None else params, None, client
            )
        return result, requests

    return asyncio.run(run())


def test_a_swiss_vacancy_keeps_its_identity_url_title_and_date(monkeypatch):
    from jobhunt_core.harvest.admission import publication_date
    from jobhunt_core.harvest.normalize import normalize_offer
    from jobhunt_core.harvest.providers.native_tes import SITEMAP_URL

    result, requests = fetch(sitemap(entry()), monkeypatch=monkeypatch)

    assert requests == [SITEMAP_URL]
    assert result.complete and result.error is None and len(result.listings) == 1
    listing = result.listings[0]
    # The same identity the old listing payload produced: stored offers match.
    assert listing.external_id == "id:2359179"
    assert listing.url == SWISS
    assert normalize_offer("tes", listing.payload) == {
        "title": "Teacher of mathematics",
        "company": "Unknown",
        # strip_html_tags turns an absent description into None.
        "description": None,
        "location": "Switzerland",
        "remote": False,
        "tags": [],
        "salary": None,
    }
    assert (
        publication_date("tes", listing.payload)
        .isoformat()
        .startswith("2026-11-11T09:30")
    )


def test_only_swiss_vacancies_are_kept():
    result, _requests = fetch(
        sitemap(
            entry(),
            entry(
                "https://www.tes.com/jobs/vacancy/teacher-of-english-wiltshire-2355582"
            ),
            entry(
                "https://www.tes.com/jobs/vacancy/switzerland-teacher-wiltshire-2355583"
            ),
            entry("https://evil.test/jobs/vacancy/teacher-switzerland-2355584"),
            entry("https://www.tes.com/jobs/browse/switzerland"),
        )
    )

    assert [row.external_id for row in result.listings] == ["id:2359179"]
    assert result.next_cursor == {
        "sitemap_entries": 5,
        "harvested": 1,
        "items_seen": 1,
        "invalid": 0,
    }


def test_a_slug_with_town_and_school_stays_in_the_title():
    from jobhunt_core.harvest.normalize import normalize_offer

    url = (
        "https://www.tes.com/jobs/vacancy/"
        "summer-camp-group-leader-aiglon-college-chesi-res-switzerland-2360407"
    )
    result, _requests = fetch(sitemap(entry(url)))

    assert normalize_offer("tes", result.listings[0].payload)["title"] == (
        "Summer camp group leader aiglon college chesi res"
    )


def test_a_vacancy_without_lastmod_carries_no_date():
    from jobhunt_core.harvest.admission import publication_date

    result, _requests = fetch(sitemap(entry(lastmod=None)))

    assert "advert" not in result.listings[0].payload
    assert publication_date("tes", result.listings[0].payload) is None


def test_a_repeated_url_is_one_listing():
    result, _requests = fetch(sitemap(entry(), entry()))

    assert len(result.listings) == 1


def test_a_sitemap_without_swiss_vacancies_is_a_complete_empty_harvest():
    result, _requests = fetch(
        sitemap(entry("https://www.tes.com/jobs/vacancy/teacher-wiltshire-2355582"))
    )

    assert result.listings == () and result.complete and result.error is None


@pytest.mark.parametrize(
    "body",
    [
        "<!doctype html><html><head><title>Client Challenge</title></head></html>",
        sitemap(),
    ],
)
def test_a_response_that_is_not_the_sitemap_is_a_visible_failure(body):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="no <url> entries"):
        fetch(body)


def test_the_legacy_page_budget_is_accepted_and_other_params_fail(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderConfigError

    result, requests = fetch(sitemap(entry()), params={"max_pages": 3})
    assert len(result.listings) == 1 and len(requests) == 1

    with pytest.raises(ProviderConfigError):
        fetch(sitemap(entry()), params={"keywords": "physics"})


def test_a_server_error_is_retried_then_raised(monkeypatch):
    with pytest.raises(httpx.HTTPStatusError):
        _result, _requests = fetch(503, monkeypatch=monkeypatch)


def test_a_stored_listing_payload_still_normalises():
    """Rows harvested from the old listing keep normalising with every field."""
    from jobhunt_core.harvest.normalize import normalize_offer

    legacy = {
        "id": 2351631,
        "title": "Teacher of Physics",
        "employer": {"name": "Aiglon College", "images": {"logo": "https://l/x.jpg"}},
        "shortDescription": "<p>Teach <b>physics</b></p>",
        "displayLocation": "St. Gallen, Switzerland",
        "contractTypes": ["Full Time"],
        "contractTerms": ["Permanent"],
        "salary": {"range": "CHF 90,000 -\xa0100,000"},
    }

    assert normalize_offer("tes", legacy) == {
        "title": "Teacher of Physics",
        "company": "Aiglon College",
        "description": "Teach physics",
        "location": "St. Gallen, Switzerland",
        "remote": False,
        "tags": [],
        "salary": "CHF 90,000 - 100,000",
        "canton": "SG",
        "logo": "https://l/x.jpg",
        "employment_type": "Full Time",
        "contract_type": "Permanent",
    }


def test_the_response_byte_budget_fails_closed(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError
    from jobhunt_core.harvest.providers import native_tes as module

    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 3)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"1234")
            )
        ) as client:
            return await module._read(client)

    with pytest.raises(ProviderResponseError, match="byte budget"):
        asyncio.run(run())
