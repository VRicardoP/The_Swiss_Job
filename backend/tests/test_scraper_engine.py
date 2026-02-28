"""Tests for BaseScraper engine — compliance, rate limiting, mode selection."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from bs4 import BeautifulSoup

from services.scraper_engine import BaseScraper


class ConcreteScraper(BaseScraper):
    """Minimal concrete implementation for testing BaseScraper."""

    SOURCE_NAME = "test_scraper"
    LISTING_URL = "https://example.com/jobs"
    RATE_LIMIT_SECONDS = 0.01  # Fast for tests
    MAX_PAGES = 2
    NEEDS_PLAYWRIGHT = False
    FETCH_DETAILS = False
    PAGE_SIZE = 2

    def build_listing_url(self, page: int, query: str) -> str:
        return f"{self.LISTING_URL}?page={page}"

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        return [
            {"title": el.get_text(strip=True), "url": "https://example.com/job/1"}
            for el in soup.select(".job")
        ]

    def parse_job_detail(self, soup: BeautifulSoup) -> dict:
        return {"description": "Detail page content"}

    def normalize_job(self, raw: dict) -> dict:
        return {
            "hash": self.compute_hash(raw.get("title", ""), "", raw.get("url", "")),
            "source": self.SOURCE_NAME,
            "title": raw.get("title", ""),
            "company": raw.get("company", "Unknown"),
            "location": "",
            "canton": None,
            "description": raw.get("description", ""),
            "description_snippet": self._snippet(raw.get("description", "")),
            "url": raw.get("url", ""),
            "remote": False,
            "tags": [],
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


class TestBaseScraperPreCheck:
    @pytest.mark.asyncio
    async def test_pre_check_allowed(self):
        scraper = ConcreteScraper()
        mock_engine = AsyncMock()
        mock_engine.can_scrape = AsyncMock(return_value=True)

        with patch("database.task_session") as mock_ts:
            mock_ts.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_ts.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch(
                "services.compliance.ComplianceEngine",
                return_value=mock_engine,
            ):
                result = await scraper._pre_check()

        assert result is True
        mock_engine.can_scrape.assert_called_once_with("test_scraper")

    @pytest.mark.asyncio
    async def test_pre_check_blocked(self):
        scraper = ConcreteScraper()
        mock_engine = AsyncMock()
        mock_engine.can_scrape = AsyncMock(return_value=False)

        with patch("database.task_session") as mock_ts:
            mock_ts.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_ts.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch(
                "services.compliance.ComplianceEngine",
                return_value=mock_engine,
            ):
                result = await scraper._pre_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_pre_check_exception_returns_false(self):
        scraper = ConcreteScraper()

        with patch(
            "database.task_session",
            side_effect=Exception("db error"),
        ):
            result = await scraper._pre_check()

        assert result is False


class TestBaseScraperFetchJobs:
    @pytest.mark.asyncio
    async def test_fetch_jobs_skips_when_blocked(self):
        scraper = ConcreteScraper()
        scraper._pre_check = AsyncMock(return_value=False)

        result = await scraper.fetch_jobs("test")
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_jobs_uses_httpx_mode(self):
        scraper = ConcreteScraper()
        scraper._pre_check = AsyncMock(return_value=True)
        scraper._scrape_with_httpx = AsyncMock(
            return_value=[
                {"title": "Dev", "url": "https://example.com/1"},
            ]
        )
        scraper._reset_compliance_blocks = AsyncMock()

        result = await scraper.fetch_jobs("test")
        assert len(result) == 1
        scraper._scrape_with_httpx.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_jobs_uses_playwright_mode(self):
        scraper = ConcreteScraper()
        scraper.NEEDS_PLAYWRIGHT = True
        scraper._pre_check = AsyncMock(return_value=True)
        scraper._scrape_with_playwright = AsyncMock(
            return_value=[
                {"title": "Dev", "url": "https://example.com/1"},
            ]
        )
        scraper._reset_compliance_blocks = AsyncMock()

        result = await scraper.fetch_jobs("test")
        assert len(result) == 1
        scraper._scrape_with_playwright.assert_called_once()


class TestBaseScraperHttpx:
    @pytest.mark.asyncio
    async def test_scrape_with_httpx_parses_pages(self):
        scraper = ConcreteScraper()
        html = '<html><body><div class="job">Developer</div></body></html>'

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await scraper._scrape_with_httpx("")

        assert len(result) == 1
        assert result[0]["title"] == "Developer"

    @pytest.mark.asyncio
    async def test_scrape_with_httpx_stops_on_empty(self):
        scraper = ConcreteScraper()
        html = "<html><body><p>No jobs</p></body></html>"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await scraper._scrape_with_httpx("")

        assert result == []

    @pytest.mark.asyncio
    async def test_scrape_reports_block_on_403(self):
        scraper = ConcreteScraper()
        scraper._report_block = AsyncMock()

        mock_response = MagicMock()
        mock_response.status_code = 403

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await scraper._scrape_with_httpx("")

        assert result == []
        scraper._report_block.assert_called_once_with(403)

    @pytest.mark.asyncio
    async def test_scrape_reports_block_on_429(self):
        scraper = ConcreteScraper()
        scraper._report_block = AsyncMock()

        mock_response = MagicMock()
        mock_response.status_code = 429

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await scraper._scrape_with_httpx("")

        assert result == []
        scraper._report_block.assert_called_once_with(429)

    @pytest.mark.asyncio
    async def test_scrape_handles_circuit_breaker_open(self):
        from services.circuit_breaker import CircuitBreakerOpen

        scraper = ConcreteScraper()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            side_effect=CircuitBreakerOpen("test", 60),
        ):
            result = await scraper._scrape_with_httpx("")

        assert result == []

    @pytest.mark.asyncio
    async def test_scrape_handles_http_error(self):
        scraper = ConcreteScraper()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("connection refused"),
        ):
            result = await scraper._scrape_with_httpx("")

        assert result == []


class TestBaseScraperDetailFetch:
    @pytest.mark.asyncio
    async def test_fetch_detail_success(self):
        scraper = ConcreteScraper()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>Detail</body></html>"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        detail = await scraper._fetch_detail_httpx(
            mock_client, "https://example.com/job/1"
        )
        assert detail == {"description": "Detail page content"}

    @pytest.mark.asyncio
    async def test_fetch_detail_403_reports_block(self):
        scraper = ConcreteScraper()
        scraper._report_block = AsyncMock()

        mock_response = MagicMock()
        mock_response.status_code = 403

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        detail = await scraper._fetch_detail_httpx(
            mock_client, "https://example.com/job/1"
        )
        assert detail is None
        scraper._report_block.assert_called_once_with(403)

    @pytest.mark.asyncio
    async def test_fetch_detail_error_returns_none(self):
        scraper = ConcreteScraper()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("fail"))

        detail = await scraper._fetch_detail_httpx(
            mock_client, "https://example.com/job/1"
        )
        assert detail is None


class TestBaseScraperConfig:
    def test_default_config(self):
        scraper = ConcreteScraper()
        assert scraper.SOURCE_NAME == "test_scraper"
        assert scraper.NEEDS_PLAYWRIGHT is False
        assert scraper.FETCH_DETAILS is False

    def test_circuit_breaker_created(self):
        scraper = ConcreteScraper()
        assert scraper._circuit is not None
        assert scraper._circuit.name == "test_scraper"
