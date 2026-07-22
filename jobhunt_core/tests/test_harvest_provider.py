"""Provider Arbeitnow (A-03): incremental SIN pérdida, con HTTP MOCKEADO.

La auditoría A-03 exigió: (1) el presupuesto de páginas NO puede perder ofertas
(backfill entre runs hasta drenar); (2) los empates de created_at en la
frontera se re-emiten (el sink idempotente deduplica); (3) items sin timestamp
o sin url/slug se saltan sin disparar el corte.
"""

import asyncio
import json

import httpx

from jobhunt_core.harvest.providers.arbeitnow import ArbeitnowProvider

# Tres páginas orden desc por created_at.
PAGES = {
    1: {
        "data": [
            {"slug": "a", "url": "https://x/a", "title": "Python Dev", "created_at": 300, "tags": []},
            {"slug": "b", "url": "https://x/b", "title": "Java Dev", "created_at": 250, "tags": []},
        ],
        "links": {"next": "?page=2"},
    },
    2: {
        "data": [
            {"slug": "c", "url": "https://x/c", "title": "Python Senior", "created_at": 200, "tags": []},
            {"slug": "d", "url": "https://x/d", "title": "QA", "created_at": 100, "tags": []},
        ],
        "links": {"next": "?page=3"},
    },
    3: {"data": [{"slug": "e", "url": "https://x/e", "title": "Old", "created_at": 50, "tags": []}], "links": {}},
}


def _fetch(params=None, cursor=None, pages=PAGES):
    hits: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        hits.append(page)
        return httpx.Response(200, text=json.dumps(pages.get(page, {"data": [], "links": {}})))

    provider = ArbeitnowProvider()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await provider.fetch_new(params or {}, cursor, http)

    return asyncio.run(go()), hits


def _ids(result):
    return [x.external_id for x in result.listings]


def test_budget_cut_never_loses_offers_backfill_resumes():
    """AUDITORÍA #1 (HIGH): presupuesto < backlog → el watermark NO avanza; el
    cursor guarda backfill y el run siguiente DRENA el resto. Unión completa."""
    r1, hits1 = _fetch(params={"max_pages": 2})
    assert _ids(r1) == ["a", "b", "c", "d"]
    assert hits1 == [1, 2]
    # Cortó por presupuesto con backlog → watermark intacto + estado de backfill.
    assert r1.next_cursor == {"watermark": 0, "pending_watermark": 300, "backfill_page": 3}

    # Run 2: reanuda con 1 página de solape (página 2) y termina el drenaje.
    r2, hits2 = _fetch(params={"max_pages": 3}, cursor=r1.next_cursor)
    assert hits2 == [2, 3]
    assert _ids(r2) == ["c", "d", "e"]  # c y d re-emitidos por el solape (idempotente)
    assert r2.next_cursor == {"watermark": 300}  # drenado → consolida el pending
    # UNIÓN de ambos runs = TODO el feed: nada se pierde.
    assert set(_ids(r1)) | set(_ids(r2)) == {"a", "b", "c", "d", "e"}


def test_full_drain_advances_watermark():
    result, hits = _fetch(params={"max_pages": 5})
    assert _ids(result) == ["a", "b", "c", "d", "e"]
    assert result.next_cursor == {"watermark": 300}
    assert hits == [1, 2, 3]


def test_incremental_early_stop_reemits_boundary_tie():
    """AUDITORÍA #2: corte ESTRICTO — los == watermark se re-emiten (dedup en el
    sink idempotente); corta en el primer < watermark."""
    result, hits = _fetch(cursor={"watermark": 200})
    assert _ids(result) == ["a", "b", "c"]  # c (==200) re-emitido, d (100<200) corta
    assert result.next_cursor == {"watermark": 300}
    assert hits == [1, 2]


def test_no_new_items_reemits_only_boundary():
    result, _ = _fetch(cursor={"watermark": 300})
    assert _ids(result) == ["a"]  # a (==300) re-emitido; b (250<300) corta
    assert result.next_cursor == {"watermark": 300}


def test_missing_created_at_is_skipped_not_cross(caplog):
    pages = {
        1: {
            "data": [
                {"slug": "a", "url": "https://x/a", "title": "T", "created_at": 300, "tags": []},
                {"slug": "sin-ts", "url": "https://x/s", "title": "SinTS", "tags": []},
                {"slug": "b", "url": "https://x/b", "title": "T", "created_at": 250, "tags": []},
            ],
            "links": {},
        }
    }
    result, _ = _fetch(pages=pages)
    # El item sin timestamp se salta SIN cortar la página; el resto se recoge.
    assert _ids(result) == ["a", "b"]
    assert result.next_cursor == {"watermark": 300}


def test_missing_url_or_slug_is_skipped():
    pages = {
        1: {
            "data": [
                {"slug": "ok", "url": "https://x/ok", "title": "T", "created_at": 300, "tags": []},
                {"title": "sin nada", "created_at": 290, "tags": []},
                {"slug": "sin-url", "title": "T", "created_at": 280, "tags": []},
            ],
            "links": {},
        }
    }
    result, _ = _fetch(pages=pages)
    assert _ids(result) == ["ok"]
    # Los inválidos NO frenan el watermark (sus timestamps sí se observan).
    assert result.next_cursor == {"watermark": 300}


def test_keyword_scope_filters_client_side():
    result, _ = _fetch(params={"keyword": "python", "max_pages": 5})
    assert _ids(result) == ["a", "c"]
    assert result.next_cursor == {"watermark": 300}


def test_http_error_propagates():
    provider = ArbeitnowProvider()

    async def go():
        transport = httpx.MockTransport(lambda req: httpx.Response(500))
        async with httpx.AsyncClient(transport=transport) as http:
            await provider.fetch_new({}, None, http)

    try:
        asyncio.run(go())
        raise AssertionError("debió propagar el error HTTP")
    except httpx.HTTPStatusError:
        pass
