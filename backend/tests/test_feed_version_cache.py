"""El recorrido completo del feed se reutiliza SÓLO si el core dice que no cambió.

Medido en el NAS (2026-09-23): recorrer el feed es el **87-89 %** del coste de
servir la pantalla principal — 47,5 s en frío y 11,5 s en caliente, en 18
páginas de 100 para 1.800 ofertas. Caliente sigue costando porque un
`If-None-Match` no ahorra trabajo: el ETag se deriva del payload, así que el
core construye la página igual para contestar 304.

Lo que estas pruebas protegen NO es el ahorro —eso se mide—, sino que el ahorro
no pueda servir un feed rancio:

- la caché se usa sólo con la versión EXACTA;
- si la versión cambia, se vuelve a recorrer;
- si el core no sabe dar versión, se recorre como siempre (degradar el
  rendimiento es aceptable; devolver datos viejos, no);
- un recorrido CORTADO por `needed` no se cachea nunca, porque no puede
  responder a quien pida más.
"""

import uuid

import pytest

from services.matching import core_client
from services.matching.core_client import CoreMatching


@pytest.fixture(autouse=True)
def _cache_limpia():
    core_client._feed_cache.clear()
    yield
    core_client._feed_cache.clear()


class _Cliente:
    """Core de mentira que CUENTA lo que se le pide."""

    def __init__(self, paginas, version="v1"):
        self.paginas = paginas
        self.version = version
        self.peticiones_pagina = 0
        self.peticiones_version = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        if url.endswith("/version"):
            self.peticiones_version += 1
            if self.version is None:
                return _Resp(503, {})
            return _Resp(200, {"version": self.version, "total": self._total()})
        self.peticiones_pagina += 1
        cursor = (params or {}).get("cursor")
        indice = 0 if cursor is None else int(cursor)
        return _Resp(200, self.paginas[indice])

    def _total(self):
        return sum(len(p["items"]) for p in self.paginas)


class _Resp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.headers = {}
        self.text = str(body)

    def json(self):
        return self._body


def _item(n):
    return {
        "vacancy": {"id": str(uuid.uuid4()), "title": f"Oferta {n}",
                    "primary_listing": {"source": "core", "external_id": str(n),
                                        "url": f"https://e.com/{n}"},
                    "listings": []},
        "evaluation": {"eval_key": f"k{n}", "score_final": 50.0, "scores": {},
                       "model": {"name": "m", "version": "1"},
                       "policy": {"name": "p", "prompt_version": "1"}},
        "state": {"saved": False, "dismissed": False, "feedback": None},
    }


def _paginas(n_paginas, por_pagina, total):
    paginas = []
    for i in range(n_paginas):
        ultima = i == n_paginas - 1
        paginas.append({
            "items": [_item(i * por_pagina + j) for j in range(por_pagina)],
            "next_cursor": None if ultima else str(i + 1),
            "total": total,
        })
    return paginas


def _matching(cliente):
    m = CoreMatching.__new__(CoreMatching)
    m._client_factory = lambda: cliente
    return m


async def test_la_segunda_lectura_no_recorre_si_la_version_no_cambio():
    cliente = _Cliente(_paginas(3, 10, 30))
    m = _matching(cliente)
    pid = uuid.uuid4()

    primeros, total = await m._fetch_full_feed(pid)
    assert (len(primeros), total) == (30, 30)
    assert cliente.peticiones_pagina == 3

    segundos, total2 = await m._fetch_full_feed(pid)
    assert cliente.peticiones_pagina == 3, "recorrió otra vez teniendo la misma versión"
    assert [i["evaluation"]["eval_key"] for i in segundos] == \
           [i["evaluation"]["eval_key"] for i in primeros]
    assert total2 == total


async def test_si_la_version_cambia_se_recorre_otra_vez():
    cliente = _Cliente(_paginas(3, 10, 30))
    m = _matching(cliente)
    pid = uuid.uuid4()

    await m._fetch_full_feed(pid)
    cliente.version = "v2"
    cliente.paginas = _paginas(3, 10, 30)  # contenido nuevo

    nuevos, _ = await m._fetch_full_feed(pid)
    assert cliente.peticiones_pagina == 6, "sirvió el feed viejo con otra versión"
    assert nuevos[0]["vacancy"]["id"] != "" and len(nuevos) == 30


async def test_sin_version_del_core_se_recorre_siempre():
    """Degradar el rendimiento es aceptable; servir datos viejos, no."""
    cliente = _Cliente(_paginas(3, 10, 30), version=None)
    m = _matching(cliente)
    pid = uuid.uuid4()

    await m._fetch_full_feed(pid)
    await m._fetch_full_feed(pid)
    assert cliente.peticiones_pagina == 6
    assert core_client._feed_cache == {}


async def test_un_recorrido_cortado_no_se_cachea():
    """Con `needed` pequeño se corta antes; ese trozo no puede servir a otro."""
    cliente = _Cliente(_paginas(3, 10, 30))
    m = _matching(cliente)
    pid = uuid.uuid4()

    items, _ = await m._fetch_full_feed(pid, needed=5)
    assert len(items) == 10  # la primera página entera
    assert core_client._feed_cache == {}


async def test_una_pagina_pequena_no_pregunta_la_version():
    """El corte temprano ya resuelve ese caso en UNA petición: preguntar la
    versión lo convertiría en dos, y la consulta no es gratis."""
    cliente = _Cliente(_paginas(3, 10, 30))
    m = _matching(cliente)

    await m._fetch_full_feed(uuid.uuid4(), needed=20)
    assert cliente.peticiones_version == 0


async def test_la_cache_por_perfil_no_se_cruza():
    cliente = _Cliente(_paginas(2, 10, 20))
    m = _matching(cliente)

    a, _ = await m._fetch_full_feed(uuid.uuid4())
    b, _ = await m._fetch_full_feed(uuid.uuid4())
    assert cliente.peticiones_pagina == 4, "reutilizó el feed de OTRO perfil"
    assert len(a) == len(b) == 20


async def test_borrar_un_perfil_tira_su_feed_cacheado():
    cliente = _Cliente(_paginas(2, 10, 20))
    m = _matching(cliente)
    pid = uuid.uuid4()

    await m._fetch_full_feed(pid)
    core_client.clear_feed_cache(pid)
    await m._fetch_full_feed(pid)
    assert cliente.peticiones_pagina == 4


async def test_no_se_cachea_si_el_total_no_cuadra_con_lo_recorrido():
    """Cachear una discrepancia la perpetuaría en todas las lecturas."""
    paginas = _paginas(2, 10, 999)  # el core dice 999 y entrega 20
    cliente = _Cliente(paginas)
    m = _matching(cliente)

    await m._fetch_full_feed(uuid.uuid4())
    assert core_client._feed_cache == {}


async def test_el_llamante_no_puede_corromper_la_cache():
    """Devolver la lista interna dejaría que un mutador de fuera la pisara."""
    cliente = _Cliente(_paginas(2, 10, 20))
    m = _matching(cliente)
    pid = uuid.uuid4()

    items, _ = await m._fetch_full_feed(pid)
    items.clear()
    otra_vez, total = await m._fetch_full_feed(pid)
    assert (len(otra_vez), total) == (20, 20)


@pytest.mark.parametrize("cuerpo", [[], "v1", 7, None, {"version": 7}, {"version": ""}])
async def test_una_version_con_forma_rara_degrada_a_recorrido(cuerpo):
    """Un 200 con otra forma es payload inválido, no una excepción que escape
    del fallback. Un cuerpo no-objeto reventaba con AttributeError."""
    cliente = _Cliente(_paginas(2, 10, 20))

    class _Raro(_Cliente):
        async def get(self, url, params=None, headers=None):
            if url.endswith("/version"):
                self.peticiones_version += 1
                return _Resp(200, cuerpo)
            return await _Cliente.get(self, url, params, headers)

    raro = _Raro(_paginas(2, 10, 20))
    m = _matching(raro)
    pid = uuid.uuid4()

    await m._fetch_full_feed(pid)
    await m._fetch_full_feed(pid)
    assert raro.peticiones_pagina == 4, "reutilizó caché con una versión no fiable"
    assert core_client._feed_cache == {}
    assert cliente.peticiones_pagina == 0
