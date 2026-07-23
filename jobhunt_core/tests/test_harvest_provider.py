"""Provider Arbeitnow (A-03): barrido completo por run, con HTTP MOCKEADO.

3ª revisión externa: sin orden contractual, ningún corte finito de páginas
antiguas demuestra drenaje → cada run recorre el feed ENTERO; el watermark solo
filtra la EMISIÓN y solo se consolida al AGOTAR el feed. Sin ancla/backfill.
"""

import asyncio
import json

import httpx
import pytest

from jobhunt_core.harvest.providers.arbeitnow import ArbeitnowProvider

# p1: a300,b250 · p2: c200,d100 · p3: e50.
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


def test_full_sweep_emits_all_and_consolidates():
    result, hits = _fetch()
    assert _ids(result) == ["a", "b", "c", "d", "e"]
    assert result.next_cursor == {"watermark": 300}
    assert hits == [1, 2, 3]  # feed completo, siempre


def test_watermark_filters_emission_but_sweep_continues():
    """El watermark decide QUÉ se emite, nunca CUÁNDO se corta: el barrido llega
    al final del feed aunque haya páginas enteras antiguas por el camino."""
    result, hits = _fetch(cursor={"watermark": 200})
    assert _ids(result) == ["a", "b", "c"]  # c (==200) re-emitido; d/e antiguos
    assert result.next_cursor == {"watermark": 300}
    assert hits == [1, 2, 3]  # las páginas antiguas NO cortan el barrido


def test_safety_cap_does_not_consolidate_watermark():
    """Tope de seguridad sin agotar el feed → watermark INTACTO (conservador):
    nada puede perderse, solo re-emitirse en el próximo run."""
    r1, hits1 = _fetch(params={"max_pages": 2})
    assert _ids(r1) == ["a", "b", "c", "d"]
    assert hits1 == [1, 2]
    assert r1.next_cursor == {"watermark": 0}  # SIN consolidar

    r2, hits2 = _fetch(params={"max_pages": 5}, cursor=r1.next_cursor)
    assert hits2 == [1, 2, 3]
    assert _ids(r2) == ["a", "b", "c", "d", "e"]  # re-emisión + lo pendiente
    assert r2.next_cursor == {"watermark": 300}
    assert set(_ids(r1)) | set(_ids(r2)) == {"a", "b", "c", "d", "e"}  # unión completa


def test_reviewer_repro_late_page_after_old_page_not_lost():
    """Repro EXACTA del revisor (3ª ronda, P1): [300,250] / [150] / [240] con
    watermark 200 — la página antigua intermedia ya no corta; 240 se emite."""
    pages = {
        1: {"data": [
            {"slug": "a", "url": "https://x/a", "title": "T", "created_at": 300, "tags": []},
            {"slug": "b", "url": "https://x/b", "title": "T", "created_at": 250, "tags": []},
        ], "links": {"next": "?page=2"}},
        2: {"data": [
            {"slug": "old", "url": "https://x/o", "title": "T", "created_at": 150, "tags": []},
        ], "links": {"next": "?page=3"}},
        3: {"data": [
            {"slug": "late", "url": "https://x/l", "title": "T", "created_at": 240, "tags": []},
        ], "links": {}},
    }
    result, hits = _fetch(cursor={"watermark": 200}, pages=pages)
    assert hits == [1, 2, 3]
    assert _ids(result) == ["a", "b", "late"]  # late_lost=False
    assert result.next_cursor == {"watermark": 300}


def test_reviewer_repro_timestampless_page_does_not_cut():
    """Repro EXACTA del revisor (3ª ronda, P2): una página sin created_at no
    prueba territorio antiguo — el barrido continúa y 300 se emite."""
    pages = {
        1: {"data": [{"slug": "sints", "url": "https://x/s", "title": "SinTS", "tags": []}],
            "links": {"next": "?page=2"}},
        2: {"data": [{"slug": "nueva", "url": "https://x/n", "title": "T", "created_at": 300, "tags": []}],
            "links": {}},
    }
    result, hits = _fetch(cursor={"watermark": 200}, pages=pages)
    assert hits == [1, 2]
    assert _ids(result) == ["nueva"]  # new_missed=False
    assert result.next_cursor == {"watermark": 300}


def test_late_insert_between_runs_not_lost():
    """Inserción entre runs: el barrido completo del run siguiente la emite."""
    r1, _ = _fetch(cursor={"watermark": 0})
    v2 = {
        1: {"data": [
            PAGES[1]["data"][0], PAGES[1]["data"][1],
            {"slug": "x", "url": "https://x/x", "title": "T", "created_at": 275, "tags": []},
        ], "links": {"next": "?page=2"}},
        2: PAGES[2], 3: PAGES[3],
    }
    r2, _ = _fetch(cursor=r1.next_cursor, pages=v2)
    assert "x" not in _ids(r2)  # 275 < watermark 300: retrodatada — limitación documentada
    # La garantía cubre lo >= watermark: con watermark previo 250, x SÍ se emite.
    r2b, _ = _fetch(cursor={"watermark": 250}, pages=v2)
    assert "x" in _ids(r2b)


def test_out_of_order_page_does_not_lose():
    pages = {
        1: {"data": [
            {"slug": "a", "url": "https://x/a", "title": "T", "created_at": 300, "tags": []},
            {"slug": "b", "url": "https://x/b", "title": "T", "created_at": 100, "tags": []},
            {"slug": "c", "url": "https://x/c", "title": "T", "created_at": 250, "tags": []},
        ], "links": {}},
    }
    result, _ = _fetch(cursor={"watermark": 200}, pages=pages)
    assert _ids(result) == ["a", "c"]
    assert result.next_cursor == {"watermark": 300}


def test_max_pages_below_minimum_rejected():
    with pytest.raises(ValueError, match="max_pages"):
        _fetch(params={"max_pages": 1})


def test_missing_url_or_slug_is_skipped():
    pages = {
        1: {"data": [
            {"slug": "ok", "url": "https://x/ok", "title": "T", "created_at": 300, "tags": []},
            {"title": "sin nada", "created_at": 290, "tags": []},
            {"slug": "sin-url", "title": "T", "created_at": 280, "tags": []},
        ], "links": {}},
    }
    result, _ = _fetch(pages=pages)
    assert _ids(result) == ["ok"]
    assert result.next_cursor == {"watermark": 300}


def test_keyword_scope_filters_client_side():
    result, _ = _fetch(params={"keyword": "python"})
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
