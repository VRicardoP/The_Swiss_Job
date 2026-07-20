"""Scraper for IrishJobs.ie + Jobs.ie — remote (work-from-home) jobs in Ireland.

Ambos portales corren sobre la MISMA plataforma StepStone: comparten el mismo
`id` de oferta y el mismo formato SSR. Se cosechan los DOS hosts en una sola
corrida y se deduplican por ese `id` de plataforma (misma oferta en ambos).

La lista NO se renderiza en el DOM: viene en un objeto JS embebido
`window.__PRELOADED_STATE__["app-unifiedResultlist"] = {...}`. Se extrae con un
regex ANCLADO a esa clave EXACTA (la página trae ~20 referencias a
`__PRELOADED_STATE__` y otro blob real "google-onetap"; un match laxo cogería el
equivocado) y se parsea el literal balanceado una sola vez por página.
"""

import json
import logging
import re

import httpx
from bs4 import BeautifulSoup

from services.circuit_breaker import CircuitBreakerOpen
from services.scraper_engine import BaseScraper
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

# Ancla EXACTA a la asignación del blob de resultados. Exige `] = ` (asignación)
# para NO casar con las referencias de solo-lectura (`...["app-unifiedResultlist"];`)
# ni con el blob "google-onetap". `.search()` sobre el texto del <script>.
_STATE_ANCHOR_RE = re.compile(
    r"""window\.__PRELOADED_STATE__\[\s*["']app-unifiedResultlist["']\s*\]\s*=\s*"""
)

# Tokens numéricos dentro del string de salario: "35,000", "22.00", "00", "31,921".
_SALARY_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# Mensaje único para cuando el blob no se puede decodificar (formato cambiado).
_REDEPLOY_MSG = (
    "IrishJobs: possible StepStone redeploy — check __PRELOADED_STATE__ format"
)


def _extract_balanced_object(text: str, start: int) -> str | None:
    """Devuelve el literal `{...}` balanceado que empieza en `start`, o None.

    Cuenta llaves respetando cadenas JSON (comillas dobles + escapes) para no
    cortar en una `}` que viva dentro de un string.
    """
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_salary(
    display: str,
) -> tuple[int | None, int | None, str | None, str | None]:
    """Parsea el string de display a (min, max, currency, period) numérico/enum.

    `item.salary` es SOLO texto de display ("€ Not Disclosed",
    "€35,000 - €45,000 per annum", "€22.00 - €25.00 per hour"). `unifiedSalary`
    llega null, así que hay que parsear a mano y TOLERAR basura ("€90,000 - €00",
    max malformado): lo que no parsea limpio (número <= 0 o max < min) → None.
    """
    tokens = _SALARY_NUMBER_RE.findall(display or "")
    if not tokens:
        return None, None, None, None

    currency = "EUR" if "€" in display else ("GBP" if "£" in display else None)
    low = display.lower()
    if "hour" in low:
        period = "hourly"
    elif "month" in low:
        period = "monthly"
    elif "annum" in low or "year" in low:
        period = "yearly"
    else:
        period = None

    def to_int(raw: str) -> int | None:
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            return None
        return int(value) if value > 0 else None  # <= 0 se considera basura

    if len(tokens) == 1:
        val = to_int(tokens[0])
        sal_min = sal_max = val
    else:
        sal_min = to_int(tokens[0])
        sal_max = to_int(tokens[1])
        # max malformado o incoherente (menor que min) → descartar solo el max
        if sal_max is not None and sal_min is not None and sal_max < sal_min:
            sal_max = None

    if sal_min is None and sal_max is None:
        return None, None, None, None
    return sal_min, sal_max, currency, period


class IrishJobsScraper(BaseScraper):
    SOURCE_NAME = "irishjobs"
    # Dos hosts StepStone cosechados en la misma corrida; dedupe por id de plataforma.
    HOSTS: tuple[str, ...] = ("https://www.irishjobs.ie", "https://www.jobs.ie")
    LISTING_PATH = "/jobs/work-from-home"
    # Requerido por BaseScraper (no se usa directamente: el override cosecha ambos hosts).
    LISTING_URL = "https://www.irishjobs.ie/jobs/work-from-home"
    RATE_LIMIT_SECONDS = 2.0
    MAX_PAGES = 8  # techo por host; el cursor incremental acota por debajo
    NEEDS_PLAYWRIGHT = False
    FETCH_DETAILS = (
        False  # todo sale del blob del listado; SIN segunda llamada por oferta
    )
    PAGE_SIZE = 25  # StepStone devuelve 25 ofertas/página

    def build_listing_url(self, page: int, query: str) -> str:
        """URL de listado del host primario (contrato de BaseScraper)."""
        return self._page_url(self.HOSTS[0], page)

    def _page_url(self, host: str, page: int) -> str:
        return f"{host}{self.LISTING_PATH}?page={page}"

    # ------------------------------------------------------------------
    # Extracción del blob __PRELOADED_STATE__
    # ------------------------------------------------------------------

    def _decode_state(self, script_text: str) -> dict | None:
        """Decodifica el literal de `app-unifiedResultlist`. None si falla el formato."""
        match = _STATE_ANCHOR_RE.search(script_text)
        if not match:
            logger.error(_REDEPLOY_MSG)
            return None
        literal = _extract_balanced_object(script_text, match.end())
        if literal is None:
            logger.error(_REDEPLOY_MSG)
            return None
        try:
            return json.loads(literal)
        except json.JSONDecodeError:
            logger.error(_REDEPLOY_MSG)
            return None

    def _decode_state_from_soup(self, soup: BeautifulSoup) -> dict | None:
        """Busca el <script> con la asignación exacta y decodifica su blob."""
        for script in soup.find_all("script"):
            text = script.string if script.string is not None else script.get_text()
            if text and _STATE_ANCHOR_RE.search(text):
                return self._decode_state(text)
        logger.error(_REDEPLOY_MSG)
        return None

    @staticmethod
    def _clean_logo(raw_logo: str) -> str | None:
        """Descarta logos vacíos ('.../CompanyLogos/' sin fichero) → None."""
        logo = (raw_logo or "").strip()
        if not logo or logo.rstrip("/").endswith("CompanyLogos"):
            return None
        return logo

    def _items_to_stubs(self, data: dict, host: str) -> list[dict]:
        """Convierte `searchResults.items` en stubs normalizables (URLs absolutas)."""
        items = (data.get("searchResults") or {}).get("items") or []
        stubs: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            rel_url = (item.get("url") or "").strip()
            if not title or not rel_url:
                continue  # se descartaría luego; evitamos crear stubs inservibles

            url = rel_url if rel_url.startswith("http") else f"{host}{rel_url}"
            description = strip_html_tags(item.get("textSnippet") or "")
            # Solo currency+period del display; los importes vienen en EUR/GBP y la
            # conversión a CHF + anualización la hace DataNormalizer.normalize_salary
            # aguas abajo. Prellenar salary_*_chf con EUR haría que su early-return
            # los guardase como CHF sin convertir (p.ej. €22/h → 22 CHF/año).
            _, _, currency, period = _parse_salary(item.get("salary") or "")

            stubs.append(
                {
                    "id": item.get(
                        "id"
                    ),  # id de plataforma StepStone (dedupe entre hosts)
                    "title": title,
                    "company": (item.get("companyName") or "").strip() or "Unknown",
                    "location": (item.get("location") or "").strip(),
                    "url": url,
                    "remote": True,  # DERIVADO DEL SCOPE /jobs/work-from-home, no del item
                    "description": description,
                    "logo": self._clean_logo(item.get("companyLogoUrl") or ""),
                    "salary_original": (item.get("salary") or "").strip() or None,
                    "salary_min_chf": None,  # lo rellena normalize_salary tras convertir
                    "salary_max_chf": None,
                    "salary_currency": currency,
                    "salary_period": period,
                }
            )
        return stubs

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        """Entrada testeable: stubs de una página usando el host primario."""
        data = self._decode_state_from_soup(soup)
        if data is None:
            return []
        return self._items_to_stubs(data, self.HOSTS[0])

    def parse_job_detail(self, soup: BeautifulSoup) -> dict:
        """No se usa — FETCH_DETAILS es False (todo sale del listado)."""
        return {}

    # ------------------------------------------------------------------
    # Cosecha de los dos hosts con dedupe por id de plataforma
    # ------------------------------------------------------------------

    async def _scrape_with_httpx(self, query: str) -> list[dict]:
        """Cosecha irishjobs.ie + jobs.ie; deduplica por id de plataforma StepStone."""
        seen_ids: set = set()
        all_stubs: list[dict] = []
        async with httpx.AsyncClient(**self._build_httpx_kwargs()) as client:
            for host in self.HOSTS:
                all_stubs.extend(await self._harvest_host(client, host, seen_ids))

        logger.info(
            "%s scraped %d raw jobs across %d hosts",
            self.SOURCE_NAME,
            len(all_stubs),
            len(self.HOSTS),
        )
        return all_stubs

    async def _harvest_host(
        self, client: httpx.AsyncClient, host: str, seen_ids: set
    ) -> list[dict]:
        """Pagina un host hasta agotar resultados/tope; devuelve stubs no vistos."""
        stubs: list[dict] = []
        total: int | None = None

        for page in range(1, self._pages_budget() + 1):
            url = self._page_url(host, page)
            try:
                response = await self._request_with_retry(lambda u=url: client.get(u))
            except (CircuitBreakerOpen, httpx.HTTPError) as e:
                logger.error("%s %s page %d error: %s", self.SOURCE_NAME, host, page, e)
                break

            if await self._listing_status_stops(response.status_code, page):
                break

            soup = BeautifulSoup(response.text, "lxml")
            data = self._decode_state_from_soup(soup)
            if data is None:
                # Decode falló (redeploy ya logueado): abortar este host, no petar.
                await self._maybe_report_soft_block(response.text, page)
                break

            page_stubs = self._items_to_stubs(data, host)
            if not page_stubs:
                break

            if total is None:
                meta = (data.get("searchResults") or {}).get("meta") or {}
                total = meta.get("total")

            stubs.extend(self._dedupe_new(page_stubs, seen_ids))

            # Crawler incremental: página entera ya conocida → contenido sincronizado.
            if self._page_all_known(page_stubs):
                self._stop_reason = "known_page"
                logger.info(
                    "%s early-stop en %s página %d: sin novedades (cursor)",
                    self.SOURCE_NAME,
                    host,
                    page,
                )
                break

            # Terminación: página incompleta o alcanzado el total declarado.
            if len(page_stubs) < self.PAGE_SIZE:
                break
            if total is not None and page * self.PAGE_SIZE >= total:
                break

            await self._rate_limit_delay()

        return stubs

    @staticmethod
    def _dedupe_new(page_stubs: list[dict], seen_ids: set) -> list[dict]:
        """Filtra stubs cuyo id de plataforma ya se vio (misma oferta en otro host)."""
        fresh: list[dict] = []
        for stub in page_stubs:
            job_id = stub.get("id")
            if job_id is not None and job_id in seen_ids:
                continue
            if job_id is not None:
                seen_ids.add(job_id)
            fresh.append(stub)
        return fresh

    # ------------------------------------------------------------------
    # Normalización al esquema unificado (21 claves)
    # ------------------------------------------------------------------

    def normalize_job(self, raw: dict) -> dict:
        title = raw.get("title", "").strip()
        company = raw.get("company", "Unknown").strip() or "Unknown"
        url = raw.get("url", "").strip()
        description = raw.get("description", "")
        location = raw.get("location", "").strip()

        tags = extract_job_skills(title, description)

        return {
            "hash": self.compute_hash(title, company, url),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": location,
            "canton": extract_canton(location),  # Irlanda → None (no hay cantón suizo)
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url,
            "remote": bool(raw.get("remote", False)),  # scope /jobs/work-from-home
            "tags": tags[: self.MAX_TAGS],
            "logo": raw.get("logo"),
            "salary_min_chf": raw.get("salary_min_chf"),
            "salary_max_chf": raw.get("salary_max_chf"),
            "salary_original": raw.get("salary_original"),
            "salary_currency": raw.get("salary_currency"),
            "salary_period": raw.get("salary_period"),
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": None,
        }
