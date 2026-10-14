"""Gastrojob keeps its AJAX sweep, its five-way verdict and its identity rules."""

import asyncio

import httpx
import pytest

COUNT = 1027


def ad(
    i=1,
    *,
    href=None,
    title="Küchenchef:in",
    workload="Vollzeit",
    company="Hotel X",
    canton="Bern",
    stamp="14.10.2026 (07:53)",
):
    link = f"/stellen/stelleninserat/5618{i}" if href is None else href
    description = f"{workload} bei {company} in {canton}" if company else "anonym"
    hidden = f"Erstmals aktiviert: {stamp}" if stamp else ""
    return f"""
    <a href="{link}">
      <article class="row column">
        <h2>{title}</h2>
        <div class="description">{description}</div>
        <div class="hidden-jossen">{hidden}</div>
      </article>
    </a>
    """


def fragment(body="", *, count=COUNT, partner=False):
    counter = (
        f'<div data-mxn-advertisements-count="{count}"></div>'
        if count is not None
        else ""
    )
    ads = body
    if partner:
        ads += '<a href="/stellen/externe-partner/99"><h2>Partner</h2></a>'
    return f"<div>{counter}{ads}</div>"


def detail(
    description="<p>Arbeitsort: Bern</p><p>Über uns</p>",
    *,
    date="2026-10-14",
    locality="Bern",
    section=True,
):
    if not section:
        return "<html><body><p>nothing</p></body></html>"
    stamp = f'<meta itemprop="datePosted" content="{date}" />' if date else ""
    place = (
        '<div itemprop="jobLocation" itemscope itemtype="http://schema.org/Place">'
        f'<meta itemprop="addressLocality" content="{locality}" /></div>'
        if locality
        else ""
    )
    return (
        '<html><body><section class="ad-detail" '
        'itemtype="http://schema.org/JobPosting">'
        f'{stamp}{place}<div itemprop="description">{description}</div>'
        "</section></body></html>"
    )


def fetch(pages, *, params=None, monkeypatch=None, details=None, detail_status=200):
    from jobhunt_core.harvest.providers import native_gastrojob as module

    if monkeypatch:
        monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
        monkeypatch.setattr(module, "RETRY_BACKOFF_S", 0)
    requests = []

    async def run():
        def transport(request):
            url = str(request.url)
            requests.append(url)
            if "tx_mxngastrojob_ajax" in url:
                page = int(
                    request.url.params["tx_mxngastrojob_ajax[@widget_0][currentPage]"]
                )
                body = pages.get(page, pages.get("default", fragment()))
                if isinstance(body, int):
                    return httpx.Response(body, text="nope")
                return httpx.Response(200, text=body)
            if detail_status != 200:
                return httpx.Response(detail_status, text="gone")
            return httpx.Response(
                200, text=details if details is not None else detail()
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            result = await module.GastrojobProvider().fetch_new(
                {} if params is None else params, None, client
            )
        return result, requests

    return asyncio.run(run())


def test_public_fields_identity_and_swiss_date_are_preserved(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer
    from jobhunt_core.harvest.providers import native_gastrojob as module

    # 10 announced offers mean one page, so page 2 is a legitimate end.
    result, requests = fetch(
        {1: fragment(ad(), count=10), "default": fragment(count=10)},
        monkeypatch=monkeypatch,
    )

    assert result.complete and result.error is None and len(result.listings) == 1
    listing = result.listings[0]
    assert listing.external_id == "id:56181"
    assert listing.url == "https://www.gastrojob.ch/stellen/stelleninserat/56181"
    # The portal's civil time, anchored to Zurich instead of assumed UTC.
    assert listing.payload["_core_published_at"] == "2026-10-14T07:53:00+02:00"
    assert normalize_offer("gastrojob", listing.payload) == {
        "title": "Küchenchef:in",
        "company": "Hotel X",
        # Listing canton plus detail locality, so the canton helper still sees it.
        "location": "Bern, Bern",
        "canton": "BE",
        "description": "Arbeitsort: Bern Über uns",
        "remote": False,
        "tags": [],
        "salary": None,
        "employment_type": "Vollzeit",
    }
    # The endpoint expects the Referer its own frontend sends.
    assert module.HEADERS["Referer"] == "https://www.gastrojob.ch/stellen"


def test_the_swiss_date_reaches_admission():
    from datetime import datetime, timezone

    from jobhunt_core.harvest.admission import publication_date

    assert publication_date(
        "gastrojob", {"_core_published_at": "2026-10-14T07:53:00+02:00"}
    ) == datetime(2026, 10, 14, 5, 53, tzinfo=timezone.utc)


def test_an_impossible_stamp_degrades_only_the_date(monkeypatch):
    result, _requests = fetch(
        {1: fragment(ad(stamp="32.13.2026 (99:99)"))},
        monkeypatch=monkeypatch,
        details=detail(date=""),
    )

    assert len(result.listings) == 1
    assert "_core_published_at" not in result.listings[0].payload


def test_the_detail_date_is_only_a_fallback(monkeypatch):
    result, _requests = fetch(
        {1: fragment(ad(stamp=""))}, monkeypatch=monkeypatch, details=detail()
    )

    assert result.listings[0].payload["_core_published_at"] == "2026-10-14"


def test_an_anonymised_ad_stays_unknown_even_if_the_detail_knows(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, _requests = fetch({1: fragment(ad(company=""))}, monkeypatch=monkeypatch)

    # The company is identity and only the listing is stable run to run.
    assert normalize_offer("gastrojob", result.listings[0].payload)["company"] == (
        "Unknown"
    )


def test_a_company_whose_name_contains_in_survives(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, _requests = fetch(
        {1: fragment(ad(company="Restaurant in der Mühle"))}, monkeypatch=monkeypatch
    )
    content = normalize_offer("gastrojob", result.listings[0].payload)

    assert content["company"] == "Restaurant in der Mühle"
    assert content["location"] == "Bern, Bern"


def test_partner_ads_are_never_harvested(monkeypatch):
    result, _requests = fetch(
        {1: fragment(ad(), partner=True)}, monkeypatch=monkeypatch
    )

    assert [row.external_id for row in result.listings] == ["id:56181"]


@pytest.mark.parametrize(
    "href",
    [
        "/stellen/externe-partner/55",
        "/stellen/stelleninserat/",
        "/stellen/stelleninserat/٢١",
        "//evil.com/stellen/stelleninserat/1",
        "https://evil.com/stellen/stelleninserat/1",
        "https://user@www.gastrojob.ch/stellen/stelleninserat/1",
        "javascript:/stellen/stelleninserat/1",
        "/stellen/stelleninserat/1\\@evil.com",
    ],
)
def test_an_href_that_is_not_a_canonical_offer_is_rejected(href):
    from jobhunt_core.harvest.providers.native_gastrojob import _resolve_job_url

    assert _resolve_job_url(href) is None


def test_the_canonical_url_is_rebuilt_not_echoed():
    """Scheme, host and path only: a tracking query or a different case would
    otherwise be one more stored row per offer."""
    from jobhunt_core.harvest.providers.native_gastrojob import _resolve_job_url

    canonical = ("https://www.gastrojob.ch/stellen/stelleninserat/42", "42")
    assert _resolve_job_url("https://WWW.Gastrojob.CH/stellen/stelleninserat/42") == (
        canonical
    )
    assert _resolve_job_url("/stellen/stelleninserat/42?tracking=x#top") == canonical
    assert _resolve_job_url(
        "https://jobs.gastrojob.ch:443/stellen/stelleninserat/42"
    ) == (canonical)


def test_a_fragment_without_a_counter_is_a_visible_failure(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="without div"):
        fetch({"default": fragment(count=None)}, monkeypatch=monkeypatch)


@pytest.mark.parametrize("count", ["", "-20", "+20", " 20 ", "١٠٤", "1_0"])
def test_an_unreadable_counter_is_a_visible_failure(count, monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="unreadable advertisement"):
        fetch({"default": fragment(count=count)}, monkeypatch=monkeypatch)


def test_own_links_that_do_not_parse_are_a_visible_failure(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    # Links to own offers whose title is gone: unreadable either way, and this
    # verdict comes BEFORE the page-range one.
    with pytest.raises(ProviderResponseError, match="none parses"):
        fetch({"default": fragment(ad(title=""), count=10)}, monkeypatch=monkeypatch)


def test_beyond_the_last_announced_page_is_a_legitimate_end(monkeypatch):
    # 10 announced offers mean one page: an empty page 2 ends the sweep.
    result, requests = fetch(
        {1: fragment(ad(), count=10), "default": fragment(count=10)},
        monkeypatch=monkeypatch,
    )

    assert result.complete and result.error is None
    assert len([url for url in requests if "tx_mxngastrojob_ajax" in url]) == 2


def test_a_partner_only_page_inside_the_range_is_a_legitimate_empty(monkeypatch):
    result, _requests = fetch(
        {1: fragment(ad()), 2: fragment(partner=True)}, monkeypatch=monkeypatch
    )

    assert result.complete and result.error is None
    assert len(result.listings) == 1


def test_an_empty_page_inside_the_range_is_a_visible_failure(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="none recognisable"):
        fetch({"default": fragment()}, monkeypatch=monkeypatch)


def test_a_structure_change_after_good_pages_keeps_them_and_reports(monkeypatch):
    result, _requests = fetch(
        {1: fragment(ad()), 2: fragment(count=None)}, monkeypatch=monkeypatch
    )

    assert len(result.listings) == 1
    assert not result.complete and result.error == "unknown_gastrojob_structure"


def test_a_zero_counter_never_reports_a_failure(monkeypatch):
    """La vía de rehabilitación: un listado realmente vacío no es un fallo."""
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="no offers"):
        fetch({"default": fragment(count=0)}, monkeypatch=monkeypatch)


def test_a_counter_of_thousands_of_digits_is_unreadable_not_a_crash(monkeypatch):
    """MUERDE sin el guard: int() lanza por el límite de 4300 dígitos de
    CPython y la excepción escapaba del parser (el r2/H4 del legacy)."""
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="unreadable advertisement"):
        fetch({"default": fragment(count="9" * 5000)}, monkeypatch=monkeypatch)


def test_pages_are_deduplicated_by_identity(monkeypatch):
    pages = {1: fragment(ad(1) + ad(2)), 2: fragment(ad(2) + ad(3))}
    result, requests = fetch(pages, monkeypatch=monkeypatch)

    assert [row.external_id for row in result.listings] == [
        "id:56181",
        "id:56182",
        "id:56183",
    ]
    assert len([url for url in requests if "/stelleninserat/" in url]) == 3


def test_the_declared_page_budget_is_the_ceiling(monkeypatch):
    result, requests = fetch(
        {"default": fragment(ad())}, params={"max_pages": 2}, monkeypatch=monkeypatch
    )

    assert result.complete
    assert len([url for url in requests if "tx_mxngastrojob_ajax" in url]) == 2


def test_an_invalid_scope_fails_before_the_network(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderConfigError

    with pytest.raises(ProviderConfigError):
        fetch({}, params={"search": "koch"}, monkeypatch=monkeypatch)


def test_a_dead_detail_page_does_not_end_the_run(monkeypatch):
    result, _requests = fetch(
        {1: fragment(ad(), count=10), "default": fragment(count=10)},
        monkeypatch=monkeypatch,
        detail_status=404,
    )

    assert result.complete and len(result.listings) == 1
    assert result.listings[0].payload.get("description") is None


def test_a_detail_without_the_microdata_section_changes_nothing(monkeypatch):
    from jobhunt_core.harvest.providers.native_gastrojob import _detail

    assert _detail(detail(section=False)) == {}


def test_a_present_but_empty_description_is_kept_as_empty():
    from jobhunt_core.harvest.providers.native_gastrojob import _detail

    assert _detail(detail(description=""))["description"] == ""


def test_the_detail_locality_is_read_from_the_meta_tag():
    from jobhunt_core.harvest.providers.native_gastrojob import _detail

    value = _detail(detail(locality="3818 Grindelwald"))
    assert value["address_locality"] == "3818 Grindelwald"
    assert value["detail_date_posted"] == "2026-10-14"


def test_the_response_byte_budget_fails_closed(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError
    from jobhunt_core.harvest.providers import native_gastrojob as module

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"1234")
            )
        ) as client:
            return await module._read(client, "https://www.gastrojob.ch/", 3)

    with pytest.raises(ProviderResponseError, match="byte budget"):
        asyncio.run(run())
