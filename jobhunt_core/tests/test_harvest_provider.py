"""Provider Arbeitnow (A-03, diseño final 4ª revisión): barrido completo con
EMISIÓN TOTAL (el sink dedup/refresca; A-04 necesita ver todo lo visible) +
objetivo adaptativo de páginas (liveness sin tope manual). HTTP MOCKEADO.
"""

import asyncio
import json
import time

import httpx
import pytest

from jobhunt_core.harvest.provider import ProviderConfigError, ProviderResponseError
from jobhunt_core.harvest.providers import arbeitnow
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


def test_malformed_body_is_typed_response_error():
    """REGRESIÓN auditoría externa 2026-08-27 P1-1 (#1).

    CORRIGE la versión anterior de esta prueba (`test_malformed_body_treated_as_empty_not_fatal`),
    que FIJABA el bug: exigía `complete=True` para un sobre no conforme. Degradar un cuerpo
    no-objeto o un `data` no-lista a "página vacía" evita el AttributeError —eso sigue vigente—
    pero lo hace INDISTINGUIBLE del final contractual del feed, y el runner confirma con ello una
    cosecha completa que nunca ocurrió (cursor persistido, `last_complete_at` refrescado,
    `consecutive_failures=0`). La forma inválida es un fallo TRANSITORIO de frontera, no un final.
    """
    for body in ("no-soy-un-objeto", ["tampoco"], 42, {"data": "no-soy-una-lista"}):
        with pytest.raises(ProviderResponseError):
            _fetch(pages={1: body})


def test_malformed_links_after_data_is_not_complete():
    """REGRESIÓN auditoría externa 2026-08-27 P1-1 (#2).

    CORRIGE `test_malformed_links_treated_as_end_not_fatal`. Un `links` truthy NO-objeto
    (string/lista/número) tampoco puede reventar con AttributeError (eso lo arregló la ronda 2),
    pero leerlo como FIN de paginación declara `complete=True` sobre un barrido que se cortó en la
    página 1 con el feed a medias. Ahora es error tipado: el barrido NO se declara completo.
    """
    pages = {1: {"data": [{"slug": "ok", "url": "https://x/ok", "title": "T", "tags": []}],
                 "links": "?page=2"}}  # links es un STRING, no un objeto
    with pytest.raises(ProviderResponseError):
        _fetch(pages=pages)


def test_contractual_empty_page_is_complete():
    """REGRESIÓN auditoría externa 2026-08-27 P1-1 (#4): una página VÁLIDA y vacía
    (`data: []` con la forma contractual) sigue siendo el final legítimo del feed —
    endurecer la frontera no puede convertir un feed agotado en un fallo perpetuo."""
    # Página vacía con sobre contractual: fin del feed, sin listings.
    vacia, hits = _fetch(pages={1: {"data": [], "links": {}}})
    assert _ids(vacia) == [] and vacia.complete is True and hits == [1]
    # Y con datos + `links` OBJETO sin `next`: también fin legítimo.
    ultima, _ = _fetch(
        pages={1: {"data": [{"slug": "ok", "url": "https://x/ok", "title": "T", "tags": []}],
                   "links": {}}}
    )
    assert _ids(ultima) == ["ok"] and ultima.complete is True


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


def test_missing_links_is_typed_response_error():
    """REGRESIÓN auditoría G9 P1-A (la otra clave del mismo sobre).

    `data` quedó protegido en P1-1 pero `links` ausente/`null` seguía leyéndose como final
    contractual del feed: `complete=True`, cursor persistido, `last_complete_at` refrescado y
    `consecutive_failures=0` sobre un barrido cortado en la página 1 — el defecto que P1-1 vino
    a cerrar, por la otra puerta. Arbeitnow es un paginador Laravel y `links` va SIEMPRE
    (comprobado en vivo contra el feed público: ver `test_terminal_page_of_the_real_feed_...`),
    así que un sobre sin `links` no es la última página: es forma inválida.
    """
    item = {"slug": "ok", "url": "https://x/ok", "title": "T", "tags": []}
    for body in ({"data": [item]}, {"data": [item], "links": None}, {"data": []}):
        with pytest.raises(ProviderResponseError):
            _fetch(pages={1: body})


def test_terminal_page_of_the_real_feed_is_complete():
    """La otra dirección de P1-A: la página TERMINAL legítima sigue cerrando el barrido.

    Forma REAL medida contra https://www.arbeitnow.com/api/job-board-api?page=5000 (HTTP 200):
    `links` PRESENTE con `next: null` y `last: null`. Endurecer `links` no puede convertir un
    feed agotado en un fallo perpetuo.
    """
    terminal = {
        "data": [{"slug": "ok", "url": "https://x/ok", "title": "T", "tags": []}],
        "links": {"first": "?page=1", "last": None, "prev": "?page=4999", "next": None},
        "meta": {"current_page": 5000},
    }
    result, hits = _fetch(pages={1: terminal})
    assert _ids(result) == ["ok"] and result.complete is True and hits == [1]
    assert result.error is None
    # Y vacía con la misma forma terminal: fin del feed, sin listings.
    vacia, _ = _fetch(pages={1: {"data": [], "links": {"next": None}}})
    assert _ids(vacia) == [] and vacia.complete is True


def test_broken_envelope_mid_sweep_keeps_the_pages_already_harvested():
    """REGRESIÓN auditoría G9 P2-C: el endurecimiento de la frontera tiraba el barrido entero.

    Páginas 1 y 2 válidas, página 3 con el sobre roto: lo ya cosechado es VÁLIDO y se emite
    (invariante de `runner.py`: un barrido incompleto se persiste pero no cuenta como cosecha
    completa). Con una forma inválida PERSISTENTE en la página 3, el todo-o-nada dejaba la
    fuente sin ingerir una sola oferta. El fallo NO se pierde: viaja en `FetchResult.error`
    para que el runner lo contabilice.
    """
    pages = dict(PAGES)
    pages[3] = {"data": "no-soy-una-lista", "links": {}}
    result, hits = _fetch(pages=pages)
    assert _ids(result) == ["a", "b", "c", "d"]  # páginas 1 y 2, íntegras
    assert hits == [1, 2, 3]
    assert result.complete is False              # jamás cosecha completa
    assert result.error and "data" in result.error
    assert result.pages_fetched == 2             # solo las páginas bien formadas


def test_broken_envelope_on_the_first_page_still_raises():
    """El otro lado de P2-C: sin páginas anteriores no hay nada que preservar y el sobre
    inválido sube como error tipado (P1-1 intacto) — el runner cuenta el fallo y deja
    cursor y `last_complete_at` como estaban."""
    with pytest.raises(ProviderResponseError):
        _fetch(pages={1: {"data": "no-soy-una-lista", "links": {}}})


def test_empty_page_with_next_announced_is_not_a_complete_harvest():
    """REGRESIÓN auditoría G10 P1-1: el sobre se contradecía a sí mismo y ganaba el silencio.

    `data: []` con `links.next` PRESENTE no es el final del feed: es un paginador que anuncia
    página siguiente y no la entrega. El código calculaba `has_next` y lo tiraba, así que un
    barrido que emitía CERO ofertas quedaba registrado como cosecha COMPLETA —cursor persistido,
    `last_complete_at` refrescado, `consecutive_failures = 0`— y la vigilancia de `health.py`
    callaba. Es el mismo falso verde de P1-1/G9 P1-A por la cuarta puerta, y contradice el
    contrato escrito en el propio módulo («un barrido que no agota `links.next` devuelve
    `complete=False`»). No es teórico: medido en vivo el 2026-08-27, la paginación real ya es
    incoherente (`?page=1` → from=1,to=175; `?page=2` → from=101,to=175).
    """
    vacia_con_next = {"data": [], "links": {"next": "?page=2"}}
    # Página 1: nada que preservar → el error tipado sube (el runner cuenta el fallo).
    with pytest.raises(ProviderResponseError):
        _fetch(pages={1: vacia_con_next})
    # A mitad de barrido: lo cosechado se emite, pero JAMÁS como cosecha completa.
    result, hits = _fetch(pages={1: PAGES[1], 2: vacia_con_next})
    assert _ids(result) == ["a", "b"] and hits == [1, 2]
    assert result.complete is False and result.error and "next" in result.error


def test_empty_page_without_next_is_still_the_end_of_the_feed():
    """La OTRA dirección de P1-1 (sobre-corrección): el final legítimo sigue cerrando.

    Forma terminal real del feed (medida en vivo): `data: []` con `links.next: null`. Exigir
    coherencia entre `data` y `links.next` no puede convertir un feed agotado en un fallo
    perpetuo.
    """
    for links in ({"next": None}, {}, {"next": ""}):
        vacia, hits = _fetch(pages={1: {"data": [], "links": links}})
        assert _ids(vacia) == [] and vacia.complete is True and vacia.error is None
        assert hits == [1]


def test_page_of_items_with_none_usable_is_not_a_complete_harvest():
    """REGRESIÓN auditoría G10 P2-1: el hermano de P1-1 una capa más abajo (la del ITEM).

    El sobre puede ser impecable —`data` es lista, `links` es objeto— y su contenido
    inservible: es exactamente el aspecto de una subida de versión de la API que renombra
    `url`→`job_url`. `_to_listing` descartaba cada item con un warning y el barrido se
    declaraba COMPLETO con cero listings; peor, la vigilancia de G9 quedaba CIEGA porque
    `last_complete_at` se refrescaba y `consecutive_failures` se reseteaba en cada corrida.
    Una cosecha que no ingiere NADA no es una cosecha completa salvo que el feed declare
    explícitamente que está vacío (ese caso lo fija el test de arriba).
    """
    renombrada = {"data": [{"id": 1, "job_url": "https://x/a", "name": "T"}],
                  "links": {"next": None}}
    no_objetos = {"data": ["x", "y", 3], "links": {"next": None}}
    for body in (renombrada, no_objetos):
        with pytest.raises(ProviderResponseError):
            _fetch(pages={1: body})
    # A mitad de barrido: preservación, como cualquier otra forma inválida.
    result, _ = _fetch(pages={1: PAGES[1], 2: renombrada})
    assert _ids(result) == ["a", "b"]
    assert result.complete is False and result.error and "utilizables" in result.error


def test_keyword_filtering_everything_out_is_still_a_complete_harvest():
    """La otra dirección de P2-1: 0 emitidos por el FILTRO DEL SCOPE no es forma inválida.

    Los items son perfectamente utilizables; simplemente ninguno casa con la keyword. Confundir
    «el scope no quiere nada de esta página» con «la API cambió de forma» dejaría un scope
    estrecho fallando para siempre.
    """
    r, hits = _fetch(params={"keyword": "no-existe-esta-palabra"})
    assert _ids(r) == [] and r.complete is True and r.error is None
    assert hits == [1, 2, 3]


def _feed_con_fallo_en(page_mala: int, respuesta):
    """Handler del feed sano salvo en `page_mala`, donde devuelve `respuesta()`."""
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        if page == page_mala:
            return respuesta()
        return httpx.Response(200, text=json.dumps(PAGES.get(page, {"data": [], "links": {}})))

    return handler


_FALLOS_TRANSITORIOS = {
    "HTTP 429": lambda: httpx.Response(429),
    "200 con cuerpo vacío": lambda: httpx.Response(200, text=""),
    "200 con HTML del CDN": lambda: httpx.Response(200, text="<html>502 Bad Gateway</html>"),
}


def test_transport_failures_mid_sweep_keep_the_pages_already_harvested():
    """REGRESIÓN auditoría G10 P1-2: la preservación de G9 solo cubría la excepción PROPIA.

    `_sweep_feed` promete en su docstring emitir las k−1 páginas buenas «hasta un sobre
    inválido», pero solo capturaba `ProviderResponseError`. Los tres fallos que de verdad
    ocurren contra el feed real —HTTP 429 (medido en vivo: llega en la petición 11 sin
    pausa), un 200 con el cuerpo vacío y un 200 con HTML de una página de error de CDN—
    subían como `HTTPStatusError`/`JSONDecodeError` y tiraban el barrido ENTERO: el mismo
    todo-o-nada que G9 dice haber cerrado, por la puerta del transporte.
    """
    for nombre, respuesta in _FALLOS_TRANSITORIOS.items():
        result, _ = _fetch(handler=_feed_con_fallo_en(2, respuesta))
        assert _ids(result) == ["a", "b"], nombre        # la página 1, íntegra
        assert result.complete is False, nombre          # jamás cosecha completa
        assert result.error, nombre                      # el runner lo contabiliza
        assert result.pages_fetched == 1, nombre


def test_transport_failure_on_the_first_page_still_raises():
    """El otro lado: sin páginas anteriores no hay nada que preservar y el fallo sube tal
    cual — el runner lo cuenta y deja cursor y `last_complete_at` como estaban."""
    for respuesta in _FALLOS_TRANSITORIOS.values():
        with pytest.raises((httpx.HTTPError, json.JSONDecodeError)):
            _fetch(handler=_feed_con_fallo_en(1, respuesta))


def test_the_sweep_paces_itself_between_pages(monkeypatch):
    """REGRESIÓN auditoría G10 P1-2 (la mitad del RITMO): el barrido no tenía un solo sleep.

    Medido en vivo el 2026-08-27 contra el feed público: a ráfaga la petición 11 devuelve
    HTTP 429 y el feed necesita 18–31 páginas, así que `complete=True` era INALCANZABLE —
    y el `self.retry(countdown=120)` de la tarea repetía la ráfaga. Con 3,5 s de pausa
    pasaron 16 de 16 peticiones sin un solo rechazo.
    """
    monkeypatch.setattr(arbeitnow, "PAGE_PAUSE_S", 0.1)
    t0 = time.monotonic()
    r, hits = _fetch()  # feed de 3 páginas → 2 pausas
    transcurrido = time.monotonic() - t0
    assert r.complete is True and hits == [1, 2, 3]
    assert transcurrido >= 0.2, transcurrido
    # …y NINGUNA pausa antes de la primera página: un feed de una sola no espera.
    t1 = time.monotonic()
    _fetch(pages={1: {"data": [], "links": {"next": None}}})
    assert time.monotonic() - t1 < 0.1


def test_a_rate_limited_page_is_retried_and_the_sweep_completes():
    """REGRESIÓN G10 P1-2: el 429 del bucket es transitorio POR DEFINICIÓN — reintentarlo
    con backoff es lo que permite terminar el barrido en vez de tirarlo."""
    rechazos = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        if page == 2 and rechazos["n"] < arbeitnow.HTTP_ATTEMPTS - 1:
            rechazos["n"] += 1
            return httpx.Response(429)
        return httpx.Response(200, text=json.dumps(PAGES.get(page, {"data": [], "links": {}})))

    r, _ = _fetch(handler=handler)
    assert rechazos["n"] == arbeitnow.HTTP_ATTEMPTS - 1
    assert _ids(r) == ["a", "b", "c", "d", "e"]
    assert r.complete is True and r.error is None


def test_a_permanent_http_status_is_not_retried():
    """Insistir en un 404/403 no lo arregla: solo gasta peticiones contra una API que
    pide explícitamente que no se abuse de ella."""
    peticiones = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        peticiones["n"] += 1
        return httpx.Response(404)

    with pytest.raises(httpx.HTTPStatusError):
        _fetch(handler=handler)
    assert peticiones["n"] == 1


def test_retry_delay_prefers_the_header_and_falls_back_to_exponential_backoff():
    """El 429 del feed real NO trae `Retry-After` (medido), así que el backoff es
    exponencial sobre la pausa; si algún día lo trae, manda el servidor."""
    peticion = httpx.Request("GET", arbeitnow.API_URL)
    con = httpx.Response(429, headers={"retry-after": "42"}, request=peticion)
    sin = httpx.Response(429, request=peticion)
    delay = arbeitnow._retry_delay
    assert delay(httpx.HTTPStatusError("429", request=peticion, response=con), 1.0, 1) == 42.0
    assert delay(httpx.HTTPStatusError("429", request=peticion, response=sin), 1.0, 1) == 2.0
    assert delay(httpx.HTTPStatusError("429", request=peticion, response=sin), 1.0, 2) == 4.0
