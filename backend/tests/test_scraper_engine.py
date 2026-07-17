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
    MAX_RETRIES = 0  # Sin reintentos por defecto: tests rápidos y deterministas
    RETRY_BACKOFF_SECONDS = 0.0

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


class TestBaseScraperPageBudget:
    """Presupuesto dinámico de páginas inyectado por el pipeline."""

    def test_no_budget_uses_max_pages(self):
        scraper = ConcreteScraper()
        assert scraper._pages_budget() == ConcreteScraper.MAX_PAGES

    def test_budget_clamped_to_max_pages(self):
        scraper = ConcreteScraper()
        scraper._max_pages_this_run = 99
        assert scraper._pages_budget() == ConcreteScraper.MAX_PAGES

    def test_budget_never_below_one_page(self):
        scraper = ConcreteScraper()
        scraper._max_pages_this_run = 0
        assert scraper._pages_budget() == 1

    @pytest.mark.asyncio
    async def test_injected_budget_limits_httpx_pages(self):
        scraper = ConcreteScraper()
        scraper._max_pages_this_run = 1
        # Página llena (PAGE_SIZE=2): sin presupuesto seguiría a la página 2.
        html = (
            '<html><body><div class="job">A</div><div class="job">B</div></body></html>'
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=mock_response
        ) as mock_call:
            result = await scraper._scrape_with_httpx("")

        assert len(result) == 2
        assert mock_call.await_count == 1  # solo la página presupuestada


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

    def test_default_headers_are_realistic(self):
        # Las cabeceras por defecto deben imitar a un Chrome real (anti-detección).
        h = ConcreteScraper.DEFAULT_HEADERS
        assert "Chrome/" in h["User-Agent"]
        assert "Sec-CH-UA" in h


# ---------------------------------------------------------------------------
# Anti-detección: reintentos, soft-block, jitter, proxy y Playwright stealth
# ---------------------------------------------------------------------------


def _resp(status_code: int, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    return r


_JOB_HTML = '<html><body><div class="job">Dev</div></body></html>'


class RetryScraper(ConcreteScraper):
    """Scraper con reintentos activos pero sin espera real (backoff 0)."""

    SOURCE_NAME = "retry_scraper"
    MAX_PAGES = 1
    MAX_RETRIES = 2
    RETRY_BACKOFF_SECONDS = 0.0
    JITTER_RATIO = 0.0


class TestBaseScraperRetry:
    @pytest.mark.asyncio
    async def test_retries_transient_error_then_succeeds(self):
        scraper = RetryScraper()
        scraper._circuit.call = AsyncMock(
            side_effect=[httpx.ConnectError("boom"), _resp(200, _JOB_HTML)]
        )

        result = await scraper._scrape_with_httpx("")
        assert len(result) == 1
        assert scraper._circuit.call.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_503_then_succeeds(self):
        scraper = RetryScraper()
        scraper._circuit.call = AsyncMock(
            side_effect=[_resp(503), _resp(200, _JOB_HTML)]
        )

        result = await scraper._scrape_with_httpx("")
        assert len(result) == 1
        assert scraper._circuit.call.call_count == 2

    @pytest.mark.asyncio
    async def test_exhausts_retries_on_persistent_error(self):
        scraper = RetryScraper()
        scraper._circuit.call = AsyncMock(side_effect=httpx.ConnectError("down"))

        result = await scraper._scrape_with_httpx("")
        assert result == []
        # 1 intento inicial + MAX_RETRIES reintentos
        assert scraper._circuit.call.call_count == 3

    @pytest.mark.asyncio
    async def test_persistent_503_reports_block_after_retries(self):
        scraper = RetryScraper()
        scraper._report_block = AsyncMock()
        scraper._circuit.call = AsyncMock(return_value=_resp(503))

        result = await scraper._scrape_with_httpx("")
        assert result == []
        assert scraper._circuit.call.call_count == 3
        scraper._report_block.assert_called_once_with(503)

    @pytest.mark.asyncio
    async def test_open_circuit_is_terminal_not_retried(self):
        # Un circuito abierto es terminal: NO se reintenta pese a MAX_RETRIES=2.
        from services.circuit_breaker import CircuitBreakerOpen

        scraper = RetryScraper()
        scraper._circuit.call = AsyncMock(
            side_effect=CircuitBreakerOpen("retry_scraper", 0)
        )

        result = await scraper._scrape_with_httpx("")
        assert result == []
        assert scraper._circuit.call.call_count == 1


class TestBaseScraperDetailBlock:
    @pytest.mark.asyncio
    async def test_detail_503_does_not_report_block(self):
        # 503 transitorio en detalle NO reporta bloqueo (este path no reintenta).
        scraper = ConcreteScraper()
        scraper._report_block = AsyncMock()
        client = MagicMock()
        client.get = AsyncMock(return_value=_resp(503))

        result = await scraper._fetch_detail_httpx(client, "https://example.com/d")
        assert result is None
        scraper._report_block.assert_not_called()

    @pytest.mark.asyncio
    async def test_detail_403_reports_block(self):
        # 403 sí es un bloqueo deliberado: se reporta a la primera.
        scraper = ConcreteScraper()
        scraper._report_block = AsyncMock()
        client = MagicMock()
        client.get = AsyncMock(return_value=_resp(403))

        result = await scraper._fetch_detail_httpx(client, "https://example.com/d")
        assert result is None
        scraper._report_block.assert_called_once_with(403)


class TestBaseScraperLaunchArgs:
    def test_no_sandbox_included_by_default(self):
        args = ConcreteScraper()._build_launch_args()
        assert "--no-sandbox" in args
        assert any("AutomationControlled" in a for a in args)

    def test_no_sandbox_omitted_when_disabled(self, monkeypatch):
        from config import settings

        monkeypatch.setattr(settings, "SCRAPER_PLAYWRIGHT_NO_SANDBOX", False)
        args = ConcreteScraper()._build_launch_args()
        assert "--no-sandbox" not in args
        # El flag stealth se mantiene siempre, gate o no gate.
        assert any("AutomationControlled" in a for a in args)


class TestBaseScraperSoftBlock:
    @pytest.mark.asyncio
    async def test_soft_block_marker_reports_block(self):
        scraper = ConcreteScraper()
        scraper._report_block = AsyncMock()
        html = "<html><body>Please complete the captcha</body></html>"

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_resp(200, html),
        ):
            result = await scraper._scrape_with_httpx("")

        assert result == []
        scraper._report_block.assert_called_once_with(200)

    @pytest.mark.asyncio
    async def test_clean_empty_page_does_not_report_block(self):
        scraper = ConcreteScraper()
        scraper._report_block = AsyncMock()
        html = "<html><body><p>No results found</p></body></html>"

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_resp(200, html),
        ):
            result = await scraper._scrape_with_httpx("")

        assert result == []
        scraper._report_block.assert_not_called()


class TestBaseScraperDelays:
    @pytest.mark.asyncio
    async def test_rate_limit_delay_within_jitter_bounds(self):
        scraper = ConcreteScraper()  # RATE_LIMIT_SECONDS=0.01, jitter 0.5
        captured = []

        async def fake_sleep(d):
            captured.append(d)

        with patch("services.scraper_engine.asyncio.sleep", new=fake_sleep):
            await scraper._rate_limit_delay()

        assert len(captured) == 1
        assert 0.01 <= captured[0] <= 0.01 * 1.5

    def test_backoff_grows_exponentially(self):
        class _S(ConcreteScraper):
            RETRY_BACKOFF_SECONDS = 1.0
            JITTER_RATIO = 0.0

        s = _S()
        assert s._backoff_delay(0) == 1.0
        assert s._backoff_delay(1) == 2.0
        assert s._backoff_delay(2) == 4.0


class TestBaseScraperProxy:
    def test_no_proxy_by_default(self):
        scraper = ConcreteScraper()
        assert "proxy" not in scraper._build_httpx_kwargs()

    def test_proxy_applied_when_configured(self, monkeypatch):
        from config import settings

        monkeypatch.setattr(settings, "SCRAPER_PROXY_URL", "http://proxy.example:8080")
        scraper = ConcreteScraper()
        kwargs = scraper._build_httpx_kwargs()
        assert kwargs["proxy"] == "http://proxy.example:8080"


# --- Playwright stealth path (con un Playwright falso inyectado) -------------


class _FakeResponse:
    def __init__(self, status: int = 200):
        self.status = status


class _FakePage:
    def __init__(self, html: str, status: int = 200):
        self._html = html
        self._status = status
        self.goto_urls: list[str] = []

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_urls.append(url)
        return _FakeResponse(self._status)

    async def content(self):
        return self._html


class _FakeContext:
    def __init__(self, page: _FakePage):
        self._page = page
        self.init_scripts: list[str] = []
        self.kwargs: dict = {}

    async def add_init_script(self, script):
        self.init_scripts.append(script)

    async def new_page(self):
        return self._page


class _FakeBrowser:
    def __init__(self, context: _FakeContext):
        self._context = context
        self.context_kwargs: dict = {}
        self.closed = False

    async def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        return self._context

    async def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser):
        self._browser = browser
        self.launch_called = False
        self.launch_kwargs: dict = {}
        self.connect_called = False
        self.cdp_url: str | None = None

    async def launch(self, **kwargs):
        self.launch_called = True
        self.launch_kwargs = kwargs
        return self._browser

    async def connect_over_cdp(self, url):
        self.connect_called = True
        self.cdp_url = url
        return self._browser


class _FakePlaywright:
    def __init__(self, chromium: _FakeChromium):
        self.chromium = chromium

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _install_fake_playwright(monkeypatch, html: str, status: int = 200):
    """Inyecta un módulo playwright falso y devuelve (chromium, browser, context, page)."""
    import sys
    import types

    page = _FakePage(html, status)
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    chromium = _FakeChromium(browser)
    pw = _FakePlaywright(chromium)

    mod = types.ModuleType("playwright.async_api")
    mod.async_playwright = lambda: pw
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", mod)
    return chromium, browser, context, page


class PlaywrightScraper(ConcreteScraper):
    SOURCE_NAME = "pw_scraper"
    NEEDS_PLAYWRIGHT = True
    MAX_PAGES = 1
    PAGE_SIZE = 2


class TestBaseScraperPlaywrightStealth:
    @pytest.mark.asyncio
    async def test_launch_applies_stealth(self, monkeypatch):
        from services.scraper_stealth import STEALTH_INIT_SCRIPT

        chromium, browser, context, page = _install_fake_playwright(
            monkeypatch, _JOB_HTML
        )
        scraper = PlaywrightScraper()

        result = await scraper._scrape_with_playwright("")

        assert len(result) == 1
        assert chromium.launch_called is True
        assert chromium.connect_called is False
        assert any("AutomationControlled" in a for a in chromium.launch_kwargs["args"])
        assert context.init_scripts == [STEALTH_INIT_SCRIPT]
        assert "Chrome/" in browser.context_kwargs["user_agent"]
        assert browser.context_kwargs["viewport"]["width"] == 1920
        assert browser.closed is True

    @pytest.mark.asyncio
    async def test_connects_over_cdp_when_configured(self, monkeypatch):
        from config import settings

        monkeypatch.setattr(settings, "SCRAPER_BROWSER_CDP_URL", "ws://remote:9222")
        chromium, _, _, _ = _install_fake_playwright(monkeypatch, _JOB_HTML)
        scraper = PlaywrightScraper()

        result = await scraper._scrape_with_playwright("")

        assert len(result) == 1
        assert chromium.connect_called is True
        assert chromium.cdp_url == "ws://remote:9222"
        assert chromium.launch_called is False

    @pytest.mark.asyncio
    async def test_proxy_passed_to_launch(self, monkeypatch):
        from config import settings

        monkeypatch.setattr(settings, "SCRAPER_PROXY_URL", "http://proxy:3128")
        chromium, _, _, _ = _install_fake_playwright(monkeypatch, _JOB_HTML)
        scraper = PlaywrightScraper()

        await scraper._scrape_with_playwright("")

        assert chromium.launch_kwargs["proxy"] == {"server": "http://proxy:3128"}

    @pytest.mark.asyncio
    async def test_soft_block_detected_in_playwright(self, monkeypatch):
        _install_fake_playwright(
            monkeypatch, "<html>please complete the captcha to continue</html>"
        )
        scraper = PlaywrightScraper()
        scraper._report_block = AsyncMock()

        result = await scraper._scrape_with_playwright("")

        assert result == []
        scraper._report_block.assert_called_once_with(200)
