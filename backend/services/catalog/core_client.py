"""Implementacion CORE de la capacidad catalogo — A.SEAM (plan §15bis).

Cliente HTTP del /v1 del core. Contrato REAL: jobhunt_core/api/v1.py +
jobhunt_core/api/schemas.py (A-09; C-API-R + cierre de cota de filtros,
commit core 5e8e849):

- GET /v1/vacancies?limit&cursor&q&source&remote&country&city ->
  VacanciesPageDTO {items: [VacancyDTO], next_cursor}. Feed/busqueda del
  corpus GLOBAL (solo vacantes ACTIVAS), orden created_at DESC + keyset
  opaco; ETag de pagina (If-None-Match -> 304). Semantica de los filtros:
  `q` substring ci sobre title/company; `source` CSV ci contra la fuente del
  PRIMARY listing; `remote` igualdad del booleano canonico (NULL no casa);
  country/city substring ci sobre location.
- GET /v1/vacancies/{uuid} -> VacancyDTO {id, title, company, description,
  salary, tags, location, remote, primary_listing{source, external_id, url,
  apply_url, first_seen_at, last_seen_at}, listings[], translations[]}.
  404 con ErrorDTO si no existe.
- Auth por credencial de consumer: `Authorization: Bearer <key_id>.<secret>`
  (ADR-09), scope `vacancies:read`. 401/403 con ErrorDTO.
- El core NO expone estadisticas ni fuentes agregadas del catalogo: `stats`
  y `sources` levantan CatalogUnsupportedError (cota del contrato vigente,
  fijada por los contract tests).

BUSQUEDA (decisiones; las fija test_catalog_contract.py):

FILTROS EXPRESABLES. Al core solo viaja lo que su contrato modela: q (el
router legacy ya lo capa a 200, mismo limite del /v1), source (CSV ci; ver
EXPANSION) y remote_only=True -> remote=true (False significa "no filtrar",
nunca remote=false). canton/language/seniority/contract_type/salary_min/
salary_max y cualquier `sort` distinto de "newest" NO tienen equivalente en
el /v1 (cota fijada en el core: filtrar numeros contra salario en texto
libre seria inventarse el resultado) => CatalogUnsupportedError SIN emitir
peticiones — fallback a local en core_read, 501 en core_primary.

EXPANSION DE FUENTES. El proyector sombra B-02 registra las fuentes legacy
como `legacy:<source>` y el filtro `source` del /v1 compara contra esa
fuente del primary listing: cada fuente pedida se envia en AMBAS formas
("adzuna" -> "adzuna,legacy:adzuna") para casar vacantes core-nativas y
proyectadas. En la PRESENTACION el prefijo interno se retira (misma regla
que el cliente de matching: se muestra la fuente original, no la sombra).

PAGINACION/TOTAL (mismo criterio que services/matching/core_client.py). El
contrato legacy exige `total` exacto y pagina por offset; el /v1 pagina por
keyset sin recuento => se recorre el feed FILTRADO completo (paginas de
FEED_PAGE_LIMIT=100 — MAX_PAGE_LIMIT del /v1 —, cota MAX_FEED_PAGES contra
bucles de cursor) y se pagina localmente. El cache de paginas por ETag hace
barato el refresco (304 sin cuerpo).

IDENTIDAD. Los items del feed se presentan con la identidad de ESTA
capacidad: el UUID canonico de la vacante — la misma que sirve `get`, con
la que el detalle hace round-trip por la propia costura (el detalle por MD5
legacy lo sigue cubriendo el fallback local del canary). La resolucion a
hash MD5 via listings sombra es del feed de MATCHING, donde el estado del
escritor local se superpone por job_hash; el catalogo no superpone estado.

Traduccion de identidad en `get`: el legacy identifica por hash MD5 (32
hex); el core por UUID de vacante. OJO: un MD5 de 32 hex tambien PARSEA
como UUID sin guiones, asi que parsear no basta — `get()` solo trata como
identidad del core la forma canonica con guiones (round-trip
`str(UUID(ref)) == ref.lower()`). Cualquier otra referencia — MD5 legacy
incluido — devuelve None sin emitir ni una peticion al core.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Callable

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from config import settings
from schemas.job import JobBrief, JobResponse, JobSearchResponse

from .port import (
    CatalogSearchParams,
    CatalogUnsupportedError,
    CoreUnavailableError,
)

logger = logging.getLogger(__name__)

_UNSUPPORTED_MSG = "el /v1 del core no expone esta operacion de catalogo"

# Cotas del recorrido del feed (contrato /v1: MAX_PAGE_LIMIT=100 por pagina).
FEED_PAGE_LIMIT = 100
MAX_FEED_PAGES = 100  # 10k items; por encima => cursor en bucle o feed anomalo

# Limite de longitud de los Query params del /v1 (Query(max_length=200)).
_CORE_QUERY_MAX_LEN = 200

# Prefijo de las fuentes sombra del proyector B-02 (jobhunt_core/shadow).
_LEGACY_SOURCE_PREFIX = "legacy:"

# Cota de la columna legacy jobs.description_snippet (String(500)): el brief
# derivado del core respeta el mismo limite de presentacion.
_SNIPPET_MAX_LEN = 500

# Filtros de CatalogSearchParams sin equivalente en el /v1 (cota del core).
_UNSUPPORTED_FILTER_FIELDS = ("canton", "language", "seniority", "contract_type")

# Errores de FORMA de un 200 del core (JSON ilegible, esquema incompatible,
# tipos inesperados): se traducen a CoreUnavailableError — un payload invalido
# es tan inutilizable como el core caido, y asi core_read tiene fallback REAL
# en vez de un 500 (hallazgo P2 rev. externa A.SEAM).
_PAYLOAD_ERRORS = (
    ValueError,  # incluye json.JSONDecodeError de resp.json()
    KeyError,
    TypeError,
    AttributeError,
    IndexError,
    ValidationError,
)

# Cache de paginas del feed por ETag: clave (filtros, cursor) -> (etag, body).
# En proceso y acotada (mismo espiritu que la del feed de matching): en el
# refresco tipico el core responde 304 sin cuerpo y se reutiliza la pagina.
_ETAG_CACHE_MAX = 512
_etag_cache: dict[tuple[tuple, str], tuple[str, dict]] = {}


def clear_catalog_feed_cache() -> None:
    """Vacia la cache de paginas del feed de catalogo (tests / operacion)."""
    _etag_cache.clear()


class _VacanciesPageDTO(BaseModel):
    """DTO privado y ESTRECHO de la pagina del feed: valida SOLO los campos
    que este cliente consume (items lista, next_cursor str|None) — sin
    importar nada de jobhunt_core (frontera estricta, plan §21)."""

    model_config = ConfigDict(extra="ignore")

    items: list | None = None
    next_cursor: str | None = None


def default_client_factory() -> httpx.AsyncClient:
    """Cliente httpx contra el /v1 del core por la red interna de compose
    (plan §21: puerto dedicado, solo red interna, nunca ngrok)."""
    return httpx.AsyncClient(
        base_url=settings.CORE_API_BASE_URL,
        headers={"Authorization": f"Bearer {settings.CORE_CONSUMER_KEY}"},
        timeout=settings.CORE_HTTP_TIMEOUT_SECONDS,
    )


def _strip_legacy_source(source: str | None) -> str | None:
    """Fuente presentable: sin el prefijo interno `legacy:` de la sombra."""
    if source and source.startswith(_LEGACY_SOURCE_PREFIX):
        return source[len(_LEGACY_SOURCE_PREFIX) :] or None
    return source


def _primary_and_url(vacancy: dict) -> tuple[dict, str]:
    """Primary listing (o {}) y URL presentable de un VacancyDTO."""
    primary = vacancy.get("primary_listing") or {}
    listings = vacancy.get("listings") or []
    url = primary.get("url") or (listings[0]["url"] if listings else "")
    return primary, url


def vacancy_to_job_response(vacancy: dict) -> JobResponse:
    """Mapea VacancyDTO del core a la forma legacy JobResponse.

    Solo se mapean los campos que el contrato core expone; los enriquecidos
    del legacy (canton, seniority, salario normalizado CHF...) quedan None —
    los contract tests afirman equivalencia SOLO donde el contrato lo exige.
    """
    primary, url = _primary_and_url(vacancy)
    # Vacante activa sin primary listing (borde del contrato): se sirve con
    # timestamps "vista ahora" en lugar de inventar historia.
    now = datetime.now(timezone.utc)
    return JobResponse(
        hash=str(vacancy["id"]),  # identidad del core (UUID), no MD5 legacy
        source=_strip_legacy_source(primary.get("source")) or "core",
        title=vacancy.get("title") or "",
        company=vacancy.get("company") or "",
        url=url,
        description=vacancy.get("description"),
        location=vacancy.get("location"),
        remote=bool(vacancy.get("remote")),
        tags=vacancy.get("tags") or [],
        salary_original=vacancy.get("salary"),  # texto libre del core
        first_seen_at=primary.get("first_seen_at") or now,
        last_seen_at=primary.get("last_seen_at") or now,
        is_active=True,  # el core solo sirve vacantes ACTIVAS (contrato §2)
    )


def vacancy_to_job_brief(vacancy: dict) -> JobBrief:
    """Mapea VacancyDTO del core al item de busqueda legacy JobBrief.

    Misma regla que el detalle: solo lo que el contrato expone; los campos
    enriquecidos del legacy (canton, salario CHF, language, seniority,
    contract_type, logo) quedan en su default None.
    """
    primary, url = _primary_and_url(vacancy)
    description = vacancy.get("description")
    now = datetime.now(timezone.utc)
    return JobBrief(
        hash=str(vacancy["id"]),  # identidad del core (UUID), como en get()
        title=vacancy.get("title") or "",
        company=vacancy.get("company") or "",
        location=vacancy.get("location"),
        description_snippet=description[:_SNIPPET_MAX_LEN] if description else None,
        url=url,
        remote=bool(vacancy.get("remote")),
        tags=vacancy.get("tags") or [],
        source=_strip_legacy_source(primary.get("source")) or "core",
        first_seen_at=primary.get("first_seen_at") or now,
        is_active=True,  # el core solo sirve vacantes ACTIVAS (contrato §2)
    )


def expand_sources_csv(csv: str) -> str:
    """CSV de fuentes con cada token en sus DOS formas: original y sombra
    (`legacy:<source>`), deduplicado preservando el orden (ver EXPANSION DE
    FUENTES en el docstring del modulo)."""
    out: list[str] = []
    for token in csv.split(","):
        t = token.strip()
        if not t or t in out:
            continue
        out.append(t)
        if not t.startswith(_LEGACY_SOURCE_PREFIX):
            shadow = f"{_LEGACY_SOURCE_PREFIX}{t}"
            if shadow not in out:
                out.append(shadow)
    return ",".join(out)


def unsupported_search_reason(params: CatalogSearchParams) -> str | None:
    """Motivo por el que el /v1 NO puede servir esta busqueda, o None.

    Cotas del contrato vigente (docstring del modulo): orden distinto de
    'newest', filtros sin columna en el content del core, y un CSV de
    fuentes expandido que excede el max_length del Query del /v1.
    """
    if params.sort != "newest":
        return f"orden {params.sort!r} sin equivalente en el feed /v1 (solo 'newest')"
    rejected = [f for f in _UNSUPPORTED_FILTER_FIELDS if getattr(params, f)]
    if params.salary_min is not None or params.salary_max is not None:
        rejected.append("salary")
    if rejected:
        return "filtros sin equivalente en el /v1 del core: " + ", ".join(rejected)
    if params.source and len(expand_sources_csv(params.source)) > _CORE_QUERY_MAX_LEN:
        return "filtro source expandido excede el limite de 200 del /v1"
    return None


def _feed_query(params: CatalogSearchParams) -> dict:
    """Query params del GET /v1/vacancies para una busqueda expresable."""
    query: dict = {"limit": FEED_PAGE_LIMIT}
    if params.q:
        query["q"] = params.q
    if params.source:
        query["source"] = expand_sources_csv(params.source)
    if params.remote_only:
        # remote_only=False significa "no filtrar" (nunca remote=false).
        query["remote"] = "true"
    return query


async def _request_page(
    client: httpx.AsyncClient, params: dict, headers: dict
) -> httpx.Response:
    """GET de una pagina del feed; solo 200/304 son respuestas utilizables."""
    try:
        resp = await client.get("/vacancies", params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise CoreUnavailableError(f"core /v1 inaccesible: {exc}") from exc
    if resp.status_code not in (200, 304):
        # 401/403/422/5xx: sin datos utilizables del core.
        raise CoreUnavailableError(
            f"core /v1 devolvio {resp.status_code} para el feed de catalogo"
        )
    return resp


def _parse_page_body(resp: httpx.Response) -> dict:
    """Cuerpo JSON-objeto de un 200 del feed, o CoreUnavailableError."""
    try:
        body = resp.json()
    except ValueError as exc:  # JSON ilegible en un 200
        raise CoreUnavailableError(
            f"JSON invalido del core en el feed de catalogo: {exc}"
        ) from exc
    if not isinstance(body, dict):
        # VacanciesPageDTO es un objeto: otra forma es incompatible.
        raise CoreUnavailableError("payload no-objeto del core en el feed de catalogo")
    return body


def _cache_page(cache_key: tuple, resp: httpx.Response, body: dict) -> None:
    """Guarda la pagina por ETag (cache acotada; se rellena con 200s)."""
    etag = resp.headers.get("etag")
    if not etag:
        return
    if len(_etag_cache) >= _ETAG_CACHE_MAX:
        _etag_cache.clear()
    _etag_cache[cache_key] = (etag, body)


class CoreCatalog:
    """Cliente /v1 del core detras del puerto CatalogPort."""

    def __init__(self, client_factory: Callable[[], httpx.AsyncClient] | None = None):
        # Inyectable para tests (MockTransport); en produccion, el factory
        # por defecto con la credencial de consumer de settings.
        self._client_factory = client_factory or default_client_factory

    def _guard_credential(self) -> None:
        """Sin credencial no se hace ni una peticion (mismo trato que caida)."""
        if (
            self._client_factory is default_client_factory
            and not settings.CORE_CONSUMER_KEY
        ):
            raise CoreUnavailableError("CORE_CONSUMER_KEY no configurada")

    async def get(self, job_ref: str):
        try:
            vacancy_id = uuid.UUID(job_ref)
        except ValueError:
            return None  # ni siquiera parsea como UUID: no existe en el core
        if str(vacancy_id) != job_ref.lower():
            # Un MD5 legacy (32 hex) parsea como UUID sin guiones: solo la
            # forma canonica con guiones es identidad del core. Cortocircuito
            # sin red (evita el GET inutil por vista de detalle y colgarse
            # CORE_HTTP_TIMEOUT_SECONDS con el core caido en core_read).
            return None
        self._guard_credential()
        try:
            async with self._client_factory() as client:
                resp = await client.get(f"/vacancies/{vacancy_id}")
        except httpx.HTTPError as exc:
            raise CoreUnavailableError(f"core /v1 inaccesible: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            # 401/403/5xx: sin datos utilizables del core.
            raise CoreUnavailableError(
                f"core /v1 devolvio {resp.status_code} para {vacancy_id}"
            )
        try:
            return vacancy_to_job_response(resp.json())
        except _PAYLOAD_ERRORS as exc:
            # 200 con payload invalido/incompatible = tan inutilizable como
            # una caida => fallback real en core_read (P2 rev. externa).
            raise CoreUnavailableError(
                f"payload invalido del core para {vacancy_id}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    async def search(self, params: CatalogSearchParams) -> JobSearchResponse:
        reason = unsupported_search_reason(params)
        if reason is not None:
            # Cota del contrato, no fallo: fallback a local en core_read,
            # 501 en core_primary — y CERO peticiones al core.
            raise CatalogUnsupportedError(reason)
        self._guard_credential()
        vacancies = await self._fetch_filtered_feed(_feed_query(params))
        try:
            briefs = [vacancy_to_job_brief(v) for v in vacancies]
        except _PAYLOAD_ERRORS as exc:
            raise CoreUnavailableError(
                f"payload invalido del feed de catalogo del core: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        total = len(briefs)
        return JobSearchResponse(
            data=briefs[params.offset : params.offset + params.limit],
            total=total,
            limit=params.limit,
            offset=params.offset,
            has_more=(params.offset + params.limit) < total,
        )

    async def stats(self):
        raise CatalogUnsupportedError(_UNSUPPORTED_MSG)

    async def sources(self):
        raise CatalogUnsupportedError(_UNSUPPORTED_MSG)

    # ------------------------------------------------------------------ feed

    async def _fetch_filtered_feed(self, query: dict) -> list[dict]:
        """Recorre el feed filtrado completo por keyset (PAGINACION/TOTAL);
        cache de paginas por ETag."""
        items: list[dict] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        async with self._client_factory() as client:
            for _ in range(MAX_FEED_PAGES):
                page = await self._fetch_page(client, query, cursor)
                # Validacion de FORMA con el DTO privado ANTES de consumir la
                # pagina (P2): tipos rotos => CoreUnavailableError, nunca un
                # TypeError/ValidationError fuera del fallback.
                try:
                    page_dto = _VacanciesPageDTO.model_validate(page)
                except _PAYLOAD_ERRORS as exc:
                    raise CoreUnavailableError(
                        f"payload invalido del feed de catalogo del core: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                items.extend(page_dto.items or [])
                cursor = page_dto.next_cursor
                if cursor is None:
                    return items
                if cursor in seen_cursors:
                    raise CoreUnavailableError(
                        f"feed de catalogo del core con cursor repetido: {cursor[:64]}"
                    )
                seen_cursors.add(cursor)
        raise CoreUnavailableError(
            f"feed de catalogo del core excede {MAX_FEED_PAGES} paginas "
            "(cota anti-bucle)"
        )

    async def _fetch_page(
        self, client: httpx.AsyncClient, query: dict, cursor: str | None
    ) -> dict:
        cache_key = (tuple(sorted(query.items())), cursor or "")
        cached = _etag_cache.get(cache_key)
        headers = {"If-None-Match": cached[0]} if cached else {}
        params = dict(query)
        if cursor is not None:
            params["cursor"] = cursor
        resp = await _request_page(client, params, headers)
        if resp.status_code == 304:
            if cached is None:  # defensivo: 304 sin haber mandado If-None-Match
                raise CoreUnavailableError("core /v1 devolvio 304 sin cache previa")
            return cached[1]
        body = _parse_page_body(resp)
        _cache_page(cache_key, resp, body)
        return body
