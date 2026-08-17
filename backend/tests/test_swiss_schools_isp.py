"""Tests para SwissSchoolsISPScraper (Workday API del grupo ISP).

Regresión clave: ISP extiende `BaseJobProvider` (no `BaseScraper`), así que el
presupuesto de páginas (`_pages_budget`) debe existir en la base común. Antes
vivía solo en `BaseScraper` y `_fetch_workday` crasheaba con AttributeError.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from scrapers.swiss_schools_isp import SwissSchoolsISPScraper
from utils import fetch_diagnostics as diag


def _workday_response(postings: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value={"jobPostings": postings})
    return resp


class TestISPPagesBudget:
    """El presupuesto heredado de BaseJobProvider funciona en ISP."""

    def test_pages_budget_available(self):
        scraper = SwissSchoolsISPScraper()
        # Sin inyección → MAX_PAGES (regresión del AttributeError).
        assert scraper._pages_budget() == SwissSchoolsISPScraper.MAX_PAGES

    def test_injected_budget_clamped_to_max_pages(self):
        scraper = SwissSchoolsISPScraper()
        scraper._max_pages_this_run = 99
        assert scraper._pages_budget() == SwissSchoolsISPScraper.MAX_PAGES

    def test_injected_budget_limits_workday_pages(self):
        scraper = SwissSchoolsISPScraper()
        scraper._max_pages_this_run = 1
        # Página llena (PAGE_SIZE postings) → sin presupuesto pediría la 2ª.
        full_page = [
            {
                "title": f"Teacher {i}",
                "locationsText": "Mosaic School / Ecole Mosaic",
                "externalPath": f"/job/{i}",
                "bulletFields": [f"JR{i}"],
            }
            for i in range(SwissSchoolsISPScraper.PAGE_SIZE)
        ]

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(full_page),
        ) as mock_call:
            import asyncio

            jobs = asyncio.run(scraper.fetch_jobs(""))

        # Con presupuesto=1, cada colegio pide exactamente 1 página (no la 2ª
        # pese a venir llena). Sin acoplar al nº de colegios de la config.
        assert mock_call.await_count == len(scraper._schools)
        assert len(jobs) >= 1


class TestISPFetch:
    @pytest.mark.asyncio
    async def test_fetch_normalizes_mosaic_jobs(self):
        scraper = SwissSchoolsISPScraper()
        postings = [
            {
                "title": "Primary Teacher",
                "locationsText": "Mosaic School / Ecole Mosaic, Geneva",
                "externalPath": "/job/Primary-Teacher_JR123",
                "bulletFields": ["JR123"],
            }
        ]

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert len(jobs) == 1
        job = jobs[0]
        assert job["source"] == "swiss_schools_isp"
        assert job["title"] == "Primary Teacher"
        assert job["language"] == "en"
        assert "myworkdayjobs.com" in job["url"]
        assert job["hash"]

    @pytest.mark.asyncio
    async def test_fetch_filters_out_other_schools(self):
        # locationsText sin el school_filter ("mosaic") se descarta.
        scraper = SwissSchoolsISPScraper()
        postings = [
            {
                "title": "Teacher Elsewhere",
                "locationsText": "Some Other Campus, Zurich",
                "externalPath": "/job/x",
                "bulletFields": ["JR999"],
            }
        ]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        # G2: el tenant de Workday es COMPARTIDO — objetos válidos con 0 matches
        # del filtro de colegio es lo normal, no un fallo. Por eso el guard de
        # "página sin objetos" debe mirar el TIPO (`parseable`) y NUNCA el
        # filtro (`results`): si mirara el filtro, este vacío legítimo saldría
        # `error` con issue y la fuente seca no podría rehabilitarse.
        assert diag.issues() == []
        assert diag.classify(0, diag.issues()) == "empty"


class TestISPFetchDiagnostics:
    """VD.10 (H1) — ISP no hereda de BaseScraper y hacía bypass de la costura
    `_request_with_retry`: sus fallos de descarga no registraban NADA y
    `classify(0, [])` devolvía `empty` — la fuente ROTA se presentaba como
    fuente SECA. Cada test afirma el veredicto final, no solo el issue."""

    @pytest.mark.asyncio
    async def test_network_error_records_issue_and_is_error(self):
        scraper = SwissSchoolsISPScraper()
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Workday down"),
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        # Un issue por colegio roto (cada uno corta con `break` tras registrar).
        assert len(issues) == len(scraper._schools)
        assert issues[0].kind == diag.KIND_NETWORK
        assert "myworkdayjobs.com" in issues[0].url
        assert "ConnectError" in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    async def test_http_500_records_issue_and_is_error(self):
        scraper = SwissSchoolsISPScraper()
        resp = MagicMock()
        resp.status_code = 500
        diag.begin()

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=resp
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == len(scraper._schools)
        assert issues[0].kind == diag.KIND_HTTP
        assert issues[0].status == 500
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    async def test_invalid_json_body_records_issue_and_is_error(self):
        # 200 con cuerpo no-JSON (redeploy/WAF sirviendo HTML): antes lanzaba
        # sin proteger; ahora es fallo visible de descarga, no un board vacío.
        scraper = SwissSchoolsISPScraper()
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(
            side_effect=json.JSONDecodeError("Expecting value", "<html>", 0)
        )
        diag.begin()

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=resp
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == len(scraper._schools)
        assert issues[0].kind == diag.KIND_NETWORK
        assert "JSONDecodeError" in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    async def test_non_utf8_body_records_issue_and_is_error(self):
        """Fase 3 r3/H4: un 200 cuyo cuerpo no es UTF-8 (b'\\xff') hace que
        resp.json() lance UnicodeDecodeError — subclase de ValueError pero NO
        de JSONDecodeError — que ESCAPABA del parser: 0 issues y el run salía
        `empty` (falso vacío, G1). Misma rama y mismo veredicto que el JSON
        malformado."""
        scraper = SwissSchoolsISPScraper()
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(
            side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        )
        diag.begin()

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=resp
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == len(scraper._schools)
        assert issues[0].kind == diag.KIND_NETWORK
        assert "UnicodeDecodeError" in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    async def test_anidamiento_extremo_registra_issue_y_es_error(self):
        """No bloqueante r6: la pata RecursionError del except estaba sin
        fijar — estrecharla a solo ValueError sobrevivía a toda la batería.
        Un 200 cuyo cuerpo son 100k corchetes anidados hace que el scanner C
        de json lance RecursionError (NO ValueError): no debe escapar y
        registra exactamente un issue por colegio, veredicto `error` (G1)."""
        scraper = SwissSchoolsISPScraper()
        nested_body = '{"jobPostings": ' + "[" * 100_000 + "]" * 100_000 + "}"
        resp = MagicMock()
        resp.status_code = 200
        # side_effect con json REAL: el RecursionError sale del scanner C,
        # no de un mock que lo simule.
        resp.json = MagicMock(side_effect=lambda: json.loads(nested_body))
        diag.begin()

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=resp
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == len(scraper._schools)
        assert issues[0].kind == diag.KIND_NETWORK
        assert "RecursionError" in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [None, ["not", "an", "object"]])
    async def test_json_no_objeto_registra_issue_y_es_error(self, body):
        """Fase 4 r4/R3-2: un 200 con JSON válido pero NO-objeto (`null`, una
        lista) hacía que `data.get(...)` lanzara AttributeError que escapaba
        de fetch_jobs con 0 issues (letra de G1). `utils.http` ya trata el
        "200 con cuerpo JSON null"; este es su camino gemelo — mismo guard
        que financejobs y thehub."""
        scraper = SwissSchoolsISPScraper()
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value=body)
        diag.begin()

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=resp
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == len(scraper._schools)
        assert issues[0].kind == diag.KIND_NETWORK
        assert "not an object" in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "postings",
        [
            None,  # data.get devuelve el null EXISTENTE, no el default
            "maintenance",  # string donde va la lista
            {"nested": True},  # objeto donde va la lista
        ],
    )
    async def test_job_postings_degenerado_registra_issue_y_es_error(self, postings):
        """Fase 5 r5/H2: el isinstance del nivel superior no cubría
        `jobPostings` degenerado — el TypeError/AttributeError del bucle
        ESCAPABA de fetch_jobs con 0 issues (letra de G1). La API real de
        Workday devuelve lista SIEMPRE (sonda: incluida la búsqueda vacía)."""
        scraper = SwissSchoolsISPScraper()
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value={"jobPostings": postings})
        diag.begin()

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=resp
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == len(scraper._schools)
        assert issues[0].kind == diag.KIND_NETWORK
        assert "jobPostings is not a list" in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    async def test_job_postings_sin_objetos_registra_issue_y_es_error(self):
        """Fase 5 r5/H2: lista NO vacía sin un solo objeto ([1, 2]) — antes
        `p.get` lanzaba AttributeError que escapaba; con solo el `continue`
        saldría `empty` con 0 issues (material). El guard mira el TIPO, no el
        filtro de colegio: 0 matches con objetos reales sigue siendo legítimo."""
        scraper = SwissSchoolsISPScraper()
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response([1, 2]),
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == len(scraper._schools)
        assert issues[0].kind == diag.KIND_NETWORK
        assert "ninguno" in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    async def test_item_degenerado_degrada_el_item_no_la_pagina(self):
        """Fase 5 r5/H2: un elemento no-objeto entre objetos reales degrada
        ESE item, nunca la página — las ofertas válidas se conservan."""
        scraper = SwissSchoolsISPScraper()
        postings = [
            "garbage",
            {
                "title": "Primary Teacher",
                "locationsText": "Mosaic School / Ecole Mosaic, Geneva",
                "externalPath": "/job/Primary-Teacher_JR123",
                "bulletFields": ["JR123"],
            },
        ]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert len(jobs) == 1
        assert jobs[0]["title"] == "Primary Teacher"
        assert diag.issues() == []

    @pytest.mark.asyncio
    async def test_job_postings_vacio_es_empty_sin_issue(self):
        """G2 (NO puede romperse): `jobPostings: []` es la búsqueda vacía REAL
        de Workday (`total: 0`) — vacío legítimo, CERO issues."""
        scraper = SwissSchoolsISPScraper()
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response([]),
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        assert diag.issues() == []
        assert diag.classify(len(jobs), diag.issues()) == "empty"
