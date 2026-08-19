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

import httpx

from scrapers.swiss_schools_config import WatchedSchool, schools_by_strategy
from services.circuit_breaker import CircuitBreakerOpen
from services.job_service import BaseJobProvider
from utils import fetch_diagnostics as diag

logger = logging.getLogger(__name__)


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
        # con externalPath — ya garantizado string no vacío por el guard de
        # _fetch_workday — y la oferta se emite, no se degrada.
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
