"""BaseScraper — abstract base for HTML-scraping job providers.

Extends BaseJobProvider to reuse circuit breaker, normalization pipeline,
deduplication, and stats tracking. Adds rate-limiting con jitter, reintentos con
backoff, detección de soft-blocks, compliance pre-check y obtención de HTML
(httpx para SSR, Playwright endurecido para SPAs JS). Las técnicas anti-detección
viven en `scraper_stealth` (SRP): este módulo solo las orquesta.
"""

import asyncio
import logging
from abc import abstractmethod

import httpx
from bs4 import BeautifulSoup

from config import settings
from services.circuit_breaker import CircuitBreakerOpen
from services.job_service import BaseJobProvider
from services.scraper_stealth import (
    CHROMIUM_CONTAINER_ARGS,
    DEFAULT_SOFT_BLOCK_MARKERS,
    STEALTH_INIT_SCRIPT,
    STEALTH_LAUNCH_ARGS,
    jittered_delay,
    looks_soft_blocked,
    realistic_headers,
)

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
    - MAX_RETRIES / RETRY_BACKOFF_SECONDS: reintento de errores transitorios
    - JITTER_RATIO: aleatoriedad añadida al rate-limit (anti-patrón de bot)
    - SOFT_BLOCK_MARKERS: substrings que delatan una pantalla anti-bot
    """

    LISTING_URL: str = ""
    RATE_LIMIT_SECONDS: float = 2.0
    MAX_PAGES: int = 10
    NEEDS_PLAYWRIGHT: bool = False
    FETCH_DETAILS: bool = True
    PAGE_SIZE: int = 20

    # Anti-detección (valores por defecto desde settings; sobreescribibles).
    JITTER_RATIO: float = settings.SCRAPER_DELAY_JITTER_RATIO
    MAX_RETRIES: int = settings.SCRAPER_MAX_RETRIES
    RETRY_BACKOFF_SECONDS: float = settings.SCRAPER_RETRY_BACKOFF_SECONDS
    SOFT_BLOCK_MARKERS: tuple[str, ...] = DEFAULT_SOFT_BLOCK_MARKERS

    # Estados HTTP que merecen reintento (servicio temporalmente caído).
    # Alineado con utils.http.DEFAULT_RETRY_STATUSES salvo 429, que aquí se
    # trata como bloqueo de compliance (ver BLOCK_STATUS), no como reintento.
    RETRYABLE_STATUS: frozenset[int] = frozenset({500, 502, 503, 504})
    # Estados que se reportan como bloqueo a compliance. El 503 está en ambos
    # sets a propósito: se reintenta como caída transitoria y, si persiste, el
    # llamante lo reporta como bloqueo.
    BLOCK_STATUS: frozenset[int] = frozenset({403, 429, 503})
    # Bloqueos que se reportan SIN reintento previo (deliberados, no transitorios).
    # Excluye el 503: es transitorio y solo debe reportarse desde el path de listado
    # tras agotar reintentos, nunca a la primera en un path sin retry (detalle).
    IMMEDIATE_BLOCK_STATUS: frozenset[int] = BLOCK_STATUS - RETRYABLE_STATUS

    # Cabeceras realistas de un Chrome real (User-Agent, client hints, Sec-Fetch).
    DEFAULT_HEADERS: dict[str, str] = realistic_headers()

    def __init__(self):
        super().__init__()
        # Presupuesto dinámico de páginas para ESTE run, inyectado por el
        # pipeline (CrawlerBudgetService) antes de fetch_jobs. None = sin
        # presupuesto → se usa MAX_PAGES (comportamiento legacy).
        self._max_pages_this_run: int | None = None

    def _pages_budget(self) -> int:
        """Tope de páginas del run: el presupuesto inyectado, acotado por MAX_PAGES."""
        if self._max_pages_this_run is None:
            return self.MAX_PAGES
        return max(1, min(self.MAX_PAGES, self._max_pages_this_run))

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
    # Rate-limiting y reintentos (anti-detección)
    # ------------------------------------------------------------------

    async def _rate_limit_delay(self) -> None:
        """Pausa entre peticiones con jitter para no crear intervalos regulares."""
        await asyncio.sleep(jittered_delay(self.RATE_LIMIT_SECONDS, self.JITTER_RATIO))

    def _backoff_delay(self, attempt: int) -> float:
        """Backoff exponencial con jitter para el reintento `attempt` (0-based)."""
        base = self.RETRY_BACKOFF_SECONDS * (2**attempt)
        return jittered_delay(base, self.JITTER_RATIO)

    async def _request_with_retry(self, do_request):
        """Ejecuta una petición por el circuit breaker reintentando lo transitorio.

        Reintenta ante errores de red (timeouts, conexión) y estados de
        RETRYABLE_STATUS, con backoff exponencial. El circuito abierto es
        terminal (no se reintenta). Devuelve la respuesta final aunque su estado
        sea de error: el llamante decide qué hacer.
        """
        # Una config negativa no debe saltar el bucle (dejaría response=None → crash).
        max_retries = max(self.MAX_RETRIES, 0)
        response = None
        for attempt in range(max_retries + 1):
            is_last = attempt == max_retries
            try:
                response = await self._circuit.call(do_request)
            except CircuitBreakerOpen:
                raise
            except httpx.HTTPError:
                if is_last:
                    raise
                await asyncio.sleep(self._backoff_delay(attempt))
                continue

            if response.status_code not in self.RETRYABLE_STATUS or is_last:
                return response

            logger.info(
                "%s HTTP %d — reintento %d/%d",
                self.SOURCE_NAME,
                response.status_code,
                attempt + 1,
                max_retries,
            )
            await asyncio.sleep(self._backoff_delay(attempt))

        return response  # inalcanzable: con max_retries>=0 la última iteración retorna/relanza

    # ------------------------------------------------------------------
    # httpx scraping (for server-rendered pages)
    # ------------------------------------------------------------------

    def _build_httpx_kwargs(self) -> dict:
        """Argumentos del AsyncClient, con proxy opcional si está configurado."""
        kwargs: dict = {
            "headers": self.DEFAULT_HEADERS,
            "follow_redirects": True,
            "timeout": settings.SCRAPER_HTTPX_TIMEOUT,
        }
        proxy = settings.SCRAPER_PROXY_URL or None
        if proxy:
            kwargs["proxy"] = proxy
        return kwargs

    async def _scrape_with_httpx(self, query: str) -> list[dict]:
        """Fetch listing pages with httpx, parse with BeautifulSoup."""
        all_jobs: list[dict] = []

        async with httpx.AsyncClient(**self._build_httpx_kwargs()) as client:
            for page in range(1, self._pages_budget() + 1):
                url = self.build_listing_url(page, query)

                try:
                    response = await self._request_with_retry(
                        lambda u=url: client.get(u)
                    )
                except (CircuitBreakerOpen, httpx.HTTPError) as e:
                    logger.error(
                        "%s listing page %d error: %s", self.SOURCE_NAME, page, e
                    )
                    break

                if await self._listing_status_stops(response.status_code, page):
                    break

                stubs = self.parse_listing_page(BeautifulSoup(response.text, "lxml"))
                if not stubs:
                    await self._maybe_report_soft_block(response.text, page)
                    break

                all_jobs.extend(await self._collect_page_jobs(client, stubs))

                # Crawler incremental: si la página entera ya se había visto, hemos
                # alcanzado el contenido sincronizado → parar (no seguir paginando).
                if self._page_all_known(stubs):
                    self._stop_reason = "known_page"
                    logger.info(
                        "%s early-stop en página %d: sin ofertas nuevas (cursor)",
                        self.SOURCE_NAME,
                        page,
                    )
                    break

                if len(stubs) < self.PAGE_SIZE:
                    break

                await self._rate_limit_delay()

        logger.info("%s scraped %d raw jobs", self.SOURCE_NAME, len(all_jobs))
        return all_jobs

    async def _listing_status_stops(self, status_code: int, page: int) -> bool:
        """Indica si el estado HTTP obliga a detener el listado (bloqueo o error).

        Un estado de BLOCK_STATUS se reporta a compliance; cualquier otro distinto
        de 200 solo se registra. En ambos casos no tiene sentido seguir paginando.
        """
        if status_code in self.BLOCK_STATUS:
            logger.warning(
                "%s blocked with HTTP %d on page %d",
                self.SOURCE_NAME,
                status_code,
                page,
            )
            await self._report_block(status_code)
            return True
        if status_code != 200:
            logger.warning("%s HTTP %d on page %d", self.SOURCE_NAME, status_code, page)
            return True
        return False

    async def _maybe_report_soft_block(self, html: str, page: int) -> bool:
        """Reporta un soft-block si una página sin datos contiene un marcador anti-bot.

        Parse-first: solo se llama cuando el parseo no extrajo ningún empleo, así
        un anuncio legítimo que mencione "captcha" nunca descarta una página válida.
        Devuelve True si detectó y reportó el bloqueo (las subclases lo usan para
        decidir si abortan el scraping). False si la página sin datos está limpia.
        """
        if looks_soft_blocked(html, self.SOFT_BLOCK_MARKERS):
            logger.warning(
                "%s soft-block detectado (HTTP 200 sin datos) en página %d",
                self.SOURCE_NAME,
                page,
            )
            await self._report_block(200)
            return True
        return False

    async def _collect_page_jobs(
        self, client: httpx.AsyncClient, stubs: list[dict]
    ) -> list[dict]:
        """Devuelve los stubs de una página, enriquecidos con su detalle si procede."""
        if not self.FETCH_DETAILS:
            return stubs
        for stub in stubs:
            detail_url = stub.get("detail_url")
            if detail_url:
                await self._rate_limit_delay()
                detail = await self._fetch_detail_httpx(client, detail_url)
                if detail:
                    stub.update(detail)
        return stubs

    async def _fetch_detail_httpx(
        self, client: httpx.AsyncClient, url: str
    ) -> dict | None:
        """Fetch a single job detail page and parse it."""
        try:
            response = await client.get(url)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "lxml")
                return self.parse_job_detail(soup)
            # Sin retry aquí: un 503 transitorio no debe contar como bloqueo (solo el
            # path de listado, que sí reintenta, reporta el 503 si persiste).
            if response.status_code in self.IMMEDIATE_BLOCK_STATUS:
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
    # Playwright scraping (for JS-rendered SPAs) — endurecido (stealth)
    # ------------------------------------------------------------------

    def _build_launch_args(self) -> list[str]:
        """Args de lanzamiento de Chromium: stealth + args de contenedor (gateados).

        Los flags stealth (anti-detección) van siempre; los que rebajan el sandbox
        solo si SCRAPER_PLAYWRIGHT_NO_SANDBOX está activo (requerido en Docker root).
        """
        args = list(STEALTH_LAUNCH_ARGS)
        if settings.SCRAPER_PLAYWRIGHT_NO_SANDBOX:
            args += list(CHROMIUM_CONTAINER_ARGS)
        return args

    async def _launch_browser(self, p):
        """Lanza Chromium local con stealth, o se conecta a un browser remoto CDP.

        Si SCRAPER_BROWSER_CDP_URL está definido (p.ej. un browser stealth de
        pago) se controla por CDP en vez de lanzar uno local detectable.
        """
        cdp_url = settings.SCRAPER_BROWSER_CDP_URL or None
        if cdp_url:
            logger.info("%s conectando a browser remoto vía CDP", self.SOURCE_NAME)
            return await p.chromium.connect_over_cdp(cdp_url)

        launch_kwargs: dict = {"headless": True, "args": self._build_launch_args()}
        proxy = settings.SCRAPER_PROXY_URL or None
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}
        return await p.chromium.launch(**launch_kwargs)

    async def _scrape_with_playwright(self, query: str) -> list[dict]:
        """Fetch JS-rendered pages with a hardened Playwright headless browser."""
        from playwright.async_api import async_playwright

        all_jobs: list[dict] = []

        async with async_playwright() as p:
            browser = await self._launch_browser(p)
            context = await browser.new_context(
                user_agent=self.DEFAULT_HEADERS["User-Agent"],
                locale="de-CH",
                viewport={"width": 1920, "height": 1080},
            )
            # Inyectar el script anti-detección antes de cargar cualquier página.
            await context.add_init_script(STEALTH_INIT_SCRIPT)
            page = await context.new_page()

            try:
                for pg_num in range(1, self._pages_budget() + 1):
                    url = self.build_listing_url(pg_num, query)

                    try:
                        response = await page.goto(
                            url,
                            wait_until="networkidle",
                            timeout=settings.SCRAPER_PLAYWRIGHT_TIMEOUT_MS,
                        )
                    except Exception as e:
                        logger.error(
                            "%s Playwright page %d error: %s",
                            self.SOURCE_NAME,
                            pg_num,
                            e,
                        )
                        break

                    if response and response.status in self.BLOCK_STATUS:
                        await self._report_block(response.status)
                        break

                    html = await page.content()
                    soup = BeautifulSoup(html, "lxml")
                    stubs = self.parse_listing_page(soup)

                    if not stubs:
                        await self._maybe_report_soft_block(html, pg_num)
                        break

                    all_jobs.extend(stubs)

                    # Crawler incremental: early-stop si la página ya es conocida.
                    if self._page_all_known(stubs):
                        self._stop_reason = "known_page"
                        logger.info(
                            "%s early-stop (Playwright) en página %d: sin novedades (cursor)",
                            self.SOURCE_NAME,
                            pg_num,
                        )
                        break

                    if len(stubs) < self.PAGE_SIZE:
                        break

                    await self._rate_limit_delay()
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
