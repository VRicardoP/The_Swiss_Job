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
from utils import fetch_diagnostics as diag

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
    - WATCHLIST_SOURCE: fuente vigilada de baja rotación → sin backoff
    """

    LISTING_URL: str = ""
    RATE_LIMIT_SECONDS: float = 2.0
    MAX_PAGES: int = 10
    NEEDS_PLAYWRIGHT: bool = False
    FETCH_DETAILS: bool = True
    PAGE_SIZE: int = 20
    # Fuente de WATCHLIST: pocas ofertas y muy espaciadas, pero que no se
    # pueden perder. Exime del backoff de frecuencia del CrawlerBudgetService
    # (que castiga precisamente a las fuentes sin novedades), NO del early-stop
    # ni del presupuesto de páginas. La declara el scraper porque es él quien
    # conoce su naturaleza — el servicio de presupuesto no debe saber nombres.
    WATCHLIST_SOURCE: bool = False

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

    # Estado de compliance del run en curso (VD.4a). Se rearma en fetch_jobs;
    # a nivel de clase para que los paths de scraping sean invocables sueltos
    # (tests, overrides) sin AttributeError.
    # NO compartir una instancia de scraper entre runs CONCURRENTES: el rearme
    # del segundo run borraría el flag de bloqueo del primero. Hoy producción
    # crea instancias frescas por run y las ejecuta en secuencia, así que no es
    # un bug — pero es una precondición de estos flags, no una casualidad.
    # - _run_block_reported: este run reportó al menos un bloqueo a compliance.
    # - _run_verified_empty: este run parseó una página 200 sin datos y SIN
    #   marcador anti-bot — un "vacío verificado" (estado normal de una watchlist).
    _run_block_reported: bool = False
    _run_verified_empty: bool = False

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs via scraping. Overrides BaseJobProvider.fetch_jobs().

        Flow: pre_check -> scrape (httpx or Playwright) -> normalize -> finalize.
        """
        if not await self._pre_check():
            return []

        # Rearmar el estado del run. Producción crea instancias frescas por run
        # (get_all_scrapers instancia en cada llamada), así que esto es defensa
        # en profundidad para instancias reutilizadas (tests, invocaciones sueltas).
        self._run_block_reported = False
        self._run_verified_empty = False

        if self.NEEDS_PLAYWRIGHT:
            all_raw = await self._scrape_with_playwright(query)
        else:
            all_raw = await self._scrape_with_httpx(query)

        results = self._process_raw_jobs(all_raw)

        # Rehabilitación (VD.4a): un run cuenta como éxito si trajo ofertas O si
        # verificó un board vacío legítimo — y en ningún caso si reportó un
        # bloqueo. Antes era `if results:` a secas, y una watchlist de colegios
        # (0 vacantes durante meses es su estado NORMAL) apagada por el
        # kill-switch no se rehabilitaba jamás ni marcaba last_success_at. La
        # "sequedad" de una fuente la vigilan source_health y el backoff del
        # crawler; el kill-switch va solo de bloqueos.
        if (results or self._run_verified_empty) and not self._run_block_reported:
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

        # Se marca ANTES del intento de persistir: aunque la BD falle, un run
        # que detectó un bloqueo no debe rehabilitar la fuente (VD.4a).
        self._run_block_reported = True
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

    async def _request_with_retry(self, do_request, url: str = ""):
        """Ejecuta una petición por el circuit breaker reintentando lo transitorio.

        Reintenta ante errores de red (timeouts, conexión) y estados de
        RETRYABLE_STATUS, con backoff exponencial. El circuito abierto es
        terminal (no se reintenta). Devuelve la respuesta final aunque su estado
        sea de error: el llamante decide qué hacer.

        VD.10 — el fallo DEFINITIVO (reintentos agotados, circuito abierto,
        estado final no-200) se registra en `fetch_diagnostics`, igual que hace
        `utils.http.fetch_with_retry` para los providers: sin esto, un 404 o un
        timeout del listado acababa en `classify(0, [])` = `empty` y la fuente
        rota se presentaba como fuente seca. `url` es solo para ese registro;
        el override que no la pasa (schuljobs) queda identificado por su
        LISTING_URL — exacto en su fetch inicial, aproximado en el scroll AJAX.
        """
        diag_url = url or self.LISTING_URL
        # Una config negativa no debe saltar el bucle (dejaría response=None → crash).
        max_retries = max(self.MAX_RETRIES, 0)
        response = None
        for attempt in range(max_retries + 1):
            is_last = attempt == max_retries
            try:
                response = await self._circuit.call(do_request)
            except CircuitBreakerOpen as exc:
                # El circuito abierto también es "no se pudo descargar",
                # nunca "no había ofertas".
                diag.record(diag.KIND_NETWORK, diag_url, detail=str(exc))
                raise
            except httpx.HTTPError as exc:
                if is_last:
                    diag.record(
                        diag.KIND_NETWORK,
                        diag_url,
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                    raise
                await asyncio.sleep(self._backoff_delay(attempt))
                continue

            if response.status_code not in self.RETRYABLE_STATUS or is_last:
                if response.status_code != 200:
                    # Estado final no-200 (bloqueos incluidos): se registra AQUÍ,
                    # donde ya es definitivo — `_listing_status_stops` decide el
                    # corte y el reporte a compliance pero NO vuelve a registrar,
                    # para no duplicar el issue.
                    diag.record(diag.KIND_HTTP, diag_url, status=response.status_code)
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
                    # El propio helper registra el fallo definitivo en
                    # fetch_diagnostics (VD.10): aquí solo se corta.
                    response = await self._request_with_retry(
                        lambda u=url: client.get(u), url=url
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

        VD.10 — el issue de diagnóstico del no-200 NO se registra aquí sino en
        quien hizo la petición (`_request_with_retry` en el path httpx, el
        bucle de `_scrape_with_playwright` en el de Playwright): este método lo
        llaman ambos paths y también tras el helper (irishjobs), y registrar en
        los dos sitios duplicaría el mismo fallo.
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
            # VD.10 — un bloqueo también es "falló la descarga", no "no hay
            # ofertas": sin issue el run saldría `empty` y `source_health`
            # registraría una fuente SECA donde hay una ROTA (señal de salud
            # y alerta equivocadas). El backoff del crawler no cambia con
            # esto: se alimenta de las novedades persistidas (el
            # `consecutive_empty_runs` del cursor), no del veredicto, y
            # aparca la fuente igual. Compliance sigue recibiendo su
            # report_block exactamente igual que antes.
            diag.record(
                diag.KIND_NETWORK,
                self.LISTING_URL,
                detail=f"soft-block: HTTP 200 con marcador anti-bot en página {page}",
            )
            await self._report_block(200)
            return True
        # Página 200 sin datos y sin marcador: vacío VERIFICADO (VD.4a).
        # "Verificado" = el sitio sirvió contenido normal, NO "confirmamos que
        # era el board": unos selectores obsoletos o un redirect a portada
        # también acaban aquí. Es el techo deliberado del flag — el kill-switch
        # modela BLOQUEOS; de la sequedad o rotura de una fuente se ocupan
        # source_health y el backoff del crawler.
        self._run_verified_empty = True
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
            # VD.10 — el detalle caído no corta el run (el stub del listado ya
            # es una oferta mínima), pero se anota: con ofertas el veredicto
            # sigue siendo `ok` con issues — degradación parcial, la semántica
            # documentada de `classify`.
            diag.record(diag.KIND_HTTP, url, status=response.status_code)
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
            diag.record(diag.KIND_NETWORK, url, detail=f"{type(e).__name__}: {e}")
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
                        # VD.10 — mismo tratamiento que el error de red httpx:
                        # un timeout/crash de goto es `error`, no `empty`.
                        diag.record(
                            diag.KIND_NETWORK, url, detail=f"{type(e).__name__}: {e}"
                        )
                        break

                    # goto puede devolver None (p.ej. navegación same-document):
                    # sin respuesta no hay nada verificado — parar sin tocar los
                    # flags del run, como el error de red en el path httpx.
                    if response is None:
                        logger.warning(
                            "%s Playwright page %d: goto sin respuesta",
                            self.SOURCE_NAME,
                            pg_num,
                        )
                        diag.record(diag.KIND_NETWORK, url, detail="goto sin respuesta")
                        break

                    # VD.10 — este path no pasa por _request_with_retry, así que
                    # el estado final no-200 se registra aquí (una sola vez:
                    # _listing_status_stops no registra, ver su docstring).
                    if response.status != 200:
                        diag.record(diag.KIND_HTTP, url, status=response.status)

                    # Mismo corte por estado que el path httpx (VD.4a, 2ª ronda):
                    # un no-200 jamás alcanza _maybe_report_soft_block, así el
                    # HTML de una página de error (404/500) no puede contar como
                    # "vacío verificado" y rehabilitar la fuente.
                    if await self._listing_status_stops(response.status, pg_num):
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
