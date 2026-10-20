"""DynamiteJobs keeps one category sweep and bounded detail enrichment."""

import asyncio
import json

import httpx
import pytest


def row(i=1, **changes):
    value = {
        "id": f"job-{i}",
        "title": f"Remote role {i}",
        "company": {"name": "ACME", "username": "acme"},
        "slug": f"remote-role-{i}",
        "status": "published",
        "flags": {"isDeleted": False, "isPageVisible": True},
        "expiresAt": 4_102_444_800_000,
        "publishedAt": 1_789_750_928_314 + i,
        "locationSlugs": ["latinamerica", None],
        "skills": [{"name": f"Skill {number}"} for number in range(12)],
        "salary": {
            "public": True,
            "type": "monthly",
            "from": 2500,
            "to": 3000,
            "currency": "USD",
        },
        "type": {"name": {"display": "Full Time"}},
    }
    value.update(changes)
    return value


def page(rows):
    state = json.dumps({"categoryJobs": {"jobs": rows}})
    return f"<script>window.__PINIA_STATE__={json.dumps(state)}</script>"


def detail(description="<p>Build <strong>APIs</strong> and ship.</p>"):
    payload = json.dumps({"@type": "JobPosting", "description": description})
    return f'<script type="application/ld+json">{payload}</script>'


def fetch(rows, *, cursor=None, params=None, monkeypatch=None, every_category=False):
    from jobhunt_core.harvest.providers import native_dynamitejobs as module

    if monkeypatch:
        monkeypatch.setattr(module, "REQUEST_PAUSE_S", 0)
    requests = []

    async def run():
        def transport(request):
            requests.append(request.url.path)
            if request.url.path.startswith("/category/"):
                body = rows if every_category or len(requests) == 1 else []
                return httpx.Response(200, text=page(body))
            return httpx.Response(200, text=detail())

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            result = await module.DynamiteJobsProvider().fetch_new(
                {} if params is None else params, cursor, client
            )
        return result, requests

    return asyncio.run(run())


def test_public_fields_null_location_and_detail_are_preserved(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, requests = fetch([row()], monkeypatch=monkeypatch)

    assert result.complete and result.error is None and len(result.listings) == 1
    assert len(requests) == 21
    listing = result.listings[0]
    assert listing.external_id == "job-1"
    assert (
        listing.url == "https://dynamitejobs.com/company/acme/remote-job/remote-role-1"
    )
    assert listing.payload["_core_statistics_location"] == "Latin America"
    assert listing.payload["_core_statistics_tags"] == [
        f"skill {number}" for number in range(12)
    ]
    assert result.next_cursor["descriptions"] == {"job-1": "Build APIs and ship."}
    assert normalize_offer("dynamitejobs", listing.payload) == {
        "title": "Remote role 1",
        "company": "ACME",
        "description": "Build APIs and ship.",
        "tags": [f"Skill {number}" for number in range(10)],
        "salary": None,
        "location": "Latin America",
        "remote": True,
        "salary_min": 30000,
        "salary_max": 36000,
        "salary_currency": "USD",
        "employment_type": "Full Time",
    }


def test_cold_cursor_fetches_at_most_twenty_details(monkeypatch):
    result, requests = fetch([row(i) for i in range(1, 22)], monkeypatch=monkeypatch)

    assert result.complete and len(result.listings) == 21
    assert len(requests) == 40
    assert result.next_cursor["detail_attempts"] == 20
    assert result.next_cursor["details_fetched"] == 20
    assert len(result.next_cursor["descriptions"]) == 20
    assert result.listings[-1].payload["_core_description"] == ""


def test_categories_are_globally_deduplicated(monkeypatch):
    result, requests = fetch(
        [row()],
        cursor={"descriptions": {"job-1": "Cached"}},
        monkeypatch=monkeypatch,
        every_category=True,
    )

    assert len(result.listings) == 1
    assert len(requests) == 20
    assert result.next_cursor["items_seen"] == 1
    assert result.listings[0].payload["_core_description"] == "Cached"


def test_only_legacy_first_three_hundred_receive_recent_order(monkeypatch):
    rows = [row(i) for i in range(1, 303)]
    result, _requests = fetch(
        rows,
        cursor={"descriptions": {_id: "Cached" for _id in ("job-1", "job-301")}},
        monkeypatch=monkeypatch,
    )
    payloads = {listing.external_id: listing.payload for listing in result.listings}

    assert len(result.listings) == 302
    assert result.next_cursor["recent_items"] == 300
    assert payloads["job-300"]["_core_order"] == 0
    assert payloads["job-1"]["_core_order"] == 299
    assert payloads["job-301"]["_core_order"] == 300
    assert result.next_cursor["descriptions"]["job-1"] == "Cached"
    assert "job-301" not in result.next_cursor["descriptions"]


def test_invalid_row_is_counted_and_private_salary_is_absent(monkeypatch):
    result, _requests = fetch(
        [row(salary={"public": False, "from": 1}), None],
        cursor={"descriptions": {"job-1": "Cached"}},
        monkeypatch=monkeypatch,
    )

    # DT-148 E1: un item ilegible viaja como recuento, no como error.
    assert result.complete and result.error is None
    assert result.next_cursor["invalid"] == 1
    content = result.listings[0].payload
    assert content["salary"]["from"] == 1
    from jobhunt_core.harvest.normalize import normalize_offer

    normalized = normalize_offer("dynamitejobs", content)
    assert "salary_min" not in normalized and "salary_currency" not in normalized


@pytest.mark.parametrize(
    "cursor",
    [
        [],
        {"descriptions": []},
        {"descriptions": {"job-1": ""}},
        {"descriptions": {1: "x"}},
    ],
)
def test_corrupt_description_cursor_is_discarded_not_fatal(cursor, monkeypatch, caplog):
    """DT-148 A18: el cursor es CACHÉ nuestra, no configuración del scope.

    Tratarlo como permanente dejaba el scope fallando en CADA ventana sin
    contabilizar el fallo —el runner relanza `ProviderConfigError` antes de
    registrarlo—, el dispatcher lo reencolaba siempre y no existía vía de
    operador para reiniciarlo. Se descarta con un grito y se vuelve a pedir
    el detalle: una ventana más lenta, no una avería permanente.
    """
    with caplog.at_level("ERROR"):
        result, _requests = fetch([row()], cursor=cursor, monkeypatch=monkeypatch)

    assert result.listings
    assert any("ilegible" in mensaje for mensaje in caplog.messages)


def test_invalid_scope_fails_before_network(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderConfigError

    with pytest.raises(ProviderConfigError):
        fetch([], params={"category": "development"}, monkeypatch=monkeypatch)


def test_published_at_uses_the_declared_millisecond_epoch():
    from datetime import datetime, timezone

    from jobhunt_core.harvest.admission import publication_date

    raw = row()
    raw["_core_published_at"] = "2026-09-18T20:22:08.315000+00:00"
    assert publication_date("dynamitejobs", raw) == datetime(
        2026, 9, 18, 20, 22, 8, 315000, tzinfo=timezone.utc
    )


def test_description_parser_supports_json_ld_graph():
    from jobhunt_core.harvest.providers.native_dynamitejobs import _description

    payload = json.dumps(
        {"@graph": [{"@type": ["Thing", "JobPosting"], "description": "<b>x</b>"}]}
    )
    html = f"<script data-x='1' type='application/ld+json'>{payload}</script>"
    assert _description(html) == "x"
    assert _description("<html>no structured job</html>") == ""


def test_invalid_embedded_state_is_retried(monkeypatch):
    from jobhunt_core.harvest.providers import native_dynamitejobs as module

    monkeypatch.setattr(module, "MAX_RETRIES", 1)
    calls = 0

    async def run():
        nonlocal calls

        def transport(_request):
            nonlocal calls
            calls += 1
            return httpx.Response(200, text="invalid" if calls == 1 else page([]))

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            return await module._read(
                client,
                "https://dynamitejobs.com/category/x",
                10_000,
                module._category_jobs,
            )

    assert asyncio.run(run()) == []
    assert calls == 2


def test_response_byte_budget_and_long_identity_fail_closed(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError
    from jobhunt_core.harvest.providers import native_dynamitejobs as module

    monkeypatch.setattr(module, "MAX_RETRIES", 0)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"1234")
            )
        ) as client:
            return await module._read(client, "https://dynamitejobs.com/x", 3)

    with pytest.raises(ProviderResponseError, match="byte budget"):
        asyncio.run(run())
    assert module._identity({"id": "x" * 201}) == ""
