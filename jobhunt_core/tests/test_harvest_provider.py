"""Provider Arbeitnow (A-03): incremental SIN pérdida, con HTTP MOCKEADO.

Revisión externa A-03: (1) sin resume por página — backfill desde la página 1
con ANCLA lógica (inmune a la deriva por borrados: solo re-emite, nunca salta);
(2) empates de frontera re-emitidos (dedup en el sink idempotente); (3) items
sin timestamp/url se saltan sin disparar el corte; (4) liveness: max_pages>=2
y techo de skip con reinicio de ancla.
"""

import asyncio
import json

import httpx
import pytest

import jobhunt_core.harvest.providers.arbeitnow as arb
from jobhunt_core.harvest.providers.arbeitnow import ArbeitnowProvider

# Orden desc por created_at. p1: a300,b250 · p2: c200,d100 · p3: e50.
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


def test_budget_cut_never_loses_offers_anchor_resumes():
    """Presupuesto < backlog → watermark intacto + ancla (SOLO progreso); el run
    siguiente re-escanea desde la página 1 RE-EMITIENDO todo >= watermark (el
    ancla no descarta: rev. 2ª #1) y drena. Unión completa."""
    r1, hits1 = _fetch(params={"max_pages": 2})
    assert _ids(r1) == ["a", "b", "c", "d"]
    assert hits1 == [1, 2]
    assert r1.next_cursor == {"watermark": 0, "pending_watermark": 300, "anchor_ts": 100}

    # Run 2: desde la página 1; la página sobre el ancla se RE-EMITE (no cuenta
    # contra el presupuesto de progreso) y el drenaje termina.
    r2, hits2 = _fetch(params={"max_pages": 2}, cursor=r1.next_cursor)
    assert hits2 == [1, 2, 3]
    assert _ids(r2) == ["a", "b", "c", "d", "e"]  # re-emisión (dedup en el sink)
    assert r2.next_cursor == {"watermark": 300}  # drenado → consolida el pending
    assert set(_ids(r1)) | set(_ids(r2)) == {"a", "b", "c", "d", "e"}  # nada se pierde


def test_late_insert_above_anchor_is_not_lost():
    """Repro EXACTA del revisor (2ª ronda, P1): una oferta insertada ENTRE runs
    por encima del ancla debe emitirse igualmente (el ancla no filtra)."""
    v1 = {
        1: {"data": [
            {"slug": "a", "url": "https://x/a", "title": "T", "created_at": 400, "tags": []},
            {"slug": "b", "url": "https://x/b", "title": "T", "created_at": 350, "tags": []},
        ], "links": {"next": "?page=2"}},
        2: {"data": [
            {"slug": "c", "url": "https://x/c", "title": "T", "created_at": 300, "tags": []},
            {"slug": "d", "url": "https://x/d", "title": "T", "created_at": 250, "tags": []},
        ], "links": {"next": "?page=3"}},
        3: {"data": [
            {"slug": "e", "url": "https://x/e", "title": "T", "created_at": 200, "tags": []},
            {"slug": "f", "url": "https://x/f", "title": "T", "created_at": 150, "tags": []},
        ], "links": {}},
    }
    r1, _ = _fetch(params={"max_pages": 2}, pages=v1)
    assert r1.next_cursor == {"watermark": 0, "pending_watermark": 400, "anchor_ts": 250}

    # Aparece x (325) entre runs: cae POR ENCIMA del ancla (250).
    v2 = {
        1: {"data": [
            v1[1]["data"][0], v1[1]["data"][1],
            {"slug": "x", "url": "https://x/x", "title": "T", "created_at": 325, "tags": []},
        ], "links": {"next": "?page=2"}},
        2: v1[2], 3: v1[3],
    }
    r2, _ = _fetch(params={"max_pages": 2}, cursor=r1.next_cursor, pages=v2)
    assert "x" in _ids(r2)  # ← el bug del revisor: antes se saltaba para siempre
    r3, _ = _fetch(params={"max_pages": 5}, cursor=r2.next_cursor, pages=v2)
    emitted_everywhere = set(_ids(r1)) | set(_ids(r2)) | set(_ids(r3))
    assert emitted_everywhere == {"a", "b", "c", "d", "e", "f", "x"}  # lost=False


def test_out_of_order_page_does_not_lose():
    """Repro EXACTA del revisor (2ª ronda, P2): página [300,100,250] con
    watermark 200 — el corte es POR PÁGINA, no por el primer item viejo."""
    pages = {
        1: {"data": [
            {"slug": "a", "url": "https://x/a", "title": "T", "created_at": 300, "tags": []},
            {"slug": "b", "url": "https://x/b", "title": "T", "created_at": 100, "tags": []},
            {"slug": "c", "url": "https://x/c", "title": "T", "created_at": 250, "tags": []},
        ], "links": {}},
    }
    result, _ = _fetch(cursor={"watermark": 200}, pages=pages)
    assert _ids(result) == ["a", "c"]  # c (250) YA NO se pierde (late_lost=False)
    assert result.next_cursor == {"watermark": 300}


def test_disorder_requires_two_old_pages_to_stop():
    """Con desorden detectado, una sola página antigua no corta (conservador)."""
    pages = {
        1: {"data": [
            {"slug": "a", "url": "https://x/a", "title": "T", "created_at": 300, "tags": []},
            {"slug": "b", "url": "https://x/b", "title": "T", "created_at": 100, "tags": []},
            {"slug": "c", "url": "https://x/c", "title": "T", "created_at": 250, "tags": []},
        ], "links": {"next": "?page=2"}},
        2: {"data": [
            {"slug": "old1", "url": "https://x/o1", "title": "T", "created_at": 150, "tags": []},
        ], "links": {"next": "?page=3"}},
        3: {"data": [
            {"slug": "late", "url": "https://x/l", "title": "T", "created_at": 240, "tags": []},
        ], "links": {}},
    }
    result, hits = _fetch(cursor={"watermark": 200}, pages=pages)
    assert hits == [1, 2, 3]  # la página 2 (antigua) NO corta: hay desorden
    assert _ids(result) == ["a", "c", "late"]  # 'late' (240) rescatada
    assert result.next_cursor == {"watermark": 300}


def test_deletion_drift_beyond_pages_does_not_lose(caplog):
    """Repro EXACTA del revisor: borrados masivos entre runs desplazan el feed;
    el re-escaneo desde la página 1 entrega e/f igualmente (solo re-emisión,
    jamás salto)."""
    v1 = {
        1: {"data": [
            {"slug": "a", "url": "https://x/a", "title": "T", "created_at": 400, "tags": []},
            {"slug": "b", "url": "https://x/b", "title": "T", "created_at": 350, "tags": []},
        ], "links": {"next": "?page=2"}},
        2: {"data": [
            {"slug": "c", "url": "https://x/c", "title": "T", "created_at": 300, "tags": []},
            {"slug": "d", "url": "https://x/d", "title": "T", "created_at": 250, "tags": []},
        ], "links": {"next": "?page=3"}},
        3: {"data": [
            {"slug": "e", "url": "https://x/e", "title": "T", "created_at": 200, "tags": []},
            {"slug": "f", "url": "https://x/f", "title": "T", "created_at": 150, "tags": []},
        ], "links": {}},
    }
    r1, _ = _fetch(params={"max_pages": 2}, pages=v1)
    assert _ids(r1) == ["a", "b", "c", "d"]
    assert r1.next_cursor == {"watermark": 0, "pending_watermark": 400, "anchor_ts": 250}

    # a-d borradas: e/f suben de la página 3 a la 1.
    v2 = {1: {"data": v1[3]["data"], "links": {}}}
    r2, hits2 = _fetch(params={"max_pages": 2}, cursor=r1.next_cursor, pages=v2)
    assert hits2 == [1]
    assert _ids(r2) == ["e", "f"]  # entregadas: 200/150 <= ancla 250 → se emiten
    assert r2.next_cursor == {"watermark": 400}
    assert set(_ids(r1)) | set(_ids(r2)) == {"a", "b", "c", "d", "e", "f"}


def test_max_pages_below_minimum_rejected():
    with pytest.raises(ValueError, match="max_pages"):
        _fetch(params={"max_pages": 1})


def test_skip_overflow_resets_anchor(monkeypatch):
    """Deriva/inserción más profunda que el techo de re-escaneo: se suelta el
    ancla (re-emisión idempotente en el próximo run) en vez de estancarse."""
    monkeypatch.setattr(arb, "MAX_SKIP_PAGES", 1)
    cursor = {"watermark": 0, "pending_watermark": 300, "anchor_ts": 10}
    result, hits = _fetch(params={"max_pages": 2}, cursor=cursor)
    assert hits == [1, 2]  # 2 páginas de re-escaneo (>10) → techo superado
    assert _ids(result) == ["a", "b", "c", "d"]  # re-emitidas igualmente
    assert result.next_cursor == {"watermark": 0, "pending_watermark": 300}  # sin ancla


def test_full_drain_advances_watermark():
    result, hits = _fetch(params={"max_pages": 5})
    assert _ids(result) == ["a", "b", "c", "d", "e"]
    assert result.next_cursor == {"watermark": 300}
    assert hits == [1, 2, 3]


def test_incremental_early_stop_reemits_boundary_tie():
    result, hits = _fetch(cursor={"watermark": 200})
    assert _ids(result) == ["a", "b", "c"]  # c (==200) re-emitido; d no (100<200)
    assert result.next_cursor == {"watermark": 300}
    # El corte es por PÁGINA completa antigua: la página 3 (toda < 200) corta.
    assert hits == [1, 2, 3]


def test_no_new_items_reemits_only_boundary():
    result, hits = _fetch(cursor={"watermark": 300})
    assert _ids(result) == ["a"]  # a (==300) re-emitido; b (250) no
    assert result.next_cursor == {"watermark": 300}
    assert hits == [1, 2]  # página 2 completa antigua → corta


def test_missing_created_at_is_skipped_not_cross():
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
