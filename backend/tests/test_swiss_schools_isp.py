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

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []


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
