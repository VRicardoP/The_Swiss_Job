"""TES keeps its embedded payload, and says out loud what the portal hides."""

import asyncio
import json

import httpx
import pytest


def job(
    i=1,
    *,
    title="Teacher of Physics",
    employer="Aiglon College",
    logo=True,
    salary=None,
    start="2026-10-11T22:00:00.000Z",
    location="Leysin, Switzerland",
    contract_types=("Full Time",),
    contract_terms=("Permanent",),
    url=None,
):
    images = {"logo": "https://l.imgt.es/logo.jpg"} if logo else {}
    return {
        "id": 2351630 + i,
        "title": title,
        "employer": {"name": employer, "images": images},
        "canonicalUrl": url
        if url is not None
        else f"/jobs/vacancy/role-{i}-switzerland-{2351630 + i}",
        "shortDescription": "<p>Teach <b>physics</b></p>",
        "displayLocation": location,
        "contractTerms": list(contract_terms),
        "contractTypes": list(contract_types),
        "advert": {"startDate": start, "endDate": "2026-10-22T22:00:00.000Z"},
        "salary": {"range": salary},
    }


def page(jobs, *, num_found=None, current=1, total_pages=1, shape="initialData"):
    payload = {
        "jobs": jobs,
        "numFound": num_found if num_found is not None else len(jobs),
        "currentPage": current,
        "totalPages": total_pages,
    }
    if shape == "initialData":
        props = {"pageProps": {"initialData": payload}}
    elif shape == "trpc":
        props = {
            "pageProps": {
                "trpcState": {"json": {"queries": [{"state": {"data": payload}}]}}
            }
        }
    else:
        props = {"pageProps": {}}
    body = json.dumps({"props": props})
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{body}</script></body></html>'


def fetch(pages, *, params=None, monkeypatch=None):
    from jobhunt_core.harvest.providers import native_tes as module

    if monkeypatch:
        monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
        monkeypatch.setattr(module, "RETRY_BACKOFF_S", 0)
    requests = []

    async def run():
        def transport(request):
            number = int(request.url.params.get("page", 1))
            requests.append(number)
            body = pages.get(number, pages.get("default"))
            if body is None:
                return httpx.Response(404, text="gone")
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


def test_public_fields_identity_and_date_are_preserved(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, requests = fetch(
        {1: page([job(1, salary="CHF 90,000 -\xa0100,000")])}, monkeypatch=monkeypatch
    )

    assert result.complete and result.error is None and len(result.listings) == 1
    assert requests == [1]
    listing = result.listings[0]
    assert listing.external_id == "id:2351631"
    assert listing.url == (
        "https://www.tes.com/jobs/vacancy/role-1-switzerland-2351631"
    )
    assert normalize_offer("tes", listing.payload) == {
        "title": "Teacher of Physics",
        "company": "Aiglon College",
        "description": "Teach physics",
        "location": "Leysin, Switzerland",
        "remote": False,
        "tags": [],
        # A free-text range travels as text: no amount is invented, and the
        # non-breaking space the portal emits is normalised.
        "salary": "CHF 90,000 - 100,000",
        "employment_type": "Full Time",
        "contract_type": "Permanent",
        "logo": "https://l.imgt.es/logo.jpg",
    }


def test_the_advert_date_reaches_admission():
    from datetime import datetime, timezone

    from jobhunt_core.harvest.admission import publication_date

    assert publication_date(
        "tes", {"advert": {"startDate": "2026-10-11T22:00:00.000Z"}}
    ) == datetime(2026, 10, 11, 22, 0, tzinfo=timezone.utc)
    assert publication_date("tes", {"advert": {}}) is None


def test_the_canton_travels_when_the_location_names_one(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, _requests = fetch(
        {1: page([job(1, location="St. Gallen, Switzerland")])}, monkeypatch=monkeypatch
    )

    assert normalize_offer("tes", result.listings[0].payload)["canton"] == "SG"


def test_the_trpc_cache_is_read_when_initialdata_is_gone(monkeypatch):
    """La forma que lee el scraper sigue entendiéndose si el portal vuelve a ella."""
    result, _requests = fetch({1: page([job()], shape="trpc")}, monkeypatch=monkeypatch)

    assert len(result.listings) == 1


def test_a_payload_without_jobs_is_a_visible_failure(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="no jobs payload"):
        fetch({1: page([], shape="none")}, monkeypatch=monkeypatch)


@pytest.mark.parametrize(
    "body",
    [
        "<html><body>no next data</body></html>",
        '<html><script id="__NEXT_DATA__">{not json</script></html>',
        '<html><script id="__NEXT_DATA__">[1,2]</script></html>',
    ],
)
def test_an_unreadable_payload_is_a_visible_failure(body, monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="__NEXT_DATA__"):
        fetch({1: body}, monkeypatch=monkeypatch)


def test_an_empty_listing_is_a_visible_failure(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="no offers"):
        fetch({1: page([])}, monkeypatch=monkeypatch)


def test_a_row_without_id_title_or_url_is_reported(monkeypatch):
    rows = [job(1), job(2, title=""), job(3, url="https://evil.test/x"), {"id": 4}]
    result, _requests = fetch({1: page(rows)}, monkeypatch=monkeypatch)

    assert [row.external_id for row in result.listings] == ["id:2351631"]
    assert not result.complete and result.error == "invalid_tes_rows"


def test_the_ignored_page_parameter_is_measured_not_hidden(monkeypatch, caplog):
    """LA COTA DEL PORTAL, MEDIDA: `?page=N` se ignora en servidor.

    La página 2 devuelve los datos de la 1 (`currentPage: 1`). El barrido lo
    detecta, para, y CUENTA las que el portal anuncia y no expone — en vez de
    perderlas en silencio como hacía el scraper.
    """
    first = page([job(i) for i in range(1, 4)], num_found=11, current=1, total_pages=2)
    result, requests = fetch({"default": first}, monkeypatch=monkeypatch)

    assert requests == [1, 2]
    assert len(result.listings) == 3
    assert result.next_cursor == {
        "pages": 2,
        "announced": 11,
        "harvested": 3,
        "shortfall": 8,
        "pagination_ignored": True,
    }
    # Una limitación permanente del portal no debe congelar last_complete_at.
    assert result.complete and result.error is None
    assert any("announces 11 offers" in m for m in caplog.messages)


def test_real_pagination_is_followed_when_the_portal_honours_it(monkeypatch):
    pages = {
        1: page([job(1), job(2)], num_found=4, current=1, total_pages=2),
        2: page([job(3), job(4)], num_found=4, current=2, total_pages=2),
    }
    result, requests = fetch(pages, monkeypatch=monkeypatch)

    assert requests == [1, 2]
    assert len(result.listings) == 4
    assert result.next_cursor["shortfall"] == 0
    assert result.complete


def test_an_invalid_scope_fails_before_the_network(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderConfigError

    with pytest.raises(ProviderConfigError):
        fetch({}, params={"keywords": "physics"}, monkeypatch=monkeypatch)


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
            return await module._read(client, 1)

    with pytest.raises(ProviderResponseError, match="byte budget"):
        asyncio.run(run())
