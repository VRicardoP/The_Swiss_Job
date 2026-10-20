"""myScience keeps its five-page sweep, its selectors and its detail pass."""

import asyncio

import httpx
import pytest


def record(
    i=1, *, title=None, href=None, date="2026-10-14", location="Zürich", logo=True
):
    link = f'<a href="{href if href is not None else f"/jobs/id7045{i}-role-{i}-eth"}" itemprop="url">'
    image = (
        '<div class="results_logo"><div class="centered_logo">'
        '<img src="/var/myscience/image/logo/ethz.svg"></div></div>'
        if logo
        else ""
    )
    stamp = (
        f'<time datetime="{date}"><span class="st"><b>14 Okt</b></span></time>'
        if date
        else ""
    )
    return f"""
    <div itemprop="itemListElement" itemscope itemtype="https://schema.org/ListItem">
      <meta itemprop="position" content="{i}">
      {link}
        <div class="results">
          <div class="block">
            <div class="results_location_date">
              <span class="results_location"><span class="location">{location}</span></span>
              {stamp}
            </div>
          </div>
          <div class="results_title">
            <span itemprop="name">{"Python Developer" if title is None else title}</span>
          </div>
          <div class="results_organization_department">
            <span class="results_organization">ETH Zürich</span>
            <span class="results_department">Dept</span>
          </div>
          {image}
        </div>
      </a>
    </div>
    """


def listing(body=""):
    return (
        '<html><body><div id="results_table" itemscope '
        'itemtype="https://schema.org/ItemList">' + body + "</div></body></html>"
    )


def detail(description="<p>Build things</p><p>and ship in English</p>", logo=True):
    image = (
        '<div class="centered_logo"><img src="/var/myscience/image/logo/detail.svg"></div>'
        if logo
        else ""
    )
    return (
        '<html><body><div id="middle_content"><div id="results_table">'
        f'{image}<div class="long_value" id="Description">{description}</div>'
        "</div></div></body></html>"
    )


def fetch(pages, *, params=None, monkeypatch=None, details=None, detail_status=200):
    """Run one sweep. `pages` maps page number -> listing html, with a default."""
    from jobhunt_core.harvest.providers import native_myscience as module

    if monkeypatch:
        monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
        monkeypatch.setattr(module, "RETRY_BACKOFF_S", 0)
    requests = []

    async def run():
        def transport(request):
            requests.append(str(request.url))
            if request.url.path == "/jobs":
                number = int(request.url.params.get("p", 1))
                body = pages.get(number, pages.get("default", listing()))
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
            result = await module.MyScienceProvider().fetch_new(
                {} if params is None else params, None, client
            )
        return result, requests

    return asyncio.run(run())


def test_public_fields_identity_and_date_are_preserved(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, requests = fetch({1: listing(record())}, monkeypatch=monkeypatch)

    assert result.complete and result.error is None and len(result.listings) == 1
    # Page 1 has records, page 2 parses to nothing and ends the sweep; then the
    # single detail page.
    assert len(requests) == 3
    listing_row = result.listings[0]
    # Identity is the portal's own id, never the slug that carries the title.
    assert listing_row.external_id == "id:70451"
    assert listing_row.url == "https://www.myscience.ch/jobs/id70451-role-1-eth"
    assert listing_row.payload["date"] == "2026-10-14"
    assert normalize_offer("myscience", listing_row.payload) == {
        "title": "Python Developer",
        "company": "ETH Zürich",
        "description": "Build things\nand ship in English",
        "location": "Zürich",
        "canton": "ZH",
        "remote": False,
        # The producer's own taxonomy (substring matches, no word boundaries).
        "tags": ["english"],
        "salary": None,
        "logo": "https://www.myscience.ch/var/myscience/image/logo/detail.svg",
    }


def test_the_published_date_reaches_admission():
    from datetime import datetime, timezone

    from jobhunt_core.harvest.admission import publication_date

    assert publication_date("myscience", {"date": "2026-10-09"}) == datetime(
        2026, 10, 9, tzinfo=timezone.utc
    )
    assert publication_date("myscience", {"date": ""}) is None


def test_a_card_without_a_link_or_a_portal_id_is_not_an_offer(monkeypatch):
    result, _requests = fetch(
        {
            1: listing(
                record(1) + record(2, href="") + record(3, href="/jobs/other-slug")
            )
        },
        monkeypatch=monkeypatch,
    )

    assert [row.external_id for row in result.listings] == ["id:70451"]
    assert not result.complete and result.error == "invalid_myscience_cards"


def test_pages_are_deduplicated_by_identity(monkeypatch):
    # The portal repeats most of page 1 on the following pages.
    pages = {1: listing(record(1) + record(2)), 2: listing(record(2) + record(3))}
    result, requests = fetch(pages, monkeypatch=monkeypatch)

    assert [row.external_id for row in result.listings] == [
        "id:70451",
        "id:70452",
        "id:70453",
    ]
    assert result.next_cursor == {
        "pages": 3,
        "items_seen": 4,
        "identities": 3,
        "details_fetched": 3,
    }
    assert len([url for url in requests if "/jobs/id" in url]) == 3


def test_a_spent_time_budget_fails_loudly_instead_of_persisting_nothing(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError
    from jobhunt_core.harvest.providers import native_myscience as module

    monkeypatch.setattr(module, "SWEEP_BUDGET_S", 0)
    with pytest.raises(ProviderResponseError, match="no records"):
        fetch({1: listing(record())}, monkeypatch=monkeypatch)


def test_the_detail_ceiling_bounds_the_pass_and_is_reported(monkeypatch):
    from jobhunt_core.harvest.providers import native_myscience as module

    monkeypatch.setattr(module, "MAX_DETAIL_FETCHES", 2)
    result, requests = fetch(
        {1: listing(record(1) + record(2) + record(3))}, monkeypatch=monkeypatch
    )

    assert len(result.listings) == 3
    assert len([url for url in requests if "/jobs/id" in url]) == 2
    assert result.next_cursor["details_fetched"] == 2
    # A truncated pass is never reported as a finished sweep.
    assert result.error == "detail_budget" and not result.complete


def test_the_declared_page_budget_is_the_ceiling(monkeypatch):
    result, requests = fetch(
        {"default": listing(record())},
        params={"max_pages": 2},
        monkeypatch=monkeypatch,
    )

    assert result.complete
    assert len([url for url in requests if "/jobs?" in url]) == 2
    assert result.next_cursor["pages"] == 2


def test_an_invalid_scope_fails_before_the_network(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderConfigError

    with pytest.raises(ProviderConfigError):
        fetch({}, params={"query": "physics"}, monkeypatch=monkeypatch)
    with pytest.raises(ProviderConfigError):
        fetch({}, params={"max_pages": 0}, monkeypatch=monkeypatch)


def test_an_empty_sweep_fails_instead_of_emptying_the_corpus(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="no records"):
        fetch({"default": listing()}, monkeypatch=monkeypatch)


def test_a_listing_full_of_unusable_cards_is_a_failure(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="no usable identities"):
        fetch({1: listing(record(1, href=""))}, monkeypatch=monkeypatch)


def test_a_transient_server_error_is_retried(monkeypatch):
    from jobhunt_core.harvest.providers import native_myscience as module

    monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
    monkeypatch.setattr(module, "RETRY_BACKOFF_S", 0)
    calls = {"n": 0}

    async def run():
        def transport(_request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, text="busy")
            return httpx.Response(200, text=listing(record()))

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            return await module._read(client, "https://www.myscience.ch/jobs", 10_000)

    assert "id70451" in asyncio.run(run())
    assert calls["n"] == 2


def test_a_permanent_client_error_is_not_retried(monkeypatch):
    from jobhunt_core.harvest.providers import native_myscience as module

    monkeypatch.setattr(module, "RETRY_BACKOFF_S", 0)
    calls = {"n": 0}

    async def run():
        def transport(_request):
            calls["n"] += 1
            return httpx.Response(404, text="gone")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            return await module._read(client, "https://www.myscience.ch/jobs", 10_000)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(run())
    assert calls["n"] == 1


def test_a_failing_first_page_raises_instead_of_reporting_zero(monkeypatch):
    with pytest.raises(httpx.HTTPStatusError):
        fetch({1: 503}, monkeypatch=monkeypatch)


def test_a_failing_later_page_keeps_what_was_harvested(monkeypatch):
    result, _requests = fetch({1: listing(record()), 2: 500}, monkeypatch=monkeypatch)

    assert len(result.listings) == 1
    assert not result.complete and result.error == "HTTPStatusError"
    # Un 500 es una avería del portal, no un bloqueo.
    assert result.blocked_with is None


def test_a_late_block_travels_so_the_killswitch_can_see_it(monkeypatch):
    """DT-148 A2: el 429 de la página 2 se preserva Y se cuenta."""
    result, _requests = fetch({1: listing(record()), 2: 429}, monkeypatch=monkeypatch)

    assert len(result.listings) == 1 and not result.complete
    assert result.blocked_with == 429


def test_a_dead_detail_page_does_not_end_the_run(monkeypatch):
    result, _requests = fetch(
        {1: listing(record())}, monkeypatch=monkeypatch, detail_status=404
    )

    assert result.complete and len(result.listings) == 1
    # The listing stub is already a minimal offer; only the description is lost.
    assert result.listings[0].payload.get("description", "") == ""
    assert result.next_cursor["details_fetched"] == 1


def test_the_detail_logo_wins_but_the_listing_logo_survives_alone(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, _requests = fetch(
        {1: listing(record())}, monkeypatch=monkeypatch, details=detail(logo=False)
    )

    content = normalize_offer("myscience", result.listings[0].payload)
    assert (
        content["logo"] == "https://www.myscience.ch/var/myscience/image/logo/ethz.svg"
    )


def test_a_card_without_a_date_or_a_location_degrades_but_persists(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, _requests = fetch(
        {1: listing(record(date="", location=""))}, monkeypatch=monkeypatch
    )

    payload = result.listings[0].payload
    assert "date" not in payload
    content = normalize_offer("myscience", payload)
    assert content["location"] == "Switzerland" and "canton" not in content


def test_a_non_iso_date_is_dropped_rather_than_guessed(monkeypatch):
    result, _requests = fetch(
        {1: listing(record(date="14 Okt 2026"))}, monkeypatch=monkeypatch
    )

    assert "date" not in result.listings[0].payload


def test_the_listing_parser_reproduces_the_scraper_selectors():
    from jobhunt_core.harvest.providers.native_myscience import _records

    # A card outside #results_table is not a result; one inside with no title
    # is not an offer.
    html = '<div itemscope><div class="results_title">outside</div></div>' + listing(
        record(1, location="Bern / Z&uuml;rich")
        + '<div itemscope><a href="/jobs/id1-x"></a></div>'
    )
    stubs = _records(html)

    assert [stub["id"] for stub in stubs] == ["70451"]
    assert stubs[0]["company"] == "ETH Zürich"
    assert stubs[0]["location"] == "Bern / Zürich"
    assert stubs[0]["logo"].endswith("/ethz.svg")


def test_the_detail_parser_keeps_paragraphs_and_ignores_scripts():
    from jobhunt_core.harvest.providers.native_myscience import _detail

    html = detail(
        "<p>one</p><script>var hidden = 1;</script><p>two<p>three", logo=False
    )

    assert _detail(html) == {"description": "one\ntwo\nthree"}


def test_the_response_byte_budget_fails_closed(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError
    from jobhunt_core.harvest.providers import native_myscience as module

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"1234")
            )
        ) as client:
            return await module._read(client, "https://www.myscience.ch/jobs", 3)

    with pytest.raises(ProviderResponseError, match="byte budget"):
        asyncio.run(run())
