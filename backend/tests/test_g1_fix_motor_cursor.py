"""Regresiones de la auditoría G1 — motor de scraping y cursor incremental.

- P2-4: un run parcialmente fallido (error en la página N con cosecha de las
  previas) salía `ok` y el cursor aprendía la página 1 → el siguiente run
  hacía early-stop y las novedades hundidas en la página 2+ se perdían para
  siempre (la variante restante de ebb2c51).
- P3-6: `len(stubs) < PAGE_SIZE` usaba stubs PARSEADOS como proxy de fin de
  paginación: una página llena con 1 anuncio no parseable descartaba el
  resto del listado sin issue.
- P3-17: los duplicados fuzzy no contaban como actividad → agregadores
  sindicados acumulaban consecutive_empty_runs y entraban en backoff.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from bs4 import BeautifulSoup
from sqlalchemy import select

from config import settings
from models.source_cursor import SourceCursor
from services.job_service import BaseJobProvider
from services.scraper_engine import BaseScraper
from tasks.scraping_tasks import _fetch_scrapers_async


class _EngineScraper(BaseScraper):
    SOURCE_NAME = "g1_engine_test"
    LISTING_URL = "https://example.com/jobs"
    RATE_LIMIT_SECONDS = 0.0
    MAX_PAGES = 3
    NEEDS_PLAYWRIGHT = False
    FETCH_DETAILS = False
    PAGE_SIZE = 2
    MAX_RETRIES = 0
    RETRY_BACKOFF_SECONDS = 0.0

    def build_listing_url(self, page: int, query: str) -> str:
        return f"{self.LISTING_URL}?page={page}"

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        return [
            {
                "title": el.get_text(strip=True),
                "url": f"https://example.com/{el.get_text(strip=True)}",
            }
            for el in soup.select(".job")
        ]

    def parse_job_detail(self, soup: BeautifulSoup) -> dict:
        return {}

    def normalize_job(self, raw: dict) -> dict:  # pragma: no cover - no usado
        return raw


def _page(n_jobs: int) -> MagicMock:
    html = (
        "<html><body>"
        + "".join(f'<div class="job">J{i}</div>' for i in range(n_jobs))
        + "</body></html>"
    )
    resp = MagicMock()
    resp.status_code = 200
    resp.text = html
    return resp


class TestP24StopReasonError:
    @pytest.mark.asyncio
    async def test_error_en_pagina_2_marca_el_run_con_hambre(self):
        """Cosecha parcial + error de red → _stop_reason='error'."""
        scraper = _EngineScraper()
        calls = iter([_page(2), httpx.ConnectError("boom")])

        async def _side_effect(coro):
            item = next(calls)
            if isinstance(item, Exception):
                raise item
            return item

        with patch.object(scraper._circuit, "call", side_effect=_side_effect):
            jobs = await scraper._scrape_with_httpx("")

        assert len(jobs) == 2, "lo cosechado antes del fallo se conserva"
        assert scraper._stop_reason == "error"

    @pytest.mark.asyncio
    async def test_non_200_marca_el_run_con_hambre(self):
        scraper = _EngineScraper()
        bad = MagicMock()
        bad.status_code = 500
        bad.text = "err"
        calls = iter([_page(2), bad])

        async def _side_effect(coro):
            return next(calls)

        with (
            patch.object(scraper._circuit, "call", side_effect=_side_effect),
            patch.object(scraper, "_report_block", new=AsyncMock()),
        ):
            jobs = await scraper._scrape_with_httpx("")

        assert len(jobs) == 2
        assert scraper._stop_reason == "error"

    @pytest.mark.asyncio
    async def test_fin_limpio_no_marca_error(self):
        """Página corta legítima → run saciado, _stop_reason None."""
        scraper = _EngineScraper()
        calls = iter([_page(2), _page(1)])

        async def _side_effect(coro):
            return next(calls)

        with patch.object(scraper._circuit, "call", side_effect=_side_effect):
            jobs = await scraper._scrape_with_httpx("")

        assert len(jobs) == 3
        assert scraper._stop_reason is None


class _WideScraper(_EngineScraper):
    SOURCE_NAME = "g1_wide_test"
    PAGE_SIZE = 20
    MAX_PAGES = 3


class TestP36PaginaCortaPorParseo:
    @pytest.mark.asyncio
    async def test_deficit_de_un_anuncio_no_corta_la_paginacion(self):
        """G1/P3-6: 19/20 stubs (1 no parseó) debe seguir a la página 2."""
        scraper = _WideScraper()
        calls = iter([_page(19), _page(0)])
        n_calls = []

        async def _side_effect(coro):
            n_calls.append(1)
            return next(calls)

        with patch.object(scraper._circuit, "call", side_effect=_side_effect):
            jobs = await scraper._scrape_with_httpx("")

        assert len(jobs) == 19
        assert len(n_calls) == 2, "la página siguiente (vacía) corta limpio"

    @pytest.mark.asyncio
    async def test_pagina_claramente_corta_sigue_cortando(self):
        scraper = _WideScraper()
        calls = iter([_page(7)])
        n_calls = []

        async def _side_effect(coro):
            n_calls.append(1)
            return next(calls)

        with patch.object(scraper._circuit, "call", side_effect=_side_effect):
            jobs = await scraper._scrape_with_httpx("")

        assert len(jobs) == 7
        assert len(n_calls) == 1, "un fin de listado real no paga página extra"


# ---------------------------------------------------------------------------
# Nivel task: el cursor no aprende de un run con hambre (P2-4) y los dupes
# cuentan como actividad (P3-17).
# ---------------------------------------------------------------------------


def _sample_job(title: str, company: str, url: str, source: str) -> dict:
    return {
        "hash": f"h_{title}_{company}_{url}"[:32].ljust(32, "0"),
        "source": source,
        "title": title,
        "company": company,
        "url": url,
        "location": "Bern",
        "canton": "BE",
        "description": "Interessante Stelle in Bern.",
        "description_snippet": "Interessante...",
        "remote": False,
        "tags": ["administration"],
        "logo": None,
        "salary_min_chf": None,
        "salary_max_chf": None,
        "salary_original": None,
        "salary_currency": None,
        "salary_period": None,
        "language": None,
        "seniority": None,
        "contract_type": None,
        "employment_type": None,
    }


def _make_mock_scraper(source_name: str, jobs: list[dict], stop_reason=None):
    scraper = MagicMock()
    scraper.get_source_name.return_value = source_name
    scraper.fetch_jobs = AsyncMock(return_value=jobs)
    scraper.job_identity = BaseJobProvider.job_identity
    scraper.PAGE_SIZE = 20
    scraper.MAX_PAGES = 5
    scraper._stop_reason = stop_reason
    scraper._max_pages_this_run = None
    return scraper


def _mock_session_factory(db_session):
    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


@pytest.mark.asyncio
class TestP24CursorNoAprendeDeRunConHambre:
    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_run_con_hambre_no_toca_el_cursor(
        self, mock_scrapers, monkeypatch, db_session
    ):
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", True)
        monkeypatch.setattr(settings, "CRAWLER_BUDGET_ENABLED", False)

        source = "g1_hungry_src"
        jobs = [_sample_job("Job A", "Acme", "http://g1h.test/a", source)]
        mock_scrapers.return_value = [
            _make_mock_scraper(source, jobs, stop_reason="error")
        ]

        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            await _fetch_scrapers_async()

        cursor = (
            await db_session.execute(
                select(SourceCursor).where(SourceCursor.source_key == source)
            )
        ).scalar_one_or_none()
        # El cursor no aprende NADA de un run con hambre: sin fila, o fila
        # sin las identidades de la página parcial.
        if cursor is not None:
            assert "http://g1h.test/a" not in (cursor.recent_identities or [])

    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_run_saciado_si_actualiza_el_cursor(
        self, mock_scrapers, monkeypatch, db_session
    ):
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", True)
        monkeypatch.setattr(settings, "CRAWLER_BUDGET_ENABLED", False)

        source = "g1_full_src"
        jobs = [_sample_job("Job B", "Acme", "http://g1f.test/b", source)]
        mock_scrapers.return_value = [_make_mock_scraper(source, jobs)]

        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            await _fetch_scrapers_async()

        cursor = (
            await db_session.execute(
                select(SourceCursor).where(SourceCursor.source_key == source)
            )
        ).scalar_one()
        assert "http://g1f.test/b" in cursor.recent_identities


@pytest.mark.asyncio
class TestP317DupesSonActividad:
    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_run_solo_de_dupes_no_cuenta_como_vacio(
        self, mock_scrapers, monkeypatch, db_session
    ):
        """G1/P3-17: un agregador cuyo contenido dedupea cross-source es
        PRODUCTIVO: no debe acumular consecutive_empty_runs."""
        from services.job_repository import JobRepository

        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", True)
        monkeypatch.setattr(settings, "CRAWLER_BUDGET_ENABLED", False)

        from services.deduplicator import Deduplicator

        # Canónica previa de OTRA fuente con el mismo título+empresa (fuzzy).
        repo = JobRepository(db_session)
        canonical = _sample_job(
            "Syndicated Role", "SameCo", "http://other.test/1", "otra_fuente"
        )
        canonical["hash"] = "g1dupes-canonical".ljust(32, "0")
        canonical["fuzzy_hash"] = Deduplicator.compute_fuzzy_hash(
            "Syndicated Role", "SameCo"
        )
        await repo.upsert_job(canonical)
        await db_session.commit()

        source = "g1_dupes_src"
        incoming = _sample_job("Syndicated Role", "SameCo", "http://g1d.test/1", source)
        incoming["hash"] = "g1dupes-incoming".ljust(32, "0")
        jobs = [incoming]
        mock_scrapers.return_value = [_make_mock_scraper(source, jobs)]

        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            summary = await _fetch_scrapers_async()

        assert summary["dupes"] >= 1
        cursor = (
            await db_session.execute(
                select(SourceCursor).where(SourceCursor.source_key == source)
            )
        ).scalar_one()
        assert (cursor.consecutive_empty_runs or 0) == 0, (
            "el duplicado fuzzy ES actividad de la fuente"
        )
