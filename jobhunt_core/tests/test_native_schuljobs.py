"""schuljobs: the scroll keys, the half-empty details and the safe casts."""

import asyncio
import json

import httpx
import pytest

from jobhunt_core.harvest.providers.native_schuljobs import (
    job_identity,
    parse_detail,
    parse_listing,
    safe_int,
)


def card(i=1, *, title=None, href=None, meta="ZH · Nänikon · INP Spectrum AG"):
    link = (
        href if href is not None else f"https://www.schuljobs.ch/job/role-{i}/J99199{i}"
    )
    return f"""
    <div class="card">
      <h3><a class="js-joboffer-detail" href="{link}">{title if title is not None else f"Klassenlehrperson {i}"}</a></h3>
      <p>{meta}</p>
      <div>14.10.2026 80%</div>
    </div>
    """


def listing(cards="", *, searchhash="19ba6852a4d80ecd", nextpage="2"):
    hash_attr = f'data-searchhash="{searchhash}"' if searchhash is not None else ""
    next_attr = f'data-nextpage="{nextpage}"' if nextpage is not None else ""
    return f"""<html><head><title>Suche</title></head><body>
      <div id="results" {hash_attr}>{cards}</div>
      <button {next_attr}>more</button>
    </body></html>"""


def scroll(cards="", nextpage=None):
    return json.dumps({"html": f"<div>{cards}</div>", "nextpage": nextpage})


def detail(
    *,
    title="Klassenlehrperson 1",
    company="INP Spectrum AG",
    locality="Nänikon",
    region="ZH",
    description="<p>Unterrichten</p>",
    employment="PART_TIME",
    date="2026-10-14",
    logo="https://www.schuljobs.ch/logo.png",
    json_ld=True,
):
    if not json_ld:
        return "<html><body><p>no structured data</p></body></html>"
    payload = {
        "@type": "JobPosting",
        "title": title,
        "hiringOrganization": {"name": company, "logo": logo},
        "jobLocation": {
            "address": {"addressLocality": locality, "addressRegion": region}
        },
        "description": description,
        "employmentType": employment,
        "datePosted": date,
    }
    return (
        '<html><body><script type="application/ld+json">'
        f"{json.dumps(payload)}</script></body></html>"
    )


def fetch(
    *,
    initial,
    scrolls=None,
    details=None,
    params=None,
    monkeypatch=None,
    detail_status=200,
):
    from jobhunt_core.harvest.providers import native_schuljobs as module

    if monkeypatch:
        monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
        monkeypatch.setattr(module, "RETRY_BACKOFF_S", 0)
    scrolls = scrolls or {}
    requests = []

    async def run():
        def transport(request):
            url = str(request.url)
            requests.append(url)
            if url.endswith("/suche"):
                if isinstance(initial, int):
                    return httpx.Response(initial, text="nope")
                return httpx.Response(200, text=initial)
            if "/scroll/" in url:
                assert request.headers.get("x-requested-with") == "XMLHttpRequest"
                page = int(url.rstrip("/").split("/")[-1])
                body = scrolls.get(page)
                if body is None:
                    return httpx.Response(200, text=scroll())
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
            result = await module.SchulJobsProvider().fetch_new(
                {"max_pages": 4} if params is None else params, None, client
            )
        return result, requests

    return asyncio.run(run())


def test_the_listing_and_the_detail_compose_one_offer(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, requests = fetch(initial=listing(card()), monkeypatch=monkeypatch)

    assert result.complete and result.error is None and len(result.listings) == 1
    listing_row = result.listings[0]
    assert listing_row.external_id == "id:J991991"
    assert normalize_offer("schuljobs", listing_row.payload) == {
        "title": "Klassenlehrperson 1",
        "company": "INP Spectrum AG",
        "description": "Unterrichten",
        "location": "Nänikon",
        "canton": "ZH",
        "remote": False,
        "tags": [],
        "salary": None,
        "employment_type": "PART_TIME",
        "logo": "https://www.schuljobs.ch/logo.png",
    }
    assert listing_row.payload["date_posted"] == "2026-10-14"
    # The initial page, one empty scroll page and the detail.
    assert len([url for url in requests if "/job/" in url]) == 1


def test_a_detail_without_json_ld_keeps_the_listing_stub(monkeypatch):
    """Half the detail pages carry no JobPosting (41 of 83 measured): that is
    normal and must not fail the sweep."""
    from jobhunt_core.harvest.normalize import normalize_offer

    result, _requests = fetch(
        initial=listing(card()), details=detail(json_ld=False), monkeypatch=monkeypatch
    )

    content = normalize_offer("schuljobs", result.listings[0].payload)
    assert result.complete
    assert content["title"] == "Klassenlehrperson 1"
    assert content["company"] == "INP Spectrum AG"
    assert content["canton"] == "ZH"
    assert content["description"] is None


def test_a_multi_valued_employment_type_is_rendered_not_lost(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, _requests = fetch(
        initial=listing(card()),
        details=detail(employment=["FULL_TIME", "TEMPORARY"]),
        monkeypatch=monkeypatch,
    )

    # The producer carried the list into a string column and lost it.
    assert (
        normalize_offer("schuljobs", result.listings[0].payload)["employment_type"]
        == "FULL_TIME, TEMPORARY"
    )


def test_the_scroll_pages_follow_the_searchhash(monkeypatch):
    # A FULL page keeps the sweep going; a short one ends it, exactly as the
    # producer's `len(page_stubs) < SCROLL_PAGE_SIZE` does.
    full = "".join(card(i) for i in range(2, 22))
    scrolls = {2: scroll(full, nextpage=3), 3: scroll(card(99), nextpage=4)}
    result, requests = fetch(
        initial=listing(card(1)), scrolls=scrolls, monkeypatch=monkeypatch
    )

    scroll_urls = [url for url in requests if "/scroll/" in url]
    assert scroll_urls[0].endswith("/searchhash/19ba6852a4d80ecd/page/2")
    assert len(scroll_urls) == 2
    assert len(result.listings) == 22


def test_no_searchhash_means_the_first_page_only(monkeypatch, caplog):
    result, requests = fetch(
        initial=listing(card(), searchhash=None), monkeypatch=monkeypatch
    )

    assert not [url for url in requests if "/scroll/" in url]
    assert len(result.listings) == 1 and result.complete
    assert any("no searchhash" in message for message in caplog.messages)


@pytest.mark.parametrize("value", ["", "abc", None, "0", "-3", True, 0])
def test_a_degenerate_next_page_cannot_take_the_sweep_down(value):
    """MUERDE sin el casteo seguro: int("") reventaba el barrido entero y
    perdía los detalles ya descargados."""
    assert safe_int(value) == 0


def test_a_degenerate_next_page_attribute_stops_the_scroll(monkeypatch):
    result, requests = fetch(
        initial=listing(card(), nextpage="abc"), monkeypatch=monkeypatch
    )

    assert not [url for url in requests if "/scroll/" in url]
    assert len(result.listings) == 1 and result.complete


@pytest.mark.parametrize("body", ["{not json", "[1,2]", 500])
def test_a_broken_scroll_page_is_reported_not_silent(body, monkeypatch):
    result, _requests = fetch(
        initial=listing(card()), scrolls={2: body}, monkeypatch=monkeypatch
    )

    # What was harvested survives, and the failure is visible: learning only
    # the first page would leave the scroll pages unfetched forever.
    assert len(result.listings) == 1
    assert not result.complete and result.error is not None


def test_an_empty_fragment_ends_the_sweep_cleanly(monkeypatch):
    result, _requests = fetch(
        initial=listing(card()),
        scrolls={2: scroll("", nextpage=3)},
        monkeypatch=monkeypatch,
    )

    assert len(result.listings) == 1
    assert result.complete and result.error is None


def test_an_empty_listing_is_a_visible_failure(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="no offers"):
        fetch(initial=listing(""), monkeypatch=monkeypatch)


@pytest.mark.parametrize(
    "href",
    [
        "",
        "/not-a-job/J1",
        "https://evil.test/job/role/J1",
        "https://user@www.schuljobs.ch/job/role/J1",
        "javascript:/job/role/J1",
        "https://www.schuljobs.ch/job/role/",
    ],
)
def test_a_link_that_is_not_an_offer_of_this_board_is_refused(href):
    assert job_identity(href) == ""


def test_the_canonical_offer_link_is_accepted():
    assert job_identity("https://www.schuljobs.ch/job/klassenlehrperson/J991993") == (
        "J991993"
    )
    # A tracking query does not change WHICH offer it is: the identity is the
    # portal's id, so the two links are one offer.
    assert job_identity("https://www.schuljobs.ch/job/role/J1?utm=x") == "J1"


def test_links_that_are_not_offers_are_counted_not_crashed(monkeypatch):
    rows = card(1) + card(2, href="/about") + card(3, title="")
    result, _requests = fetch(initial=listing(rows), monkeypatch=monkeypatch)

    assert [row.external_id for row in result.listings] == ["id:J991991"]


def test_the_page_budget_is_declared_by_the_scope(monkeypatch):
    scrolls = {page: scroll(card(page), nextpage=page + 1) for page in range(2, 8)}
    result, requests = fetch(
        initial=listing(card(1)),
        scrolls=scrolls,
        params={"max_pages": 2},
        monkeypatch=monkeypatch,
    )

    assert len([url for url in requests if "/scroll/" in url]) == 1
    assert result.next_cursor["pages"] == 2
    assert len(result.listings) == 2


def test_an_invalid_scope_fails_before_the_network(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderConfigError

    with pytest.raises(ProviderConfigError):
        fetch(
            initial=listing(card()), params={"query": "lehrer"}, monkeypatch=monkeypatch
        )
    with pytest.raises(ProviderConfigError):
        fetch(
            initial=listing(card()), params={"max_pages": 99}, monkeypatch=monkeypatch
        )


def test_the_listing_metadata_is_read_as_canton_city_company():
    rows = parse_listing(listing(card(meta="BE · Bern · Schule X"))).rows()

    assert rows[0]["canton"] == "BE"
    assert rows[0]["location"] == "Bern"
    assert rows[0]["company"] == "Schule X"


def test_a_two_part_metadata_leaves_the_company_unknown():
    rows = parse_listing(listing(card(meta="BE · Bern"))).rows()

    assert rows[0]["canton"] == "BE" and rows[0]["company"] == "Unknown"


def test_a_city_is_never_mistaken_for_a_canton():
    rows = parse_listing(listing(card(meta="Zürich · Zürich · Schule X"))).rows()

    # Only a two-letter head is the canton.
    assert rows[0]["canton"] is None


def test_an_empty_logo_is_not_a_logo():
    assert "logo" not in parse_detail(detail(logo=""))


def test_a_dead_detail_page_does_not_end_the_run(monkeypatch):
    result, _requests = fetch(
        initial=listing(card()), detail_status=404, monkeypatch=monkeypatch
    )

    assert result.complete and len(result.listings) == 1
