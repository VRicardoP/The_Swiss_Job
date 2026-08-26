"""G5 — familia de la ESTRUCTURA del cuerpo JSON en la cosecha.

- **P3-3**: `nav_arbeidsplassen` endureció el envoltorio `hits` (G4/P2-8) y NO
  el nivel interno `hits.hits`. Un `hits.hits` que llegara como dict o string
  producía un `AttributeError` que ESCAPABA de `fetch_jobs`. No era muerte
  silenciosa —el `except Exception` de `tasks/fetch_tasks.py` fabrica un
  `FetchIssue` y fuerza `OUTCOME_ERROR`—, pero las ofertas ya cosechadas de la
  faceta anterior se perdían ENTERAS en vez de degradar parcialmente, y el
  panel recibía un `AttributeError` en vez del «200 con estructura desconocida»
  legible que el propio commit se propuso dar.
- **P3-4**: dos contratos OPUESTOS para `{clave: null}` dentro del mismo
  commit. `careerjet` lo toleraba (`data.get("jobs") or []` ⇒ `empty`, con el
  comentario de G3/P2-6 que lo declara legítimo) mientras `json_items` lo
  declaraba fallo de estructura (⇒ `error`): 9 de 11 providers cambiaron de
  contrato al adoptar el helper. Si alguna de esas nueve APIs devuelve
  `clave: null` al agotar páginas, la fuente sale `error` en cada run y dispara
  alerta a los `SOURCE_HEALTH_ERROR_STREAK` runs — falso positivo de salud.
"""

import httpx
import pytest

from providers.nav_arbeidsplassen import NavArbeidsplassenProvider
from utils import fetch_diagnostics as diag


class TestP34UnSoloContratoParaLaClaveNula:
    @pytest.mark.parametrize(
        "body",
        [
            {"jobs": None},
            {"items": None, "total": 0},
            {"data": None},
        ],
    )
    def test_clave_presente_con_null_es_pagina_vacia_y_no_se_registra(self, body):
        key = next(iter(body))
        diag.begin()
        assert diag.json_items(body, "http://x", "prov", key=key) == []
        assert diag.issues() == [], (
            f"{{{key!r}: null}} sale como fallo de estructura: la fuente irá a "
            "`error` en cada run y disparará alerta de salud"
        )

    def test_la_clave_AUSENTE_sigue_siendo_fallo_de_estructura(self):
        """Lo que G4/P2-8 quería cazar (la clave renombrada) no se afloja."""
        diag.begin()
        assert (
            diag.json_items({"resultados": []}, "http://x", "prov", key="jobs") is None
        )
        assert len(diag.issues()) == 1
        assert "falta" in diag.issues()[0].detail

    def test_la_clave_de_TIPO_equivocado_sigue_siendo_fallo(self):
        diag.begin()
        assert diag.json_items({"jobs": "abc"}, "http://x", "prov", key="jobs") is None
        assert len(diag.issues()) == 1
        assert "str" in diag.issues()[0].detail

    def test_el_vacio_legitimo_sigue_sin_registrar(self):
        diag.begin()
        assert diag.json_items({"jobs": []}, "http://x", "prov", key="jobs") == []
        assert diag.issues() == []


def _nav_transport(bodies: list[dict]):
    """Devuelve un cuerpo distinto por petición (facetas + páginas)."""
    llamadas = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = min(llamadas["n"], len(bodies) - 1)
        llamadas["n"] += 1
        return httpx.Response(200, json=bodies[i])

    return httpx.MockTransport(handler), llamadas


@pytest.mark.asyncio
class TestP33NavElNivelInternoTambienSeEndurece:
    @pytest.mark.parametrize(
        "hits_interno",
        [
            {"a": {"_id": "x"}},  # dict donde se esperaba lista
            "abc",  # string
            123,  # entero
        ],
    )
    async def test_un_hits_hits_ilegible_no_lanza_AttributeError(
        self, monkeypatch, hits_interno
    ):
        provider = NavArbeidsplassenProvider()
        transport, _ = _nav_transport([{"hits": {"hits": hits_interno, "total": 0}}])

        real = httpx.AsyncClient

        def _client(*a, **kw):
            kw["transport"] = transport
            return real(*a, **kw)

        monkeypatch.setattr(httpx, "AsyncClient", _client)

        diag.begin()
        jobs = await provider.fetch_jobs("")

        assert jobs == [], "no debe lanzar: el AttributeError escapaba de fetch_jobs"
        assert diag.issues(), (
            "un `hits.hits` ilegible sale como página vacía legítima y la "
            "fuente se da por sana"
        )
        assert any(
            "estructura desconocida" in (i.detail or "") for i in diag.issues()
        ), "el panel recibe un AttributeError en vez de un detalle legible"

    async def test_hits_hits_nulo_es_fin_de_faceta_y_no_error(self, monkeypatch):
        """Contrato de P3-4 aplicado también aquí: `null` = página vacía."""
        provider = NavArbeidsplassenProvider()
        transport, _ = _nav_transport([{"hits": {"hits": None, "total": 0}}])

        real = httpx.AsyncClient

        def _client(*a, **kw):
            kw["transport"] = transport
            return real(*a, **kw)

        monkeypatch.setattr(httpx, "AsyncClient", _client)

        diag.begin()
        assert await provider.fetch_jobs("") == []
        assert diag.issues() == []

    async def test_las_ofertas_de_la_faceta_ANTERIOR_no_se_pierden(self, monkeypatch):
        """La degradación es PARCIAL: lo cosechado antes del fallo sobrevive."""
        provider = NavArbeidsplassenProvider()
        buena = {
            "hits": {
                "hits": [
                    {
                        "_id": "nav-1",
                        "_source": {
                            "title": "Utvikler",
                            "employer": {"name": "Acme"},
                            "properties": {"jobtitle": "Utvikler"},
                            "locationList": [{"city": "Oslo"}],
                        },
                    }
                ],
                "total": {"value": 1},
            }
        }
        rota = {"hits": {"hits": "abc"}}
        transport, _ = _nav_transport([buena, rota])

        real = httpx.AsyncClient

        def _client(*a, **kw):
            kw["transport"] = transport
            return real(*a, **kw)

        monkeypatch.setattr(httpx, "AsyncClient", _client)

        diag.begin()
        jobs = await provider.fetch_jobs("")

        assert len(jobs) >= 1, (
            "el AttributeError tiraba las ofertas ya cosechadas de la faceta "
            "anterior en vez de degradar parcialmente"
        )
        assert diag.issues(), "y aun así el fallo de estructura debe registrarse"
