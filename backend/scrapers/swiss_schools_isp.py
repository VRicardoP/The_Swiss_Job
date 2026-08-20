"""Scraper para el portal Workday del grupo International Schools Partnership.

Cubre 1 colegio de la watchlist:
- Mosaic Ecole (Geneva)

Estrategia:
- Workday expone una API JSON pública en /wday/cxs/{tenant}/{site}/jobs
- POST con cuerpo {searchText, limit, offset} devuelve lista paginada
- Filtramos por nombre del colegio en locationsText (ej. "Mosaic School")
- Categoría fijada a "A" para saltarse la penalización H

⚠ `published_at` se deja DELIBERADAMENTE en None (ticket 2A / ADR-10):
Workday solo expone `postedOn` como texto relativo en buckets
("Posted 30+ Days Ago"), no una fecha. Aproximarla inventaría precisión
que no existe y rozaría la prohibición de derivar la fecha de cuándo la
vimos nosotros. NO lo "arregles": la política por fuente del ticket 2B
decide qué hacer con las fuentes sin fecha.
"""

import logging
import re
from urllib.parse import unquote_to_bytes

import httpx

from scrapers.swiss_schools_config import WatchedSchool, schools_by_strategy
from services.circuit_breaker import CircuitBreakerOpen
from services.job_service import BaseJobProvider
from utils import fetch_diagnostics as diag

logger = logging.getLogger(__name__)

# Esquema URI según RFC 3986 (letra seguida de letras/dígitos/+/-/.): "http:",
# "https:", "javascript:", etc. Cualquier esquema convierte el valor en URL
# absoluta, no en ruta de oferta.
_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

# Sintaxis de percent-encoding (RFC 3986 §2.1): todo '%' forma EXACTAMENTE un
# triplete hexadecimal (%HH). Las dos ramas son excluyentes por su primer
# carácter, así que el match es lineal. "x%", "x%2" y "x%GG" no cumplen.
_WELL_FORMED_PERCENT_RE = re.compile(r"(?:[^%]|%[0-9A-Fa-f]{2})*")

# Niveles de decodificación PARA INSPECCIÓN: dos, y solo dos — el segundo caza
# la doble codificación ("%252E%252E" → "%2E%2E" → ".."). El servidor real
# decodifica una vez; el nivel extra es defensa contra evasión, no semántica.
_INSPECTION_DECODE_LEVELS = 2

# Triplete %HH sobre bytes: si tras las dos decodificaciones de inspección aún
# queda uno, hay anidamiento a TRES o más niveles — la misma familia de evasión
# un escalón más allá ("%25252E" → "%252E" → "%2E"). Subir el techo solo
# movería el problema al nivel siguiente: se falla cerrado. Un '%' suelto sin
# triplete ("%25y" → "%y") es dato legítimo y no dispara este guard.
_PERCENT_TRIPLET_RE = re.compile(rb"%[0-9A-Fa-f]{2}")


def _decoded_segment_issue(decoded: bytes) -> str | None:
    """Motivo por el que un segmento DECODIFICADO no es dato de una ruta.

    Opera sobre bytes (decodificación byte a byte, como hace el servidor)
    y, tras los checks estructurales, exige que esos bytes formen UTF-8
    válido: la API real responde 400 a surrogates codificados (%ED%A0%80)
    y a formas overlong (%C0%AF) — octava revisión. Se rechaza lo que
    cambia QUÉ recurso direcciona la URL (controles C0 y DEL, barra
    invertida, separador '/', travesía '.'/'..') y lo que no es texto. El
    percent-encoding legítimo (%20, %C3%A9, CJK, emoji...) es dato válido.
    """
    if any(byte < 0x20 or byte == 0x7F for byte in decoded):
        return "codifica caracteres de control"
    if 0x5C in decoded:  # '\'
        return "codifica barra invertida ('%5C')"
    if 0x2F in decoded:  # '/'
        return "codifica separador de ruta ('%2F')"
    if decoded in (b".", b".."):
        return "codifica segmento de travesía ('.' o '..')"
    try:
        # La API real responde 400 a octetos que no forman texto UTF-8
        # (surrogates, overlong): esos bytes no identifican una oferta.
        decoded.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "codifica bytes que no son UTF-8 válido"
    return None


def _percent_encoding_issue(path: str) -> str | None:
    """Motivo por el que el percent-encoding de `path` invalida la ruta.

    Séptima revisión: la validación de forma miraba solo los caracteres
    CRUDOS — "%00", "%GG" o "%252E%252E" pasaban enteros y la API real los
    responde con 400/404 (ninguno identifica una oferta). Octava: los bytes
    decodificados deben formar UTF-8 válido, y un triplete %HH que
    sobreviva a las dos decodificaciones delata anidamiento a ≥3 niveles.
    Pasos:
    1. sintaxis: todo '%' forma exactamente un triplete hexadecimal;
    2. decodificar cada segmento SOLO para inspección, máximo dos niveles;
    3. sobre lo decodificado, rechazar controles, '\\', '/', travesía y
       bytes que no formen UTF-8;
    4. si tras las dos decodificaciones aún queda un triplete %HH, fallo
       cerrado por codificación anidada excesiva.
    La ruta ORIGINAL es la que se persiste; aquí no se transforma nada.
    """
    if _WELL_FORMED_PERCENT_RE.fullmatch(path) is None:
        return "percent-encoding malformado ('%' sin triplete hexadecimal)"
    for segment in path.split("/"):
        decoded = segment.encode("utf-8")
        for _ in range(_INSPECTION_DECODE_LEVELS):
            if b"%" not in decoded:
                break
            decoded = unquote_to_bytes(decoded)
            issue = _decoded_segment_issue(decoded)
            if issue is not None:
                return issue
        if _PERCENT_TRIPLET_RE.search(decoded):
            return "codificación anidada excesiva (%HH tras dos niveles)"
    return None


def _external_path_shape_issue(path: str) -> str | None:
    """Motivo (en español) por el que `path` NO tiene forma de ruta de oferta.

    Devuelve None si la forma es la esperada: ruta relativa bajo /job/ con
    al menos un segmento no vacío; sin query, fragmento, esquema ni
    autoridad; y sin barras invertidas, separadores, travesía ni caracteres
    de control — ni crudos ni escondidos tras percent-encoding (sintaxis
    %HH estricta, inspección decodificada a dos niveles con UTF-8 estricto
    y fallo cerrado ante anidamiento: ver `_percent_encoding_issue`).
    `path` llega ya con strip aplicado.
    La sonda en vivo (r6/H1)
    demostró que un externalPath="?job=123" pasaba el guard de tipo y se
    persistía como URL "válida" que no lleva a ninguna oferta: la forma es
    parte del contrato, no solo el tipo.
    """
    if not path.startswith("/job/"):
        # Cubre también "?job=123", "#job", "/not-a-job", rutas relativas
        # sin barra inicial y la autoridad "//host/..." (su 2º carácter es
        # '/'): nada de eso es una ruta de oferta bajo el career site.
        if path.startswith("//"):
            return "empieza por '//' (autoridad, no ruta relativa)"
        if _URI_SCHEME_RE.match(path):
            return "lleva esquema URI, no es ruta relativa"
        return "no empieza por /job/"
    if "?" in path:
        return "contiene query ('?')"
    if "#" in path:
        return "contiene fragmento ('#')"
    if "\\" in path:
        return "contiene barra invertida ('\\')"
    # Controles C0 (incluye \t\n\r internos) y DEL: jamás forman parte de una
    # ruta legítima y romperían la URL construida por concatenación.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path):
        return "contiene caracteres de control"
    # Sustitutos UTF-16 sueltos (r7 ronda de análisis 2): json.loads acepta
    # '"\\ud800"' y produce un str NO codificable a UTF-8 — el encode de la
    # inspección de percent-encoding lanzaría UnicodeEncodeError, que
    # escaparía de fetch_jobs con 0 issues (la clase G1 otra vez). Además
    # ningún sustituto suelto es Unicode intercambiable: ni una URL real ni
    # la fila que persistiríamos pueden contenerlo.
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in path):
        return "contiene sustitutos UTF-16 sueltos"
    # Tras el strip cualquier espacio es interno: una URL con espacio crudo
    # no identifica una oferta (Workday los codifica como %20 o guiones).
    if any(ch.isspace() for ch in path):
        return "contiene espacios internos"
    # Segmentos tras el prefijo: "/job/x/y" → ["x", "y"]; "/job/" → [""].
    segments = path.split("/")[2:]
    # (B) "/job/" a secas es la página de LISTADO, no una vacante; un
    # segmento vacío intermedio ("//") o final (barra final) tampoco tiene
    # forma de oferta — las rutas reales llevan ≥1 segmento no vacío.
    if any(segment == "" for segment in segments):
        return "sin segmento de oferta tras /job/ (segmento vacío)"
    # (A) Travesía de directorio: "/job/../admin" pasaba todo lo anterior y
    # el navegador normaliza ".." — la URL persistida acababa FUERA de
    # /job/, la misma clase de defecto que H1 por otra puerta.
    if any(segment in (".", "..") for segment in segments):
        return "contiene segmento de travesía ('.' o '..')"
    # Percent-encoding (7ª revisión): todo lo anterior mira los caracteres
    # CRUDOS; esto valida sintaxis %HH y lo que el servidor DECODIFICARÍA
    # (controles, '\', '/', travesía — incluida la doble codificación tipo
    # "%252E"). Sustituye a la tirita del "%2E" de la ronda anterior. La
    # URL se construye SIEMPRE con la ruta original, nunca la decodificada.
    if "%" in path:
        return _percent_encoding_issue(path)
    return None


class SwissSchoolsISPScraper(BaseJobProvider):
    """Workday API directa — no usa el flujo HTML del BaseScraper."""

    SOURCE_NAME = "swiss_schools_isp"
    PAGE_SIZE = 20
    MAX_PAGES = 10
    # Watchlist igual que el resto de colegios (exención del backoff). Se declara
    # aquí a mano porque este scraper NO hereda de SwissSchoolBaseScraper: usa la
    # API de Workday, no el flujo HTML. Sin esta línea quedaría aparcado mientras
    # los otros 7 colegios se consultan a diario.
    WATCHLIST_SOURCE = True

    def __init__(self):
        super().__init__()
        self._schools: list[WatchedSchool] = schools_by_strategy("isp_workday")

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        all_raw: list[dict] = []
        async with httpx.AsyncClient(timeout=20.0) as client:
            for school in self._schools:
                params = school.params or {}
                tenant = params.get("tenant", "")
                site = params.get("site", "")
                school_filter = params.get("school_filter", "").lower()
                if not tenant or not site:
                    continue

                raw_jobs = await self._fetch_workday(
                    client, tenant, site, school_filter
                )
                for r in raw_jobs:
                    r["_school"] = school
                    all_raw.append(r)

        results = self._process_raw_jobs(all_raw)
        return self._finalize_fetch(results)

    async def _fetch_workday(
        self,
        client: httpx.AsyncClient,
        tenant: str,
        site: str,
        school_filter: str,
    ) -> list[dict]:
        api_url = (
            f"https://{tenant}.wd3.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
        )
        results: list[dict] = []

        # Presupuesto dinámico de páginas si el pipeline lo inyectó (≤ MAX_PAGES).
        for page in range(self._pages_budget()):
            offset = page * self.PAGE_SIZE
            payload = {
                "appliedFacets": {},
                "limit": self.PAGE_SIZE,
                "offset": offset,
                "searchText": school_filter,
            }
            try:
                resp = await self._circuit.call(
                    lambda: client.post(api_url, json=payload)
                )
            except (CircuitBreakerOpen, httpx.HTTPError) as e:
                logger.error("ISP Workday fetch error: %s", e)
                # VD.10 — misma forma que `_request_with_retry` en BaseScraper:
                # sin issue este `break` acababa en `classify(0, [])` = `empty`
                # y la fuente ROTA se presentaba como fuente SECA.
                diag.record(
                    diag.KIND_NETWORK, api_url, detail=f"{type(e).__name__}: {e}"
                )
                break

            if resp.status_code != 200:
                logger.warning("ISP Workday HTTP %d", resp.status_code)
                # VD.10 — estado final no-200 (sin retry aquí: ya es definitivo).
                diag.record(diag.KIND_HTTP, api_url, status=resp.status_code)
                break

            try:
                data = resp.json()
            except (ValueError, RecursionError) as e:
                # Un 200 con cuerpo ilegible es la API rota (redeploy, WAF
                # sirviendo HTML), no un board vacío. KIND_NETWORK y no
                # KIND_HTTP: el transporte respondió 200 (no hay estado de
                # error que reportar) y `fetch_diagnostics` asigna los fallos
                # de parseo a `network_error` — igual que `utils.http`, que
                # agrupa JSONDecodeError con los errores de red.
                # ValueError y no JSONDecodeError (r3/H4): un cuerpo que no es
                # UTF-8 (b'\xff') lanza UnicodeDecodeError — otra subclase de
                # ValueError — que escapaba con 0 issues y el run salía
                # `empty` (falso vacío, G1). RecursionError (r3/R10): el
                # anidamiento extremo también escapa del parseo de JSON.
                logger.error("ISP Workday invalid JSON body: %s", e)
                diag.record(
                    diag.KIND_NETWORK, api_url, detail=f"{type(e).__name__}: {e}"
                )
                break

            # Camino gemelo del cuerpo ilegible (r4/R3-2): un 200 con JSON
            # válido pero NO-objeto (`null`, una lista) hacía que
            # `data.get(...)` lanzara AttributeError que escapaba de
            # fetch_jobs con 0 issues. `utils.http` ya trata explícitamente
            # el "200 con cuerpo JSON null"; mismo guard que financejobs y
            # thehub.
            if not isinstance(data, dict):
                logger.error("ISP Workday malformed JSON body: not an object")
                diag.record(
                    diag.KIND_NETWORK,
                    api_url,
                    detail="JSON body is not an object (Workday redeploy?)",
                )
                break

            # `jobPostings` degenerado (null, string, objeto…) es el mismo
            # redeploy un nivel más adentro (r5/H2): `data.get` con default
            # devuelve el null EXISTENTE, no el default, y el bucle lanzaba
            # TypeError/AttributeError que escapaba de fetch_jobs con 0 issues
            # (letra de G1). Solo la lista es la forma reconocida; la API real
            # de Workday la devuelve SIEMPRE, incluida la búsqueda vacía
            # (`jobPostings: []`, `total: 0`) — G2: `[]` sigue siendo vacío
            # legítimo sin issue.
            postings = data.get("jobPostings")
            if not isinstance(postings, list):
                logger.error("ISP Workday malformed JSON body: jobPostings")
                diag.record(
                    diag.KIND_NETWORK,
                    api_url,
                    detail="jobPostings is not a list (Workday redeploy?)",
                )
                break

            # Filtro estricto por locationsText (ej. "Mosaic School / Ecole Mosaic")
            parseable = 0
            for p in postings:
                if not isinstance(p, dict):
                    continue  # elemento degenerado: degrada ese item, no la página
                parseable += 1
                location_text = p.get("locationsText")
                # locationsText no-string (lista, número...) es un item
                # estructuralmente inválido (r6/H3): el `.lower()` lanzaba
                # AttributeError y tumbaba el LOTE entero. Se registra issue
                # y se sigue con los objetos válidos — degrada el item, no la
                # página. La señal es el TIPO, nunca el nº de matches del
                # filtro: el tenant de Workday es compartido y 0 matches con
                # objetos válidos sigue siendo vacío legítimo (G2).
                if location_text is not None and not isinstance(location_text, str):
                    logger.error("ISP Workday malformed jobPosting: locationsText")
                    diag.record(
                        diag.KIND_NETWORK,
                        api_url,
                        detail=(
                            "locationsText no es string "
                            f"({type(location_text).__name__}): item degradado"
                        ),
                    )
                    continue
                if school_filter not in (location_text or "").lower():
                    continue
                # Oferta COINCIDENTE ilegible (r7/H1): el cierre anterior solo
                # validaba locationsText — un `title` no-string o un
                # `externalPath` vacío llegaban a normalize_job, que la
                # descartaba con log pero SIN issue: el run salía `empty`
                # (material de G1). Y un externalPath "" produce la página de
                # carreras del tenant como URL de oferta (G3: no es una URL
                # propia). Un ÚNICO issue por elemento coincidente inválido,
                # tenga uno o los dos campos rotos. El guard vive DESPUÉS del
                # filtro a propósito (G2): los elementos de otros colegios del
                # tenant compartido no se diagnostican — 0 matches con objetos
                # válidos sigue siendo vacío legítimo.
                title = p.get("title")
                external_path = p.get("externalPath")
                title_ok = isinstance(title, str) and bool(title.strip())
                path_ok = isinstance(external_path, str) and bool(external_path.strip())
                if not (title_ok and path_ok):
                    logger.error("ISP Workday malformed jobPosting: title/externalPath")
                    # El detail nombra SOLO los campos rotos: con `title="   "`
                    # los dos tipos serían `str` y no se sabría cuál falló.
                    # Presencia en la lista = campo ilegible (el tipo es
                    # contexto extra, no la señal).
                    broken = ", ".join(
                        f"{name}={type(value).__name__}"
                        for name, value, ok in (
                            ("title", title, title_ok),
                            ("externalPath", external_path, path_ok),
                        )
                        if not ok
                    )
                    diag.record(
                        diag.KIND_NETWORK,
                        api_url,
                        detail=(
                            f"oferta coincidente ilegible ({broken}): item degradado"
                        ),
                    )
                    continue
                # FORMA de externalPath (r6/H1): el guard de tipo dejaba pasar
                # "?job=123", "/not-a-job" o valores con esquema, que acababan
                # persistidos como URL "válida" sin oferta detrás (outcome=ok
                # con jobs fantasma). Solo una ruta relativa bajo /job/ (la
                # forma real de Workday, verificada en vivo) identifica una
                # oferta. Igual que el resto de guards de item: se degrada
                # SOLO este elemento, nunca la fuente. Y vive DESPUÉS del
                # filtro de colegio a propósito (G2, ver arriba): validar
                # elementos de otros colegios del tenant compartido apagaría
                # una fuente sana con 0 matches legítimos.
                external_path = external_path.strip()
                path_issue = _external_path_shape_issue(external_path)
                if path_issue is not None:
                    logger.error("ISP Workday malformed jobPosting: externalPath shape")
                    diag.record(
                        diag.KIND_NETWORK,
                        api_url,
                        detail=(
                            f"externalPath sin forma de ruta de oferta "
                            f"({path_issue}): item degradado"
                        ),
                    )
                    continue
                # Canonizar el valor ya validado: normalize_job y el fallback
                # de source_id trabajan con la ruta sin espacios de borde.
                p["externalPath"] = external_path
                results.append(p)

            # Página NO vacía sin un solo objeto: estructura desconocida, no un
            # board vacío. El guard mira el TIPO y no el filtro: 0 matches con
            # objetos reales es lo normal (tenant compartido con otros
            # colegios) y marcarlo rompería G2.
            if postings and not parseable:
                logger.error("ISP Workday malformed JSON body: jobPostings items")
                diag.record(
                    diag.KIND_NETWORK,
                    api_url,
                    detail=(
                        f"jobPostings trae {len(postings)} elementos y ninguno "
                        "es un objeto (Workday redeploy?)"
                    ),
                )
                break

            if len(postings) < self.PAGE_SIZE:
                break

        return results

    def normalize_job(self, raw: dict) -> dict:
        school: WatchedSchool = raw["_school"]
        external_path = raw.get("externalPath", "")
        # Detail URL: base_career_site + externalPath
        params = school.params or {}
        tenant = params.get("tenant", "")
        site = params.get("site", "")
        base = f"https://{tenant}.wd3.myworkdayjobs.com/en-US/{site}"
        url = f"{base}{external_path}"

        # Source ID: JR... de bulletFields. Un bulletFields degenerado
        # (escalar, lista sin string) hacía `bullet[0]` → TypeError y la
        # oferta COINCIDENTE se descartaba en _process_raw_jobs sin issue
        # (r7/H1, material de G1). Es solo material de source_id: se repara
        # con externalPath — ya garantizado por los guards de _fetch_workday
        # como ruta /job/ válida y sin espacios de borde — y la oferta se
        # emite, no se degrada.
        bullet = raw.get("bulletFields")
        if isinstance(bullet, list) and bullet and isinstance(bullet[0], str):
            source_id = bullet[0] or external_path
        else:
            source_id = external_path

        title = raw.get("title", "")
        location_text = raw.get("locationsText", "")

        job = {
            "source": self.SOURCE_NAME,
            "source_id": source_id,
            "title": title,
            "company": school.name,
            "location": location_text or f"{school.city}, CH",
            "url": url,
            # Categoría real la asigna el classifier; bypass en match_service.
            "tags": ["education", "international school", school.id],
            "language": "en",
        }
        job["hash"] = self.compute_hash(title, school.name, url)
        return job
