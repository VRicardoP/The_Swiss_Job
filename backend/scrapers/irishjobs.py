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
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from services.circuit_breaker import CircuitBreakerOpen
from services.scraper_engine import BaseScraper
from utils import fetch_diagnostics as diag
from utils.dates import parse_published_at
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

# Dos hosts StepStone cosechados en la misma corrida (dedupe por id de
# plataforma). A nivel de módulo y no solo en la clase: `_resolve_job_url`
# deriva de aquí su lista blanca de hostnames sin duplicar los literales.
_HOSTS: tuple[str, ...] = ("https://www.irishjobs.ie", "https://www.jobs.ie")

# Hostnames PROPIOS admitidos en una URL absoluta del blob (r6/H1, G3):
# cualquier otro host es ajeno al portal y no puede acabar clicable en el
# corpus. Derivado de _HOSTS para que añadir un host actualice ambas cosas.
_ALLOWED_HOSTNAMES: frozenset[str] = frozenset(
    urlsplit(h).hostname or "" for h in _HOSTS
)


def _s(value: object) -> str:
    """Devuelve `value` solo si es str; cadena vacía en caso contrario.

    Blindaje por-campo (r6/H2, misma forma que financejobs): un escalar
    inesperado en un campo interior (`title: 42`) hacía que el `.strip()`
    lanzara AttributeError. La red por-fuente lo convertía en `error` (la
    letra de G1 quedaba intacta), pero UNA oferta corrupta perdía la cosecha
    entera de esa página. Un campo de tipo inesperado debe degradar ESA
    oferta, nunca la página.
    """
    return value if isinstance(value, str) else ""


def _resolve_job_url(raw: str, host: str) -> str | None:
    """Resuelve `item.url` del blob a la URL absoluta de la oferta, o None.

    Mismo criterio que `_resolve_job_url` en stelle_admin/gastrojob (r6/H1):
    el `url` del item se aceptaba SIN validar y una URL absoluta hacia
    CUALQUIER host acababa persistida como enlace clicable para el usuario
    (host confusion / phishing, G3). Las relativas se resuelven sobre `host`
    (el que sirvió la página); las absolutas solo se aceptan hacia los
    hostnames de _HOSTS. Se emite RECONSTRUIDA (https + hostname + path, sin
    query/fragment/puerto): el hash de dedup e ix_jobs_url comparan la URL
    literal, y cada variante sería una fila nueva por oferta (G4).
    """
    if not raw:
        return None
    # Diferencial de parsers urllib/WHATWG: urllib solo corta el netloc en
    # `/ ? #`; los navegadores también en `\`. Con
    # `https://evil.com\@www.irishjobs.ie/...` urllib ve un netloc que acaba
    # en el host propio, pero el navegador del usuario navega a evil.com.
    # También percent-encoded (%5C): un decode aguas abajo lo reactivaría.
    # Ningún url legítimo del blob contiene ninguna de las dos formas.
    if "\\" in raw or "%5c" in raw.lower():
        return None
    # Caracteres de control (\t, \n, \x00...): urlsplit los RECORTA en
    # silencio (alineado con WHATWG) y el resultado ya no es el enlace que
    # emitió el portal — se rechazan antes de parsear.
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw):
        return None
    try:
        parts = urlsplit(urljoin(f"{host}/", raw))
        # `.port` valida el puerto AL ACCEDER y lanza ValueError con uno no
        # numérico o fuera de rango — dentro del try, como el parseo.
        port = parts.port
    except ValueError:
        # p. ej. "https://[evil/..." (IPv6 malformado): urljoin/urlsplit
        # LANZAN en vez de parsear — degrada ESTA oferta, nunca la página.
        return None
    # Solo http(s): otros esquemas con autoridad no son ofertas navegables.
    if parts.scheme not in ("http", "https"):
        return None
    # Sin userinfo: `https://algo@www.irishjobs.ie/...` permite spoofing
    # visual del enlace. `username` es "" (no None) con userinfo vacío.
    if parts.username is not None:
        return None
    # Sin puerto explícito: StepStone nunca lo emite — uno "inesperado" solo
    # aparece en URLs manipuladas.
    if port is not None:
        return None
    # `.hostname` minusculiza y quita el puerto; un lookalike (punycode,
    # sufijo ajeno) no está en la lista blanca y queda fuera.
    hostname = parts.hostname or ""
    if hostname not in _ALLOWED_HOSTNAMES:
        return None
    # Sin path real no hay oferta que enlazar: ninguna oferta se emite sin
    # URL propia (G3).
    if not parts.path or parts.path == "/":
        return None
    return f"https://{hostname}{parts.path}"


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
            # int() DENTRO del try (r6/H2): un token de miles de dígitos
            # desborda float a inf e int(inf) lanza OverflowError — fuera
            # del try escapaba y una sola oferta perdía la página entera.
            result = int(value)
        except (ValueError, OverflowError):
            return None
        return result if value > 0 else None  # <= 0 se considera basura

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
    # Dos hosts StepStone cosechados en la misma corrida; dedupe por id de
    # plataforma. El literal vive en _HOSTS (módulo): _resolve_job_url deriva
    # de él su lista blanca de hostnames (r6/H1).
    HOSTS: tuple[str, ...] = _HOSTS
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

    def _decode_state(self, script_text: str, start: int, url: str) -> dict | None:
        """Decodifica el literal de `app-unifiedResultlist`. None si falla el formato.

        `start` es el offset donde empieza el literal `{...}` — lo localiza el
        ÚNICO llamante (`_decode_state_from_soup`) al casar el ancla; pasarlo
        evita re-buscar el regex aquí y elimina la antigua rama "sin ancla",
        que era inalcanzable por construcción (r5/H4: su mutante sobrevivía a
        toda la suite). `url` es la página cuyo blob se decodifica: con DOS
        hosts, el issue debe culpar al que sirvió el blob ilegible (VD.10, H3).
        """
        # Los DOS modos de fallo de esta función registran issue (r4/R3-1):
        # literal truncado y JSON ilegible son la misma avería (un redeploy de
        # StepStone) y sin registro el run salía `empty` con 0 issues — fuente
        # ROTA presentada como SECA (violación material de G1). El tercer modo
        # (ancla ausente) lo registra `_decode_state_from_soup`, que es quien
        # busca el ancla. G2 verificado en vivo (2026-08-17): una página
        # legítimamente vacía de AMBOS hosts (búsqueda sin resultados,
        # "total": 0) SÍ trae el blob con `items: []`, así que un vacío sano
        # nunca pisa estos caminos.
        literal = _extract_balanced_object(script_text, start)
        if literal is None:
            logger.error(_REDEPLOY_MSG)
            diag.record(
                diag.KIND_NETWORK,
                url,
                detail=f"{_REDEPLOY_MSG} (truncated state literal)",
            )
            return None
        try:
            return json.loads(literal)
        except (ValueError, RecursionError) as e:
            # ValueError y no JSONDecodeError (r3/H4): un entero JSON de
            # miles de dígitos hace que json.loads lance un ValueError PLANO
            # ("Exceeds the limit...") que escapaba del parser; el
            # RecursionError del anidamiento extremo, igual (r3/R10). Y se
            # registra EXACTAMENTE un issue: capturar sin registrar dejaría
            # un 200 ilegible como falso `empty` con 0 issues, que es peor
            # que la excepción (G1) — misma forma que utils/http.py y
            # financejobs.
            logger.error(_REDEPLOY_MSG)
            diag.record(
                diag.KIND_NETWORK,
                url,
                detail=f"{_REDEPLOY_MSG} ({type(e).__name__}: {e})",
            )
            return None

    def _decode_state_from_soup(self, soup: BeautifulSoup, url: str) -> dict | None:
        """Busca el <script> con la asignación exacta y decodifica su blob."""
        for script in soup.find_all("script"):
            text = script.string if script.string is not None else script.get_text()
            if not text:
                continue
            match = _STATE_ANCHOR_RE.search(text)
            if match:
                # El literal empieza donde acaba la asignación casada; el
                # decode NO re-busca el ancla (r5/H4).
                return self._decode_state(text, match.end(), url)
        # Ningún <script> con la asignación: mismo redeploy y mismo registro
        # que los fallos de _decode_state (r4/R3-1, ver el comentario allí).
        logger.error(_REDEPLOY_MSG)
        diag.record(
            diag.KIND_NETWORK, url, detail=f"{_REDEPLOY_MSG} (no state script found)"
        )
        return None

    @staticmethod
    def _clean_logo(raw_logo: str) -> str | None:
        """Descarta logos vacíos ('.../CompanyLogos/' sin fichero) → None."""
        logo = (raw_logo or "").strip()
        if not logo or logo.rstrip("/").endswith("CompanyLogos"):
            return None
        return logo

    def _items_to_stubs(self, data: dict, host: str, url: str) -> list[dict]:
        """Convierte `searchResults.items` en stubs normalizables (URLs absolutas).

        `url` es la página cuyo blob se convierte: el issue de deriva de
        estructura debe culpar al host/página que la sirvió (VD.10, H3).
        """
        # La estructura INTERNA del blob también registra issue (r5/H1): con
        # el JSON ya decodificado, `searchResults` no-objeto, `items` no-lista
        # o una lista no vacía de la que no sale ni un stub son el mismo
        # redeploy de StepStone — sin registro, el run salía `empty` con 0
        # issues (violación MATERIAL de G1), el peldaño siguiente de la avería
        # que r4/R3-1 cerró hasta decodificar. Solo `items: []` es vacío
        # legítimo (G2): sonda 2026-08-17 en ambos hosts — los items reales
        # traen id/title/url/... y la búsqueda sin resultados devuelve
        # `items: []` con `total: 0`, nunca estas formas.
        search_results = data.get("searchResults")
        if not isinstance(search_results, dict):
            logger.error(_REDEPLOY_MSG)
            diag.record(
                diag.KIND_NETWORK,
                url,
                detail=f"{_REDEPLOY_MSG} (searchResults is not an object)",
            )
            return []
        items = search_results.get("items")
        if not isinstance(items, list):
            logger.error(_REDEPLOY_MSG)
            diag.record(
                diag.KIND_NETWORK,
                url,
                detail=f"{_REDEPLOY_MSG} (searchResults.items is not a list)",
            )
            return []
        stubs: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            # `_s` en cada campo interior (r6/H2): los niveles exteriores ya
            # están validados, pero un escalar inesperado (`title: 42`) hacía
            # que el `.strip()` lanzara AttributeError y perdiera la página.
            title = _s(item.get("title")).strip()
            rel_url = _s(item.get("url")).strip()
            if not title or not rel_url:
                continue  # se descartaría luego; evitamos crear stubs inservibles

            # `abs_url` y NO `url`: no pisar el parámetro (la URL de la página),
            # que el guard final "ninguno parseable" pasa a diag.record — con la
            # local pisada culparía a la última oferta en vez de a la página.
            # None = URL no utilizable (host ajeno, esquema raro, malformada):
            # sin URL propia la oferta NO se emite (r6/H1, G3); si cae la
            # página entera, el guard final registra su issue.
            abs_url = _resolve_job_url(rel_url, host)
            if abs_url is None:
                continue
            description = strip_html_tags(_s(item.get("textSnippet")))
            # Solo currency+period del display; los importes vienen en EUR/GBP y la
            # conversión a CHF + anualización la hace DataNormalizer.normalize_salary
            # aguas abajo. Prellenar salary_*_chf con EUR haría que su early-return
            # los guardase como CHF sin convertir (p.ej. €22/h → 22 CHF/año).
            _, _, currency, period = _parse_salary(_s(item.get("salary")))

            # `id` saneado como el resto de campos (misma familia que r6/H2,
            # pero el coste era MAYOR): crudo, un id no-hashable (lista/dict)
            # lanzaba TypeError en el set de `_dedupe_new` y escapaba hasta la
            # red externa del task — UNA oferta corrupta perdía la cosecha de
            # AMBOS hosts. int o str pasan (el portal real emite siempre int);
            # cualquier otro tipo degrada a None, el mismo camino que un item
            # sin id: el stub entra sin dedupe.
            raw_id = item.get("id")
            # bool es subclase de int: `true` no es un id utilizable (True == 1
            # colisionaría en el set de dedupe con un id entero real).
            job_id = (
                raw_id
                if isinstance(raw_id, (int, str)) and not isinstance(raw_id, bool)
                else None
            )

            stubs.append(
                {
                    "id": job_id,  # id de plataforma StepStone (dedupe entre hosts)
                    "title": title,
                    "company": _s(item.get("companyName")).strip() or "Unknown",
                    "location": _s(item.get("location")).strip(),
                    "url": abs_url,
                    "remote": True,  # DERIVADO DEL SCOPE /jobs/work-from-home, no del item
                    "description": description,
                    "logo": self._clean_logo(_s(item.get("companyLogoUrl"))),
                    "salary_original": _s(item.get("salary")).strip() or None,
                    "salary_min_chf": None,  # lo rellena normalize_salary tras convertir
                    "salary_max_chf": None,
                    "salary_currency": currency,
                    "salary_period": period,
                    # Fecha de publicación del portal (item.datePosted, ISO8601 Z)
                    "date_posted": item.get("datePosted"),
                }
            )

        # Lista NO vacía de la que no sale ni un stub: estructura desconocida
        # (p. ej. StepStone renombró `title`/`url` y todo cayó en los
        # `continue`), no vacío legítimo — mismo guard que financejobs
        # ("N elementos y ninguno parseable"). `items == []` no entra, y con
        # >=1 stub válido el run sigue siendo `ok`.
        if items and not stubs:
            logger.error(_REDEPLOY_MSG)
            diag.record(
                diag.KIND_NETWORK,
                url,
                detail=(
                    f"{_REDEPLOY_MSG} (searchResults.items trae "
                    f"{len(items)} elementos y ninguno es parseable)"
                ),
            )
        return stubs

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        """Entrada testeable: stubs de una página usando el host primario."""
        data = self._decode_state_from_soup(soup, self.LISTING_URL)
        if data is None:
            return []
        return self._items_to_stubs(data, self.HOSTS[0], self.LISTING_URL)

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
                # `url=` explícita: con DOS hosts, el diagnóstico debe culpar
                # al que cayó — sin ella el fallback a LISTING_URL atribuía
                # los fallos de jobs.ie a irishjobs.ie (VD.10, H3).
                response = await self._request_with_retry(
                    lambda u=url: client.get(u), url=url
                )
            except (CircuitBreakerOpen, httpx.HTTPError) as e:
                logger.error("%s %s page %d error: %s", self.SOURCE_NAME, host, page, e)
                # G2/P2-1: corte por FALLO, no fin de listado — sin marcarlo,
                # el cursor aprendía las páginas cosechadas y el early-stop
                # del run siguiente enterraba las ofertas de la página caída
                # (mismo trato que el motor base, G1/P2-4).
                self._stop_reason = "error"
                break

            if await self._listing_status_stops(response.status_code, page):
                self._stop_reason = "error"  # G2/P2-1
                break

            soup = BeautifulSoup(response.text, "lxml")
            data = self._decode_state_from_soup(soup, url)
            if data is None:
                # Decode falló (redeploy ya logueado): abortar este host, no petar.
                await self._maybe_report_soft_block(response.text, page)
                self._stop_reason = "error"  # G2/P2-1
                break

            page_stubs = self._items_to_stubs(data, host, url)
            if not page_stubs:
                break

            if total is None:
                # `meta`/`total` degenerados no deben tumbar la cosecha (r5/H1,
                # letra de G1): aquí YA hay stubs (searchResults es dict — lo
                # verificó _items_to_stubs), así que un total ilegible solo
                # pierde el corte por total; siguen cortando la página
                # incompleta y el tope de páginas.
                meta = data["searchResults"].get("meta")
                candidate = meta.get("total") if isinstance(meta, dict) else None
                # bool es subclase de int: `true` no es un total utilizable.
                if isinstance(candidate, int) and not isinstance(candidate, bool):
                    total = candidate

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
    # Normalización al esquema unificado (22 claves)
    # ------------------------------------------------------------------

    @staticmethod
    def job_identity(job: dict) -> str:
        """Identidad para cursor/early-stop = id de PLATAFORMA (G1/P3-7).

        StepStone sirve la MISMA oferta en dos hosts con URLs distintas: con
        la URL como identidad, las compartidas que `_dedupe_new` descarta
        antes de persistir nunca entraban en el cursor y el segundo host se
        re-crawleaba a presupuesto completo cada run. El id de plataforma es
        idéntico en ambos hosts — la misma identidad que ya usa el dedupe.
        Sin id (borde) cae a la identidad base (url).
        """
        source_id = job.get("source_id") or job.get("id")
        if source_id is not None and str(source_id).strip():
            return f"irishjobs:{source_id}"
        return BaseScraper.job_identity(job)

    def normalize_job(self, raw: dict) -> dict:
        title = raw.get("title", "").strip()
        company = raw.get("company", "Unknown").strip() or "Unknown"
        url = raw.get("url", "").strip()
        description = raw.get("description", "")
        location = raw.get("location", "").strip()

        tags = extract_job_skills(title, description)

        return {
            "hash": self.compute_hash(title, company, url),
            # Identidad de plataforma para el cursor (job_identity, P3-7).
            # upsert_job filtra a columnas del modelo: la clave extra no viaja
            # a la BD.
            "source_id": raw.get("id"),
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
            "published_at": parse_published_at(raw.get("date_posted")),
        }
