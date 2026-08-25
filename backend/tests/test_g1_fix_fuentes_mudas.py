"""Regresiones de la auditoría G1 — fuentes mudas ante fallos (clase V.0/VD.7).

- P1-1: publicjobs era el único provider sin fetch_with_retry ni diag.record:
  todo 4xx/5xx/parseo roto salía como `empty` (sequía legítima) sin alerta.
- P2-5: tes no registraba issue ante fallos de estructura (__NEXT_DATA__
  ausente, JSON ilegible, ruta tRPC cambiada) → fuente ROTA presentada como
  seca; además `queries[0]` era un índice mágico.
"""

import json

import httpx
import pytest
from bs4 import BeautifulSoup

from providers.publicjobs import PublicJobsProvider
from scrapers.tes import TESScraper
from utils import fetch_diagnostics as diag


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    """Sin esperas reales de backoff en los reintentos de utils.http."""

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("utils.http.asyncio.sleep", _no_sleep)


class TestP11PublicjobsMudo:
    @pytest.mark.asyncio
    async def test_http_500_sale_como_error_no_empty(self, monkeypatch):
        """G1/P1-1: un 500 debe registrar issue → classify `error`, no `empty`."""

        async def _fake_get(self, url, **kwargs):
            return httpx.Response(500, text="boom")

        monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

        diag.begin()
        jobs = await PublicJobsProvider().fetch_jobs("")
        issues = diag.issues()

        assert jobs == []
        assert issues, "el fallo HTTP debe quedar registrado en fetch_diagnostics"
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    async def test_json_ilegible_sale_como_error(self, monkeypatch):
        async def _fake_get(self, url, **kwargs):
            return httpx.Response(200, text="<html>not json</html>")

        monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

        diag.begin()
        jobs = await PublicJobsProvider().fetch_jobs("")
        issues = diag.issues()

        assert jobs == []
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    async def test_estructura_sveltekit_desconocida_sale_como_error(self, monkeypatch):
        """Un 200 con JSON válido pero sin 'nodes' es estructura rota, no sequía."""

        async def _fake_get(self, url, **kwargs):
            return httpx.Response(200, json={"renamed": []})

        monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

        diag.begin()
        jobs = await PublicJobsProvider().fetch_jobs("")
        issues = diag.issues()

        assert jobs == []
        assert diag.classify(len(jobs), issues) == "error"


def _next_data_html(payload: dict) -> BeautifulSoup:
    html = (
        "<html><body><script id='__NEXT_DATA__' type='application/json'>"
        + json.dumps(payload)
        + "</script></body></html>"
    )
    return BeautifulSoup(html, "html.parser")


def _tes_job(title="Teacher of English"):
    return {
        "title": title,
        "employer": {"name": "Some School"},
        "canonicalUrl": "/jobs/some-school/teacher",
        "shortDescription": "desc",
        "displayLocation": "Zug",
    }


class TestP25TesMudo:
    def test_sin_next_data_registra_issue(self):
        """G1/P2-5: sin __NEXT_DATA__ el run debe salir `error`, no `empty`."""
        diag.begin()
        stubs = TESScraper().parse_listing_page(
            BeautifulSoup("<html><body></body></html>", "html.parser")
        )
        issues = diag.issues()
        assert stubs == []
        assert diag.classify(0, issues) == "error"

    def test_ruta_trpc_cambiada_registra_issue(self):
        diag.begin()
        soup = _next_data_html({"props": {"pageProps": {"renombrado": {}}}})
        stubs = TESScraper().parse_listing_page(soup)
        assert stubs == []
        assert diag.classify(0, diag.issues()) == "error"

    def test_query_de_jobs_no_es_la_primera(self):
        """G1/P2-5: `queries[0]` era un índice mágico — la query de jobs se
        busca aunque otra query ocupe la primera posición."""
        payload = {
            "props": {
                "pageProps": {
                    "trpcState": {
                        "json": {
                            "queries": [
                                {"state": {"data": {"filters": {}}}},
                                {"state": {"data": {"jobs": [_tes_job()]}}},
                            ]
                        }
                    }
                }
            }
        }
        diag.begin()
        stubs = TESScraper().parse_listing_page(_next_data_html(payload))
        assert len(stubs) == 1
        assert stubs[0]["title"] == "Teacher of English"
        assert diag.issues() == []

    def test_pagina_valida_con_jobs_vacios_es_empty_legitimo(self):
        payload = {
            "props": {
                "pageProps": {
                    "trpcState": {
                        "json": {"queries": [{"state": {"data": {"jobs": []}}}]}
                    }
                }
            }
        }
        diag.begin()
        stubs = TESScraper().parse_listing_page(_next_data_html(payload))
        assert stubs == []
        assert diag.issues() == [], "jobs: [] es fin de paginación, no fallo"
