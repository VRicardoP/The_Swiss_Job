"""BaseScraper — abstract base for HTML-scraping job providers.

Extends BaseJobProvider to reuse circuit breaker, normalization pipeline,
deduplication, and stats tracking. Adds rate-limiting, compliance pre-check,
and HTML fetching (httpx for SSR pages, Playwright for JS SPAs).
"""

import asyncio
import logging
from abc import abstractmethod

import httpx
from bs4 import BeautifulSoup

from services.circuit_breaker import CircuitBreakerOpen
from services.job_service import BaseJobProvider

logger = logging.getLogger(__name__)


class BaseScraper(BaseJobProvider):
    """Abstract base for HTML-scraping providers.

    Subclasses must implement:
    - build_listing_url(page, query) -> str
    - parse_listing_page(soup) -> list[dict]
    - parse_job_detail(soup) -> dict
    - normalize_job(raw) -> dict  (inherited contract)

    Class-level config (override per scraper):
    - LISTING_URL: base URL for listing pages
    - RATE_LIMIT_SECONDS: min delay between requests (default 2.0)
    - MAX_PAGES: max pagination depth (default 10)
    - NEEDS_PLAYWRIGHT: True for JS-rendered SPAs (default False)
    - FETCH_DETAILS: fetch individual detail pages (default True)
    - PAGE_SIZE: expected jobs per page (default 20)
    """

    LISTING_URL: str = ""
    RATE_LIMIT_SECONDS: float = 2.0
    MAX_PAGES: int = 10
    NEEDS_PLAYWRIGHT: bool = False
    FETCH_DETAILS: bool = True
    PAGE_SIZE: int = 20

    # More realistic browser headers for scraping
    DEFAULT_HEADERS: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-CH,de;q=0.9,fr;q=0.8,en;q=0.7",
    }

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs via scraping. Overrides BaseJobProvider.fetch_jobs().

        Flow: pre_check -> scrape (httpx or Playwright) -> normalize -> finalize.
        """
        if not await self._pre_check():
            return []

        if self.NEEDS_PLAYWRIGHT:
            all_raw = await self._scrape_with_playwright(query)
        else:
            all_raw = await self._scrape_with_httpx(query)

        results = self._process_raw_jobs(all_raw)

        if results:
            await self._reset_compliance_blocks()

        return self._finalize_fetch(results)

    # ------------------------------------------------------------------
    # Compliance integration
    # ------------------------------------------------------------------

    async def _pre_check(self) -> bool:
        """Verify source compliance before scraping."""
        from database import task_session
        from services.compliance import ComplianceEngine

        try:
            async with task_session() as db:
                engine = ComplianceEngine(db)
                allowed = await engine.can_scrape(self.SOURCE_NAME)
        except Exception as e:
            logger.error("%s compliance pre-check failed: %s", self.SOURCE_NAME, e)
            return False

        if not allowed:
            logger.warning("Scraping disabled for %s (compliance)", self.SOURCE_NAME)
        return allowed

    async def _report_block(self, status_code: int) -> None:
        """Report a block event to the compliance engine."""
        from database import task_session
        from services.compliance import ComplianceEngine

        try:
            async with task_session() as db:
                engine = ComplianceEngine(db)
                await engine.report_block(self.SOURCE_NAME, status_code)
        except Exception as e:
            logger.error("%s failed to report block: %s", self.SOURCE_NAME, e)

    async def _reset_compliance_blocks(self) -> None:
        """Reset consecutive blocks after successful scrape."""
        from database import task_session
        from services.compliance import ComplianceEngine

        try:
            async with task_session() as db:
                engine = ComplianceEngine(db)
                await engine.reset_blocks(self.SOURCE_NAME)
        except Exception as e:
            logger.error("%s failed to reset blocks: %s", self.SOURCE_NAME, e)

    # ------------------------------------------------------------------
    # httpx scraping (for server-rendered pages)
    # ------------------------------------------------------------------

    async def _scrape_with_httpx(self, query: str) -> list[dict]:
        """Fetch listing pages with httpx, parse with BeautifulSoup."""
        all_jobs: list[dict] = []

        async with httpx.AsyncClient(
            headers=self.DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=20.0,
        ) as client:
            for page in range(1, self.MAX_PAGES + 1):
                url = self.build_listing_url(page, query)

                try:
                    response = await self._circuit.call(lambda u=url: client.get(u))
                except (CircuitBreakerOpen, httpx.HTTPError) as e:
                    logger.error(
                        "%s listing page %d error: %s",
                        self.SOURCE_NAME,
                        page,
                        e,
                    )
                    break

                if response.status_code in (403, 429):
                    logger.warning(
                        "%s blocked with HTTP %d on page %d",
                        self.SOURCE_NAME,
                        response.status_code,
                        page,
                    )
                    await self._report_block(response.status_code)
                    break

                if response.status_code != 200:
                    logger.warning(
                        "%s HTTP %d on page %d",
                        self.SOURCE_NAME,
                        response.status_code,
                        page,
                    )
                    break

                soup = BeautifulSoup(response.text, "lxml")
                stubs = self.parse_listing_page(soup)

                if not stubs:
                    break

                if self.FETCH_DETAILS:
                    for stub in stubs:
                        detail_url = stub.get("detail_url")
                        if detail_url:
                            await asyncio.sleep(self.RATE_LIMIT_SECONDS)
                            detail = await self._fetch_detail_httpx(client, detail_url)
                            if detail:
                                stub.update(detail)
                        all_jobs.append(stub)
                else:
                    all_jobs.extend(stubs)

                if len(stubs) < self.PAGE_SIZE:
                    break

                await asyncio.sleep(self.RATE_LIMIT_SECONDS)

        logger.info("%s scraped %d raw jobs", self.SOURCE_NAME, len(all_jobs))
        return all_jobs

    async def _fetch_detail_httpx(
        self, client: httpx.AsyncClient, url: str
    ) -> dict | None:
        """Fetch a single job detail page and parse it."""
        try:
            response = await client.get(url)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "lxml")
                return self.parse_job_detail(soup)
            if response.status_code in (403, 429):
                await self._report_block(response.status_code)
        except (httpx.HTTPError, CircuitBreakerOpen) as e:
            logger.error(
                "%s detail fetch error for %s: %s",
                self.SOURCE_NAME,
                url,
                e,
            )
        return None

    # ------------------------------------------------------------------
    # Playwright scraping (for JS-rendered SPAs)
    # ------------------------------------------------------------------

    async def _scrape_with_playwright(self, query: str) -> list[dict]:
        """Fetch JS-rendered pages with Playwright headless browser."""
        from playwright.async_api import async_playwright

        all_jobs: list[dict] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=self.DEFAULT_HEADERS["User-Agent"],
                locale="de-CH",
            )
            page = await context.new_page()

            try:
                for pg_num in range(1, self.MAX_PAGES + 1):
                    url = self.build_listing_url(pg_num, query)

                    try:
                        response = await page.goto(
                            url, wait_until="networkidle", timeout=30000
                        )
                    except Exception as e:
                        logger.error(
                            "%s Playwright page %d error: %s",
                            self.SOURCE_NAME,
                            pg_num,
                            e,
                        )
                        break

                    if response and response.status in (403, 429):
                        await self._report_block(response.status)
                        break

                    html = await page.content()
                    soup = BeautifulSoup(html, "lxml")
                    stubs = self.parse_listing_page(soup)

                    if not stubs:
                        break

                    all_jobs.extend(stubs)

                    if len(stubs) < self.PAGE_SIZE:
                        break

                    await asyncio.sleep(self.RATE_LIMIT_SECONDS)
            finally:
                await browser.close()

        logger.info(
            "%s scraped %d raw jobs (Playwright)", self.SOURCE_NAME, len(all_jobs)
        )
        return all_jobs

    # ------------------------------------------------------------------
    # Abstract methods — subclasses must implement
    # ------------------------------------------------------------------

    @abstractmethod
    def build_listing_url(self, page: int, query: str) -> str:
        """Build the URL for a specific listing page number."""
        ...

    @abstractmethod
    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        """Extract job stubs from a listing page.

        Each stub dict should contain at minimum: title, company, url.
        If FETCH_DETAILS is True, include 'detail_url' for per-job fetch.
        """
        ...

    @abstractmethod
    def parse_job_detail(self, soup: BeautifulSoup) -> dict:
        """Extract full job details from a detail page.

        Returns dict of additional fields to merge into the listing stub.
        """
        ...
