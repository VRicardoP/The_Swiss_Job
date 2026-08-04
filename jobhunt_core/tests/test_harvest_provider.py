"""Provider Arbeitnow (A-03, diseño final 4ª revisión): barrido completo con
EMISIÓN TOTAL (el sink dedup/refresca; A-04 necesita ver todo lo visible) +
objetivo adaptativo de páginas (liveness sin tope manual). HTTP MOCKEADO.
"""

import asyncio
import json

import httpx
import pytest

from jobhunt_core.harvest.provider import ProviderConfigError
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


def _fetch(params=None, cursor=None, pages=PAGES, handler=None):
    hits: list = []

    def default_handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        hits.append(page)
        return httpx.Response(200, text=json.dumps(pages.get(page, {"data": [], "links": {}})))

    provider = ArbeitnowProvider()

    async def go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler or default_handler)
        ) as http:
            return await provider.fetch_new(params or {}, cursor, http)

    return asyncio.run(go()), hits


def _ids(result):
    return [x.external_id for x in result.listings]


def test_full_sweep_emits_everything_every_run():
    """EMISIÓN TOTAL (rev. 4ª #3): dos barridos idénticos entregan lo MISMO al
    sink — A-04 refresca last_seen_at y detecta revisiones con cada cosecha."""
    r1, _ = _fetch()
    assert _ids(r1) == ["a", "b", "c", "d", "e"]
    assert r1.complete is True
    r2, _ = _fetch(cursor=r1.next_cursor)
    assert _ids(r2) == ["a", "b", "c", "d", "e"]  # nada se filtra por "ya visto"
    assert r2.next_cursor["last_top_seen"] == 300  # watermark = SOLO metadato


def test_adaptive_target_reaches_full_feed_without_manual_change():
    """Repro del revisor (rev. 4ª #1): feed 3 páginas con max_pages=2 — el
    objetivo adaptativo crece solo hasta agotar el feed; 'e' SÍ se ve."""
    r1, hits1 = _fetch(params={"max_pages": 2})
    assert hits1 == [1, 2]
    assert r1.complete is False  # barrido incompleto, señalizado
    assert r1.next_cursor["page_target"] == 4  # objetivo duplicado, persistido

    # MISMO max_pages configurado: el target persistido manda.
    r2, hits2 = _fetch(params={"max_pages": 2}, cursor=r1.next_cursor)
    assert hits2 == [1, 2, 3]
    assert "e" in _ids(r2)  # e_never_seen=False
    assert r2.complete is True
    assert r2.next_cursor["page_target"] == 4  # el tamaño APRENDIDO se conserva (P2#1)


def test_mid_run_deletion_only_delays_never_loses():
    """Repro del revisor (rev. 4ª #2): borrado DURANTE la paginación desplaza
    c/d a territorio ya visitado — el siguiente barrido completo los emite."""
    state = {"page1_served": False}

    def mutating_handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        if page == 1 and not state["page1_served"]:
            state["page1_served"] = True  # tras servir p1, "se borran" a y b
            return httpx.Response(200, text=json.dumps({
                "data": [
                    {"slug": "a", "url": "https://x/a", "title": "T", "created_at": 400, "tags": []},
                    {"slug": "b", "url": "https://x/b", "title": "T", "created_at": 350, "tags": []},
                ],
                "links": {"next": "?page=2"},
            }))
        v2 = {  # feed tras el borrado: c/d subieron a la página 1
            1: {"data": [
                {"slug": "c", "url": "https://x/c", "title": "T", "created_at": 300, "tags": []},
                {"slug": "d", "url": "https://x/d", "title": "T", "created_at": 250, "tags": []},
            ], "links": {"next": "?page=2"}},
            2: {"data": [
                {"slug": "e", "url": "https://x/e", "title": "T", "created_at": 200, "tags": []},
                {"slug": "f", "url": "https://x/f", "title": "T", "created_at": 150, "tags": []},
            ], "links": {}},
        }
        return httpx.Response(200, text=json.dumps(v2.get(page, {"data": [], "links": {}})))

    r1, _ = _fetch(handler=mutating_handler)
    assert _ids(r1) == ["a", "b", "e", "f"]  # c/d desplazados: omisión TEMPORAL

    r2, _ = _fetch(cursor=r1.next_cursor, handler=mutating_handler)
    assert _ids(r2) == ["c", "d", "e", "f"]  # lost=[] — el barrido total los ve
    assert set(_ids(r1)) | set(_ids(r2)) == {"a", "b", "c", "d", "e", "f"}


def test_learned_target_persists_after_complete_no_oscillation():
    """Rev. 5ª P2#1: el tamaño aprendido se CONSERVA tras completar — sin ciclo
    partial/complete/partial (4 runs, config fija max_pages=2)."""
    r1, h1 = _fetch(params={"max_pages": 2})
    assert (r1.complete, r1.next_cursor["page_target"], h1) == (False, 4, [1, 2])
    r2, h2 = _fetch(params={"max_pages": 2}, cursor=r1.next_cursor)
    assert (r2.complete, h2) == (True, [1, 2, 3])
    assert r2.next_cursor["page_target"] == 4  # APRENDIDO, no eliminado
    r3, h3 = _fetch(params={"max_pages": 2}, cursor=r2.next_cursor)
    assert (r3.complete, h3) == (True, [1, 2, 3])  # ya no vuelve a 'partial'
    r4, h4 = _fetch(params={"max_pages": 2}, cursor=r3.next_cursor)
    assert (r4.complete, h4) == (True, [1, 2, 3])


def test_hard_cap_is_contractual_with_persistent_alert(caplog):
    """Rev. 5ª P2#2: feed > hard_max_pages → 'partial' estable sobre el mismo
    prefijo + alerta de CAPACIDAD en CADA run (nunca pérdida silenciosa)."""
    import logging

    with caplog.at_level(logging.ERROR):
        r1, h1 = _fetch(params={"max_pages": 2, "hard_max_pages": 2})
        assert (r1.complete, r1.next_cursor["page_target"], h1) == (False, 2, [1, 2])
        r2, h2 = _fetch(params={"max_pages": 2, "hard_max_pages": 2}, cursor=r1.next_cursor)
        assert (r2.complete, r2.next_cursor["page_target"], h2) == (False, 2, [1, 2])
    assert caplog.text.count("CAPACIDAD EXCEDIDA") == 2  # persistente, cada run


def test_partial_sweep_flag_and_no_target_regression():
    """El flag complete=False acompaña SIEMPRE al corte por objetivo, y el
    objetivo nunca decrece por debajo de lo configurado."""
    r1, _ = _fetch(params={"max_pages": 2})
    assert (r1.complete, r1.next_cursor["page_target"]) == (False, 4)
    # Con el feed ya agotable, un cursor con target viejo no reduce el barrido.
    r2, _ = _fetch(params={"max_pages": 5}, cursor={"page_target": 2, "last_top_seen": 0})
    assert r2.complete is True and _ids(r2) == ["a", "b", "c", "d", "e"]


def test_items_without_timestamp_are_emitted():
    """Sin filtro por watermark ya no hace falta timestamp para EMITIR."""
    pages = {
        1: {"data": [
            {"slug": "sints", "url": "https://x/s", "title": "SinTS", "tags": []},
            {"slug": "ok", "url": "https://x/ok", "title": "T", "created_at": 300, "tags": []},
        ], "links": {}},
    }
    result, _ = _fetch(pages=pages)
    assert _ids(result) == ["sints", "ok"]
    assert result.next_cursor["last_top_seen"] == 300


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


def test_malformed_items_isolated_not_fatal():
    """REGRESIÓN P2 rev. externa integral: un item malformado (tag no-string → reventaba
    `" ".join(tags)` en el filtro de keyword; o item no-objeto) NO debe tumbar el barrido ni dejar
    el scope reintentando la página tóxica. Se aíslan; los válidos de la MISMA página se emiten."""
    pages = {
        1: {"data": [
            {"slug": "ok", "url": "https://x/ok", "title": "Python Dev", "created_at": 10,
             "tags": ["remote"]},
            {"slug": "bad", "url": "https://x/bad", "title": "Python Toxic", "created_at": 9,
             "tags": [1]},            # tag NO-string: rompía el join del filtro keyword
            "no-soy-un-objeto",       # item no-dict
        ], "links": {}},
    }
    # keyword fuerza _matches_keyword (el join de tags) sobre CADA item → antes crasheaba.
    result, _ = _fetch(params={"keyword": "python"}, pages=pages)
    ids = _ids(result)
    assert "ok" in ids and "bad" in ids  # ambos emitidos, SIN crash por el tag no-string
    assert result.complete is True       # barrido TERMINÓ (no reintento infinito de página tóxica)


def test_malformed_body_treated_as_empty_not_fatal():
    """REGRESIÓN P2 rev. externa integral: un cuerpo/'data' no conforme (no-objeto o data no-lista)
    se trata como página vacía en vez de reventar con AttributeError."""
    pages = {1: {"data": "no-soy-una-lista", "links": {}}}
    result, _ = _fetch(pages=pages)
    assert _ids(result) == [] and result.complete is True


def test_malformed_links_treated_as_end_not_fatal():
    """REGRESIÓN P2 rev. externa integral RONDA 2: un `links` truthy NO-objeto (string/lista/número)
    reventaba `(body.get('links') or {}).get('next')` con AttributeError y perdía la página entera.
    Ahora se trata como FIN de paginación; las ofertas válidas de la página SÍ se emiten."""
    pages = {1: {"data": [{"slug": "ok", "url": "https://x/ok", "title": "T", "tags": []}],
                 "links": "?page=2"}}  # links es un STRING, no un objeto
    result, _ = _fetch(pages=pages)
    assert _ids(result) == ["ok"] and result.complete is True


def test_max_pages_below_minimum_rejected():
    with pytest.raises(ValueError, match="max_pages"):
        _fetch(params={"max_pages": 1})


def test_hard_max_pages_invalid_rejected():
    """Rev. A-04 #4: config inválida FALLA explícita — con hard_max_pages=0 el
    barrido haría 0 peticiones y devolvería cursor parcial vacío para siempre."""
    with pytest.raises(ValueError, match="hard_max_pages"):
        _fetch(params={"max_pages": 2, "hard_max_pages": 0})
    with pytest.raises(ValueError, match="hard_max_pages"):
        _fetch(params={"max_pages": 10, "hard_max_pages": 5})


def test_invalid_config_types_are_config_errors():
    """Rev. 3ª (APPROVE P2, repro): max_pages='abc' dejaba consecutive_failures
    creciendo con retries — los TIPOS inválidos del JSON de config son tan
    permanentes como los rangos: ProviderConfigError, nunca
    ValueError/TypeError/AttributeError genéricos."""
    for params in (
        {"max_pages": "abc"},  # repro de la revisión: ValueError → retry x2
        {"hard_max_pages": None},  # TypeError
        {"keyword": 123},  # AttributeError en .lower()
    ):
        with pytest.raises(ProviderConfigError):
            _fetch(params=params)


def test_corrupt_cursor_is_config_error():
    """Rev. 3ª: un cursor corrupto tampoco se arregla reintentando."""
    for cursor in ({"page_target": "abc"}, {"last_top_seen": {}}):
        with pytest.raises(ProviderConfigError):
            _fetch(cursor=cursor)


def test_keyword_scope_filters_client_side():
    result, _ = _fetch(params={"keyword": "python"})
    assert _ids(result) == ["a", "c"]
    assert result.complete is True


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
