"""Scraper for Gastrojob.ch — hospitality and gastronomy jobs in Switzerland.

TYPO3 (extensión mxn_gastrojob). El listado público /stellen monta las ofertas
por JS: el HTML estático devuelve 200 con 0 enlaces de oferta, que es lo que
mató al scraper anterior (selectores especulativos ⇒ 0 stubs ⇒ el detector de
soft-block lo leyó como anti-bot ⇒ kill-switch, VD.4b). El reemplazo pagina
contra el endpoint AJAX del PROPIO frontend (la URL sale de los href de su
paginación, no de ingeniería inversa), que responde a httpx puro con un
fragmento HTML de 10 ofertas por página — sin Playwright.

Todos los selectores de este fichero están verificados contra fixtures
capturados en vivo el 2026-08-15 (tests/fixtures/gastrojob_listing_ajax_p*.html
y gastrojob_detail.html).
"""

import logging
import re
from datetime import datetime
from urllib.parse import urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from services.scraper_engine import BaseScraper
from utils import fetch_diagnostics as diag
from utils.dates import parse_published_at
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

BASE_URL = "https://www.gastrojob.ch"

# Path REAL de una oferta en el DOM del endpoint AJAX (fixtures VD.4b):
# /stellen/stelleninserat/<id numérico>. Estricto a propósito — también hace
# de "path propio": la portada o el listado nunca lo cumplen. `[0-9]` y no
# `\d`: en re sobre str, `\d` casa dígitos unicode (٢١) que el portal nunca
# emite y que romperían la estabilidad de la URL canónica.
_JOB_PATH_RE = re.compile(r"^/stellen/stelleninserat/([0-9]+)$")

# El listado mezcla las ofertas propias con anuncios de partners externos
# ("powered by hoteljob-schweiz.de") que apuntan a /stellen/externe-partner/<id>
# y que NO se cosechan (path fuera del canónico _JOB_PATH_RE). Comparten
# contador y paginación con las propias, y la zona partner empieza MUCHO antes
# del final (sonda 2026-08-15: p1-p5 100 % propias, p10 ya mezcla 8 propias +
# 2 partner, y p20/p30/p40/p50/p109-111 son 100 % partner): el bloque de
# ofertas propias son ~10 páginas de 111. Por eso reconocer una página
# partner-only DENTRO de rango como sana es imprescindible para G2 — es un
# estado real y frecuente del portal, no un caso límite de las 2 últimas
# páginas (ver parse_listing_page).
#
# Techo conocido (fase 3, H7): una página partner-only dentro de rango es
# INDISTINGUIBLE de "las ofertas propias se volvieron irreconocibles y solo
# quedaron visibles los partner" — no hay arreglo sin falsos positivos que
# romperían G2. Hoy lo mitigan dos cosas: las páginas realmente cosechadas
# (1-5 por techo MAX_PAGES, ~2 por presupuesto) no contienen partner, y un
# fragmento con enlaces propios rotos registra issue AUNQUE haya partner
# (H3). Es un techo asumido, no una garantía.
_PARTNER_AD_SELECTOR = 'a[href*="/stellen/externe-partner/"]'

# Enlace a oferta PROPIA en el fragmento AJAX (el path canónico de
# _JOB_PATH_RE como selector CSS). Constante única — se usa para iterar los
# ítems y para el veredicto "hay enlaces propios y ninguno es parseable":
# si el portal cambia el path solo hay que tocar aquí y el regex.
_OWN_AD_SELECTOR = 'a[href*="/stellen/stelleninserat/"]'

# "Erstmals aktiviert: 14.08.2026 (14:08)" — primera activación del anuncio,
# impresa en cada ítem del listado (div.hidden-jossen) y en el detalle. Es la
# fecha de publicación: coincide con el meta[itemprop=datePosted] del detalle
# (fixture 55327: 14.08.2026 ↔ content="2026-08-14"). `[0-9]` y no `\d` por
# la misma regla que _JOB_PATH_RE (VD.4b r2): el portal nunca imprime dígitos
# unicode (١٤) y no deben aceptarse como fecha.
_ERSTMALS_RE = re.compile(
    r"Erstmals aktiviert:\s*"
    r"([0-9]{2})\.([0-9]{2})\.([0-9]{4})\s*\(([0-9]{2}):([0-9]{2})\)"
)

# El texto de div.description tiene el patrón "<Jornada> bei <Empresa> in
# <Cantón>", con jornada a veces vacía (" bei Landgasthaus... in Aargau").
# `.*?` perezoso ⇒ corta en el PRIMER " bei "; `.+` codicioso en empresa ⇒ el
# " in " que separa es el ÚLTIMO (una empresa con " in " en el nombre no rompe).
_DESCRIPTION_RE = re.compile(
    r"^\s*(?P<workload>.*?)\s*\bbei\s+(?P<company>.+)\s+in\s+(?P<location>.+?)\s*$"
)

# El portal imprime "Erstmals aktiviert" en hora local suiza (sin zona).
# Se ancla a Europe/Zurich (DST incluido) para que published_at salga
# timezone-aware correcto y no desplazado 1-2 h al asumir UTC.
_ZURICH = ZoneInfo("Europe/Zurich")


def _resolve_job_url(href: str | None) -> tuple[str, str] | None:
    """Resuelve un href del listado a (URL absoluta de la oferta, source_id),
    o None si no es utilizable como oferta.

    Misma regla que `stelle_admin._resolve_job_url` (VD.1): una oferta sin URL
    propia no es utilizable y se salta — y el host se valida para no abrir un
    bypass. `urljoin` resuelve los relativos (los href del fragmento AJAX lo
    son: "/stellen/stelleninserat/55327"), nunca se concatena.
    """
    if not href:
        return None
    # Diferencial de parsers urllib/WHATWG: urllib solo corta el netloc en
    # `/ ? #`; los navegadores también en `\`. Se rechaza el `\` ANTES de
    # parsear — ningún href legítimo de gastrojob.ch lo contiene.
    if "\\" in href:
        return None
    try:
        parts = urlsplit(urljoin(BASE_URL, href))
    except ValueError:
        # Href malformado que revienta el parser de urllib (p. ej. un `[` en
        # posición de autoridad ⇒ "Invalid IPv6 URL"). Debe degradar ESTA
        # oferta, nunca la página ni el run: sin captura, la excepción escapa
        # de parse_listing_page hasta scraping_tasks saltándose diag.classify
        # y source_health — el run se queda sin veredicto (VD.4b H1).
        return None
    # Solo http(s): un esquema con autoridad (javascript:, ftp://...) no es
    # una oferta navegable aunque su path imite el patrón.
    if parts.scheme not in ("http", "https"):
        return None
    # Sin userinfo: `https://algo@www.gastrojob.ch/...` permite spoofing
    # visual del enlace que ve el usuario.
    if parts.username is not None:
        return None
    # `.hostname` minusculiza y quita el puerto. Con esto un
    # `//evil.com/stellen/stelleninserat/1` protocolo-relativo queda fuera.
    host = parts.hostname or ""
    if host != "gastrojob.ch" and not host.endswith(".gastrojob.ch"):
        return None
    match = _JOB_PATH_RE.match(parts.path)
    if not match:
        return None
    # URL canónica RECONSTRUIDA, no la parseada (VD.4b H4): esquema y host
    # fijos, sin query/fragment/puerto. El hash de dedup (title|company|url)
    # e ix_jobs_url comparan la URL literal — si el portal añadiera mañana un
    # `?tracking=` (o variara mayúsculas/puerto), cada variante sería una
    # fila nueva por oferta.
    # Techo conocido (VD.4b r2-H4): un href de subdominio
    # (https://jobs.gastrojob.ch/stellen/stelleninserat/9) pasa el check de
    # host pero se reescribe a www — hoy el portal no sirve ofertas por
    # subdominio; si empezara, la URL emitida podría dar 404.
    return f"{BASE_URL}{parts.path}", match.group(1)


def _parse_erstmals_aktiviert(text: str) -> str | None:
    """Extrae la fecha de "Erstmals aktiviert: DD.MM.YYYY (HH:MM)" como
    ISO8601 con offset suizo, o None si el patrón no está o la fecha es basura.

    Devuelve str (no datetime) a propósito: `utils.dates.parse_published_at`
    es la única puerta a published_at (rango de cordura + normalización UTC),
    igual que en el resto de scrapers.
    """
    match = _ERSTMALS_RE.search(text)
    if not match:
        return None
    day, month, year, hour, minute = (int(g) for g in match.groups())
    try:
        # Transiciones DST de Europe/Zurich, decisión deliberada (fase 3): se
        # acepta el fold=0 implícito sin validar la transición. La hora
        # INEXISTENTE de primavera (02:00-03:00 del último domingo de marzo)
        # no puede llegar aquí: el portal imprime un instante real en reloj
        # civil suizo y ese reloj se salta esa hora. La hora AMBIGUA de otoño
        # (02:00-03:00 repetida en octubre) sale con el PRIMER instante
        # (CEST, +02:00): error máximo de 1 hora, una noche al año, sobre una
        # ventana de cosecha de 7 días. Devolver None en esos casos sería
        # peor: se perdería la fecha completa y published_at alimenta esa
        # ventana — perder la oferta por 60 minutos de imprecisión es
        # exactamente el fallo que esta fase vino a eliminar.
        local_dt = datetime(year, month, day, hour, minute, tzinfo=_ZURICH)
    except ValueError:
        # Fecha imposible (32.13...): se degrada ESTA fecha, no la oferta.
        return None
    return local_dt.isoformat()


def _unreadable_count_detail(raw: str) -> str:
    """Mensaje ÚNICO del veredicto "contador ilegible" (los tests lo fijan).

    Lo comparten el rechazo por regex y el ValueError de int() (r2/H4): un
    contador que exceda el límite de conversión decimal de CPython (4300
    dígitos) es tan ilegible como uno no numérico. Se trunca el valor solo en
    el caso patológico para no arrastrar miles de dígitos al diagnóstico
    (last_error_detail es String(500)).
    """
    shown = raw if len(raw) <= 64 else f"{raw[:64]}… [{len(raw)} chars]"
    return f"contador de anuncios ilegible ({shown!r}): estructura desconocida"


class GastrojobScraper(BaseScraper):
    SOURCE_NAME = "gastrojob"
    LISTING_URL = f"{BASE_URL}/stellen"
    RATE_LIMIT_SECONDS = 2.0
    # El portal lista 1.104 ofertas en 111 páginas (sonda 2026-08-15), pero NO
    # se cosecha entero: con política WINDOW (7 días) y ~2.4 ofertas nuevas/día
    # observadas en el fixture p1, la ventana cabe en ~2 páginas. 5 es solo el
    # techo duro: NO habrá run de bootstrap con las 5 — el cursor heredado de
    # la era muerta ya tiene bootstrap_complete=t (consecutive_empty_runs=11,
    # avg_new=0), así que el CrawlerBudgetService dará max_pages_this_run=2 al
    # primer run real. Sin pérdida: 2 páginas cubren ~17 días de novedades
    # frente a la ventana de 7 (verificado VD.4b; peticiones ∝ novedades,
    # docs/SCRAPING_HUMANO.md — nunca total/page_size).
    MAX_PAGES = 5
    # El endpoint AJAX responde a httpx puro (sonda + fixtures 2026-08-15:
    # 200, ~8,7 KB, 10 ofertas). Playwright solo haría falta para la página
    # /stellen renderizada — más coste y más fragilidad para el mismo dato.
    NEEDS_PLAYWRIGHT = False
    # El listado no trae descripción; el detalle expone microdata
    # schema.org/JobPosting completa (descripción, datePosted, empresa).
    FETCH_DETAILS = True
    # El fragmento AJAX trae 10 ofertas por página (fixtures p1/p2); el motor
    # da por terminada la paginación cuando una página trae menos de esto.
    PAGE_SIZE = 10
    # El frontend envía Referer en su propia llamada AJAX; el endpoint lo
    # espera (verificado con httpx puro en la sonda del diagnóstico).
    DEFAULT_HEADERS = {**BaseScraper.DEFAULT_HEADERS, "Referer": LISTING_URL}
    # VD.4b H5 (rev. fase 3): una página fuera de rango devuelve el contador
    # TOTAL (p. ej. 1108) con 0 ítems — indistinguible de "estructura
    # desconocida" mirando solo el fragmento. La distinción se hace por el
    # NÚMERO de página: del contador se deriva la última página que puede
    # existir (ceil(anunciadas / PAGE_SIZE)) y solo un fragmento vacío MÁS
    # ALLÁ de ella es fin de paginación legítimo. Verificado en vivo
    # 2026-08-15: 1108 anunciadas ⇒ el propio widget de paginación del portal
    # termina en la 111 = ceil(1108/10), y la 112+ llega vacía con el mismo
    # contador. El número de página lo deja build_listing_url en
    # _current_page; se rearma en fetch_jobs porque las instancias pueden
    # reutilizarse entre cosechas (mismo patrón que _run_block_reported en
    # BaseScraper.fetch_jobs).
    _current_page = 1

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        # Rearme por run del estado de página: defensa en profundidad SIN
        # comportamiento observable hoy — el flujo normal siempre pasa por
        # build_listing_url, que lo fija antes de cada parseo. Se conserva
        # para instancias reutilizadas por si ese invariante cambiara.
        self._current_page = 1
        return await super().fetch_jobs(query, location)

    def build_listing_url(self, page: int, query: str) -> str:
        # Endpoint AJAX de paginación del propio frontend — el patrón (y su
        # percent-encoding de corchetes/@) es el de los href de la paginación
        # del sitio. `query` se ignora: se cosecha el listado completo, como
        # en el resto de scrapers de listado.
        # La página pedida se recuerda para que parse_listing_page pueda
        # distinguir un vacío fuera de rango de uno dentro del rango (H5).
        self._current_page = page
        params = {
            "type": "5000",
            "tx_mxngastrojob_ajax[action]": "filteredAds",
            "tx_mxngastrojob_ajax[controller]": "Ajax",
            "tx_mxngastrojob_ajax[@widget_0][currentPage]": str(page),
            "tx_mxngastrojob_ajax[search]": "",
            "tx_mxngastrojob_ajax[filter]": "[]",
        }
        return f"{BASE_URL}/?{urlencode(params)}"

    def _record_structure_failure(self, detail: str) -> None:
        """Registra un fallo de estructura como error de fetch VISIBLE.

        Un HTTP 200 cuyo contenido no podemos leer NO es "no hay ofertas": se
        anota en fetch_diagnostics para que el veredicto del run sea `error`
        (source_health), no `empty` en silencio — la misma garantía que
        financejobs (VD.7) y el bug exacto que mató a esta fuente (VD.4b).
        """
        logger.error("gastrojob: %s", detail)
        diag.record(diag.KIND_NETWORK, self.LISTING_URL, detail=detail)

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        """Extrae stubs del fragmento AJAX (10 ofertas por página).

        Estructura real de cada ítem (fixtures 2026-08-15): un <a> con href
        "/stellen/stelleninserat/<id>" envuelve el article.row.column, con
        h2 (título), div.description ("<Jornada> bei <Empresa> in <Cantón>")
        y div.hidden-jossen ("Erstmals aktiviert: DD.MM.YYYY (HH:MM)").
        """
        stubs: list[dict] = []
        # Dedup por URL absoluta dentro del run (misma defensa que
        # stelle_admin contra colisiones en ix_jobs_url).
        seen_urls: set[str] = set()

        for link in soup.select(_OWN_AD_SELECTOR):
            resolved = _resolve_job_url(link.get("href", ""))
            if resolved is None:
                continue
            url, source_id = resolved
            if url in seen_urls:
                continue

            title_el = link.select_one("h2")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                continue
            seen_urls.add(url)

            # "<Jornada> bei <Empresa> in <Cantón>" — si el patrón no casa,
            # no se inventa nada: la microdata del detalle rellena la empresa.
            desc_el = link.select_one("div.description")
            desc_match = _DESCRIPTION_RE.match(desc_el.get_text()) if desc_el else None
            workload = desc_match.group("workload").strip() if desc_match else ""
            company = desc_match.group("company").strip() if desc_match else ""
            location = desc_match.group("location").strip() if desc_match else ""

            date_el = link.select_one("div.hidden-jossen")
            date_posted = (
                _parse_erstmals_aktiviert(date_el.get_text()) if date_el else None
            )

            stubs.append(
                {
                    "title": title,
                    "company": company or "Unknown",
                    "location": location,
                    "detail_url": url,
                    "url": url,
                    "source_id": source_id,
                    "employment_type": workload or None,
                    "date_posted": date_posted,
                }
            )

        if stubs:
            return stubs

        # 0 stubs: distinguir "vacío legítimo" de "estructura desconocida".
        # Orden de veredictos (rev. fase 3, H3):
        #   1. sin contador ⇒ el fragmento no es del portal (fixtures: SIEMPRE
        #      incluye <div data-mxn-advertisements-count="1104">) ⇒ VISIBLE;
        #   2. contador ilegible (no numérico o negativo) ⇒ VISIBLE;
        #   3. hay enlaces de oferta PROPIA y ninguno produjo stub ⇒ ilegible
        #      se mire como se mire ⇒ VISIBLE — el número de página y el
        #      contador son irrelevantes para este veredicto;
        #   4. solo para fragmentos SIN ningún enlace propio: más allá de la
        #      última página que el contador cubre ⇒ fin de paginación, y
        #      partner-only dentro de rango ⇒ vacío legítimo (ambos sin
        #      issue, la vía de rehabilitación de G2);
        #   5. cualquier otra cosa ⇒ cambio de estructura VISIBLE, aunque
        #      ocurra a mitad de run tras páginas buenas.
        count_el = soup.select_one("div[data-mxn-advertisements-count]")
        if count_el is None:
            self._record_structure_failure(
                "fragmento AJAX sin div[data-mxn-advertisements-count]: "
                "estructura desconocida (¿cambió la extensión mxn_gastrojob?)"
            )
            return stubs

        announced_raw = count_el.get("data-mxn-advertisements-count", "")
        # Misma regla `[0-9]` estricta que _JOB_PATH_RE y _ERSTMALS_RE (el
        # portal nunca emite dígitos unicode): int() a secas es más laxo —
        # acepta "١٠٤" (→104), "+20", " 20 " y "1_0" — y un contador así
        # derivaría un rango de páginas erróneo que puede SILENCIAR páginas
        # como falsos "fuera de rango". El fullmatch también rechaza el "-",
        # así que cubre el contador negativo sin check aparte.
        if not re.fullmatch(r"[0-9]+", announced_raw):
            self._record_structure_failure(_unreadable_count_detail(announced_raw))
            return stubs
        try:
            announced = int(announced_raw)
        except ValueError:
            # Un contador de miles de dígitos pasa el [0-9]+ pero int() lanza
            # (límite CPython de 4300 dígitos, r2/H4) y la excepción escapaba
            # del parser. Mismo veredicto y mismo issue que el no numérico.
            self._record_structure_failure(_unreadable_count_detail(announced_raw))
            return stubs

        if soup.select_one(_OWN_AD_SELECTOR) is not None:
            # Hay enlaces de oferta propia pero ninguno produjo stub (título
            # ausente, href no resoluble...): misma regla que financejobs
            # ("N elementos y ninguno parseable") ⇒ estructura desconocida.
            # ANTES que rango/partner (H3): una página "fuera de rango" con
            # enlaces propios rotos salía como vacío legítimo silencioso.
            self._record_structure_failure(
                f"la página {self._current_page} trae enlaces de oferta y "
                "ninguno es parseable: estructura desconocida"
            )
            return stubs

        # Última página que el contador puede cubrir. Con 0 anunciadas sale
        # 0 y CUALQUIER página queda fuera de rango: el vacío legítimo de un
        # listado sin ofertas no registra issue (la vía de rehabilitación).
        # Ceil ENTERO y no math.ceil (r3/H3): la división float de un
        # contador de 310-4300 dígitos lanza OverflowError, que escapaba del
        # parser. Idéntico a math.ceil para todo entero >= 0.
        last_expected_page = (announced + self.PAGE_SIZE - 1) // self.PAGE_SIZE
        if self._current_page > last_expected_page:
            return stubs

        if soup.select_one(_PARTNER_AD_SELECTOR) is not None:
            # Página compuesta SOLO por anuncios de partner: estructura
            # reconocida con 0 ofertas propias ⇒ vacío legítimo, sin issue.
            # La zona partner empieza sobre la p10 (no solo al final) y este
            # veredicto tiene un techo asumido — ver _PARTNER_AD_SELECTOR.
            return stubs

        self._record_structure_failure(
            f"el portal anuncia {announced} ofertas y la página "
            f"{self._current_page} (≤ {last_expected_page} esperadas) no "
            "trae ninguna reconocible: estructura desconocida, no "
            "'0 ofertas'"
        )
        return stubs

    def parse_job_detail(self, soup: BeautifulSoup) -> dict:
        """Extrae la microdata schema.org/JobPosting del detalle.

        Selectores verificados contra tests/fixtures/gastrojob_detail.html
        (captura real 2026-08-15). Un detalle sin la sección devuelve {}: el
        stub del listado ya es una oferta completa mínima.
        """
        detail: dict = {}
        section = soup.select_one(
            'section.ad-detail[itemtype="http://schema.org/JobPosting"]'
        )
        if section is None:
            return detail

        desc_el = section.select_one("div[itemprop=description]")
        if desc_el:
            detail["description"] = strip_html_tags(
                desc_el.get_text(separator="\n", strip=True)
            )

        # <meta itemprop="datePosted" content="2026-08-14"> — solo fecha:
        # fallback de la fecha con hora del listado (ver normalize_job).
        date_el = section.select_one("meta[itemprop=datePosted]")
        if date_el and date_el.get("content"):
            detail["detail_date_posted"] = date_el["content"]

        # La empresa canónica de la microdata NO pisa la del listado (la
        # identidad/hash debe ser estable aunque el fetch de detalle falle un
        # run); solo rellena si el listado no la dio (ver normalize_job).
        org_el = section.select_one("[itemprop=hiringOrganization] [itemprop=name]")
        if org_el:
            org_name = org_el.get("content") or org_el.get_text(strip=True)
            if org_name:
                detail["detail_company"] = org_name

        # Localidad concreta (el listado solo trae el cantón).
        loc_el = section.select_one("[itemprop=jobLocation] [itemprop=addressLocality]")
        if loc_el:
            locality = loc_el.get("content") or loc_el.get_text(strip=True)
            if locality:
                detail["address_locality"] = locality

        return detail

    def normalize_job(self, raw: dict) -> dict:
        title = raw.get("title", "").strip()
        company = raw.get("company", "Unknown").strip() or "Unknown"
        # La empresa del LISTADO es la identidad (hash estable run a run); la
        # microdata del detalle solo rellena el hueco si el listado no la dio.
        if company == "Unknown":
            company = (raw.get("detail_company") or "").strip() or "Unknown"
        url = raw.get("url", "").strip()
        description = raw.get("description", "")

        # Listado = cantón ("Neuenburg"); detalle = localidad ("Fontaines").
        # Se combinan para que extract_canton siga viendo el nombre del cantón.
        canton_name = raw.get("location", "").strip()
        locality = (raw.get("address_locality") or "").strip()
        if locality and canton_name:
            location = f"{locality}, {canton_name}"
        else:
            location = locality or canton_name or "Switzerland"

        tags = extract_job_skills(title, description)

        return {
            "hash": self.compute_hash(title, company, url),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": location,
            "canton": extract_canton(location),
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url,
            "remote": False,
            "tags": tags[: self.MAX_TAGS],
            "logo": None,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": None,
            "salary_currency": None,
            "salary_period": None,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": raw.get("employment_type"),
            # Fecha del portal: la del listado lleva hora (suiza, ya con
            # offset); la del detalle es solo YYYY-MM-DD y actúa de fallback.
            "published_at": parse_published_at(
                raw.get("date_posted") or raw.get("detail_date_posted")
            ),
        }
