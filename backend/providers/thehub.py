"""Provider for The Hub (thehub.io) startup jobs API."""

import asyncio
import logging
import re

import httpx

from services.job_service import BaseJobProvider
from utils import fetch_diagnostics as diag
from utils.dates import parse_published_at
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

PAGE_DELAY_SECONDS = 0.5
# Mismo ritmo entre peticiones de detalle que entre páginas (~46 detalles por
# run con el volumen actual): flat 0.5s, la pauta de todos los providers.
DETAIL_DELAY_SECONDS = 0.5

# id de Mongo (ObjectId, 24 hex minúsculas). Se valida ANTES de interpolarlo
# en URLs (detalle y pública): un id inyectado por la API acabaría persistido
# como URL clicable y dispararía un GET con path traversal contra
# api.thehub.io. Las 40 filas antiguas de thehub en BD son 40/40 de esta forma.
_OBJECT_ID_RE = re.compile(r"^[0-9a-f]{24}$")


def _valid_job_id(value: object) -> str:
    """Devuelve el id saneado, o "" si no es un ObjectId (se trata como ausente)."""
    job_id = value.strip() if isinstance(value, str) else ""
    return job_id if _OBJECT_ID_RE.fullmatch(job_id) else ""


class TheHubProvider(BaseJobProvider):
    """Fetch remote jobs from The Hub (thehub.io) public API.

    La API no requiere auth ni gating de User-Agent. Devuelve las ofertas en
    `response["docs"]` (15 por página), con la paginación en la raíz
    (`total`, `limit`, `page`, `pages`). Filtramos por `isRemote=true`.

    Desde el rediseño SPA/Nuxt del portal (verificado 2026-08-14/15, VD.9)
    la API vive en api.thehub.io — la vieja `thehub.io/api/jobs` devuelve un
    404 de Keystone. El listado v2 viene ADELGAZADO (sin `absoluteJobUrl`,
    sin `description`, sin fechas), así que cada oferta necesita un paso de
    detalle (`/jobs/single/<id>`) para description, location completa y
    `createdAt`.
    """

    SOURCE_NAME = "thehub"
    API_URL = "https://api.thehub.io/v2/jobs"
    # El detalle funciona por `id` (Mongo); por `key` (slug) da 404.
    DETAIL_URL = "https://api.thehub.io/jobs/single/{job_id}"
    # URL pública propia del portal. Se CONSTRUYE (ya no llega
    # absoluteJobUrl) y por `id`, no por `key` (/jobs/<key> da 404): es
    # exactamente el formato de las filas antiguas en BD, así que la
    # deduplicación por URL/hash se conserva sin migración.
    PUBLIC_JOB_URL = "https://thehub.io/jobs/{job_id}"
    # Los logos se sirven desde el CDN imgix, no desde thehub.io (allí dan 404).
    LOGO_BASE = "https://thehub-io.imgix.net"
    MAX_PAGES = 5

    def _record_structure_failure(self, detail: str) -> None:
        """Registra un fallo de estructura como error de fetch VISIBLE.

        Un HTTP 200 cuyo contenido no podemos leer NO es "no hay ofertas": se
        anota en fetch_diagnostics para que el veredicto del run sea `error`
        (source_health), no `empty` en silencio — la misma garantía que
        financejobs (VD.7) y el resto de la fase de recuperación.
        """
        logger.error("thehub: %s", detail)
        diag.record(diag.KIND_NETWORK, self.API_URL, detail=detail)

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch remote jobs from The Hub, paginating hasta MAX_PAGES."""
        results: list[dict] = []
        max_pages = self._pages_budget()

        async with httpx.AsyncClient() as client:
            for page in range(1, max_pages + 1):
                # default arg `p=page` captura el valor y evita late-binding en el lambda
                data = await self._circuit.call(
                    lambda p=page: fetch_with_retry(
                        client,
                        self.API_URL,
                        params={"isRemote": "true", "page": p},
                    )
                )

                # SOLO el None de fetch_with_retry corta aquí: es un fetch
                # fallido cuyo issue ya registró utils.http. Un `{}` (200 con
                # JSON vacío) NO es lo mismo — con `if not data` caía en este
                # corte y el 200 ilegible salía `empty` en silencio; ahora
                # fluye al guard de la clave `docs`.
                if data is None:
                    break

                # JSON válido pero no-objeto (p. ej. una lista): no hay dónde
                # leer `docs` — sin este guard, `.get()` tiraba el lote con un
                # AttributeError. Mismo isinstance que ya aplica financejobs.
                if not isinstance(data, dict):
                    self._record_structure_failure(
                        f"respuesta malformada en la página {page} — "
                        "no es un objeto JSON; se corta la paginación"
                    )
                    break

                # La API real incluye `docs` SIEMPRE, incluso fuera de rango
                # (sonda 2026-08-15: page=9999 responde con docs: [] y las
                # claves de paginación). Un 200 sin la clave es la API
                # renombrando el campo — estructura desconocida, no "no hay
                # más ofertas": el default [] de .get() caía en el corte de
                # paginación sin issue y el run salía `empty` en silencio.
                if "docs" not in data:
                    self._record_structure_failure(
                        f"respuesta sin la clave 'docs' en la página {page}: "
                        "estructura desconocida; se corta la paginación"
                    )
                    break

                raw_jobs = data["docs"]
                # API malformada (docs no es una lista): cortar la paginación
                # conservando lo ya procesado. El corte se registra como
                # fallo de estructura: un 200 con `docs` ilegible NO es "no
                # hay más ofertas", y sin issue el run saldría `empty` en
                # silencio.
                if not isinstance(raw_jobs, list):
                    self._record_structure_failure(
                        f"respuesta malformada en la página {page} — "
                        "'docs' no es una lista; se corta la paginación"
                    )
                    break
                if not raw_jobs:
                    break

                enriched = await self._enrich(client, raw_jobs)
                page_jobs = self._process_raw_jobs(enriched)
                # Página NO vacía de la que no sale ni una oferta normalizable
                # (p. ej. la API renombró `title` o `id`): estructura
                # desconocida, no vacío legítimo — la misma regla que
                # financejobs ("N elementos y ninguno parseable"). `docs: []`
                # no entra aquí (corta arriba sin issue) y con 1 oferta válida
                # el run sigue siendo `ok`.
                if not page_jobs:
                    self._record_structure_failure(
                        f"la página {page} trae {len(raw_jobs)} docs y ninguno "
                        "es normalizable (estructura desconocida)"
                    )
                results.extend(page_jobs)

                # `pages` es el total de páginas; parar al alcanzar la última.
                total_pages = self._safe_int(data.get("pages"))
                if total_pages and page >= total_pages:
                    break

                if page < max_pages:
                    await asyncio.sleep(PAGE_DELAY_SECONDS)

        return self._finalize_fetch(results)

    async def _enrich(
        self, client: httpx.AsyncClient, raw_jobs: list[dict]
    ) -> list[dict]:
        """Completa cada doc adelgazado del listado v2 con su detalle.

        Si el detalle de UNA oferta falla (fetch_with_retry devuelve None
        tras sus reintentos), NO se tira el run: la oferta se emite con lo
        del listado — misma tolerancia a fallos parciales que el bucle de
        páginas (un `data` vacío corta la paginación pero conserva lo ya
        procesado). Un doc malformado (no-dict) se salta con log, y un `id`
        que no sea un ObjectId válido se trata como ausente.

        El hash (title|company|url) sale idéntico con o sin detalle, así una
        re-vista sigue refrescando `last_seen_at` en vez de caducar por un
        fallo transitorio. OJO: una re-vista sin detalle se emite DEGRADADA —
        description vacía, snippet None, tags [] (salen de
        extract_job_skills(title, description)), location "" — y es el upsert
        (JobRepository) quien conserva los valores buenos ya almacenados
        (description/snippet/tags/location/canton) y NO invalida el embedding,
        porque el contenido efectivo de la fila no cambia; aquí solo se
        garantiza la identidad. Residuales que SÍ quedan: `content_hash`
        versiona el payload entrante y oscila un run tras la degradación
        (cosmético, ver V2-2 en JobRepository.upsert_job), y un ALTA sin
        detalle queda sin `createdAt` — la ventana la contabiliza como
        `window_no_date`.
        """
        enriched: list[dict] = []
        for i, doc in enumerate(raw_jobs):
            # Un doc malformado NO tira el lote: se salta con log, la misma
            # política con la que _process_raw_jobs descarta un raw inválido.
            if not isinstance(doc, dict):
                logger.warning(
                    "thehub: doc de listado malformado (%s) — se salta",
                    type(doc).__name__,
                )
                continue
            job_id = _valid_job_id(doc.get("id"))
            detail = None
            if job_id:
                detail = await self._circuit.call(
                    lambda j=job_id: fetch_with_retry(
                        client, self.DETAIL_URL.format(job_id=j)
                    )
                )
            if isinstance(detail, dict):
                # El detalle manda; el doc del listado aporta lo que falte.
                enriched.append({**doc, **detail})
            else:
                logger.warning(
                    "thehub: sin detalle para la oferta %s — se emite con "
                    "los campos del listado",
                    job_id or "<sin id>",
                )
                enriched.append(doc)
            if i < len(raw_jobs) - 1:
                await asyncio.sleep(DETAIL_DELAY_SECONDS)
        return enriched

    @staticmethod
    def _safe_int(value: object) -> int:
        """Castea `pages`/`page` (a veces string, p.ej. \"4\") a int. 0 si no procede."""
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    def _logo_url(self, company_data: dict) -> str | None:
        """URL del logo desde el CDN imgix, o None si la oferta no trae logoImage."""
        logo_image = company_data.get("logoImage") or {}
        path = (logo_image.get("path") or "").strip()
        return f"{self.LOGO_BASE}{path}" if path else None

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw The Hub API response into the unified job schema."""
        title = (raw.get("title") or "").strip()

        company_data = raw.get("company") or {}
        company = (company_data.get("name") or "").strip()

        # La API v2 ya no expone absoluteJobUrl: la URL pública se construye
        # por `id` (por `key` da 404), validado como ObjectId — un id
        # inyectado no debe interpolarse en una URL persistida. Sin id válido
        # → url vacía → _process_raw_jobs descarta la oferta (sin URL no hay
        # identidad utilizable).
        job_id = _valid_job_id(raw.get("id"))
        url = self.PUBLIC_JOB_URL.format(job_id=job_id) if job_id else ""
        description = strip_html_tags(raw.get("description") or "")

        # location suele venir {} → toleramos ausencia (location vacío, canton None).
        location_data = raw.get("location") or {}
        location_str = (
            location_data.get("address") or location_data.get("locality") or ""
        ).strip()

        # remote es booleano ESTRUCTURAL (campo real), no heurística de título.
        is_remote = bool(raw.get("isRemote", False))

        # El salario de The Hub solo llega como texto libre ("competitive",
        # "unpaid") y los *Range son objetos vacíos → sin dato numérico fiable.
        return {
            "hash": self.compute_hash(title, company, url),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": location_str,
            "canton": extract_canton(location_str),
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url,
            "remote": is_remote,
            "tags": extract_job_skills(title, description)[: self.MAX_TAGS],
            "logo": self._logo_url(company_data),
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": None,
            "salary_currency": None,
            "salary_period": None,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": None,
            # Fecha del PORTAL: `createdAt` del detalle. Es la fecha de
            # publicación real — la propia página pública la embebe como
            # "Posted" con el MISMO valor y el portal ordena "New jobs" por
            # createdAt (verificado 2026-08-15, VD.9).
            "published_at": parse_published_at(raw.get("createdAt")),
        }
