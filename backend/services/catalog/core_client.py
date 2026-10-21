"""HTTP adapter for the catalog owned by Core.

It validates the Core payload, preserves stable vacancy identities and maps the
filters supported by the public contract. Unsupported filters fail explicitly.
Legacy 32-character links resolve through Core listing references.
"""

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from schemas.job import JobBrief, JobResponse, JobSearchResponse

from .port import (
    CatalogSearchParams,
    CatalogUnsupportedError,
    CoreUnavailableError,
)

logger = logging.getLogger(__name__)

# Limite de longitud de los Query params del /v1 (Query(max_length=200)).
_CORE_QUERY_MAX_LEN = 200

# Prefijo de las fuentes sombra del proyector B-02 (jobhunt_core/shadow).
_LEGACY_SOURCE_PREFIX = "legacy:"

# Cota de la columna legacy jobs.description_snippet (String(500)): el brief
# derivado del core respeta el mismo limite de presentacion.
_SNIPPET_MAX_LEN = 500

# Filtros de CatalogSearchParams sin equivalente en el /v1 (cota del core).
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

# Cache de paginas del feed por ETag: clave (filtros, cursor) ->
# (etag, body, comprobada_en). En proceso y acotada (mismo espiritu que la del
# feed de matching): durante 60 s se sirve la pagina ya comprobada; despues se
# revalida y el core puede responder 304 sin cuerpo.
_ETAG_CACHE_MAX = 512
_PAGE_FRESH_SECONDS = 60
_etag_cache: dict[tuple, tuple[str, dict, float]] = {}

# Recorrido por cursor de `recent` (avisos por correo). El tope de paginas es
# una proteccion contra un cursor que no avanza, no el presupuesto normal: con
# `limit` por defecto el recorrido para mucho antes.
_RECENT_PAGE_LIMIT = 100
_RECENT_MAX_PAGES = 20


def clear_catalog_feed_cache() -> None:
    """Vacia la cache de paginas del feed de catalogo (tests / operacion)."""
    _etag_cache.clear()


class _VacanciesPageDTO(BaseModel):
    """DTO privado y ESTRECHO de la pagina del feed: valida SOLO los campos
    que este cliente consume (items lista, next_cursor str|None) — sin
    importar nada de jobhunt_core (frontera estricta, plan §21)."""

    model_config = ConfigDict(extra="ignore")

    items: list
    next_cursor: str | None = None
    total: int = Field(ge=0, strict=True)


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

    DT-148 A18-01: quedaba UNA cota real. Las otras dos —orden distinto de
    'newest' y los seis filtros sin columna en el content— ya no pueden darse
    porque la interfaz dejó de ofrecerlos: un 501 que ningun cliente puede
    provocar no protege nada, sólo documenta un camino muerto.
    """
    if params.source and len(expand_sources_csv(params.source)) > _CORE_QUERY_MAX_LEN:
        return "filtro source expandido excede el limite de 200 del /v1"
    return None


def _feed_query(params: CatalogSearchParams) -> dict:
    """Query params del GET /v1/vacancies para una busqueda expresable."""
    query: dict = {"limit": params.limit, "offset": params.offset}
    if params.q:
        query["q"] = params.q
    if params.source:
        query["source"] = expand_sources_csv(params.source)
    if params.remote_only:
        # remote_only=False significa "no filtrar" (nunca remote=false).
        query["remote"] = "true"
    if params.location:
        # El /v1 filtra `city` como substring ci sobre `location`, que el
        # content lleva SIEMPRE. Se envía como `city` porque es el nombre del
        # parámetro, no porque el dato sea una ciudad: es texto libre.
        query["city"] = params.location
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
    _etag_cache[cache_key] = (etag, body, time.monotonic())


async def _recent_page(client: httpx.AsyncClient, query: dict):
    """Una pagina del recorrido de `recent`: (ofertas, cursor siguiente).

    Un 200 con forma incompatible es tan inutilizable como el core caido, asi
    que se traduce a `CoreUnavailableError` como en el resto del cliente.
    """
    body = _parse_page_body(await _request_page(client, query, {}))
    try:
        items = body["items"]
        if not isinstance(items, list):
            raise TypeError("items del core no es una lista")
        return [vacancy_to_job_response(v) for v in items], body.get("next_cursor")
    except _PAYLOAD_ERRORS as exc:
        raise CoreUnavailableError(
            f"payload invalido del catalogo del core: {type(exc).__name__}: {exc}"
        ) from exc


class CoreCatalog:
    """Cliente /v1 del core detras del puerto CatalogPort."""

    def __init__(
        self,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        db: AsyncSession | None = None,
    ):
        # Inyectable para tests (MockTransport); en produccion, el factory
        # por defecto con la credencial de consumer de settings.
        self._client_factory = client_factory or default_client_factory
        # G1/P2-17 — identidad accionable: la sesion local (opcional, la
        # inyecta resolve_catalog) permite presentar el hash MD5 legacy en
        # vez del UUID del core cuando existe Job local de respaldo (por su
        # `url` UNIQUE, la misma resolucion determinista que matching). Sin
        # ella, TODA accion sobre el feed (POST
        # /documents/generate: `job_hash` max_length=32 + `Job.hash ==`)
        # moria con 422/404.
        self._db = db

    async def _get_local_backed(self, job_ref: str):
        """Detalle de una identidad MD5 legacy presentada por este cliente.

        G1/P2-17: en core_primary no hay FallbackCatalog — sin esto, el
        detalle de un item del feed (que ahora viaja con MD5 accionable)
        moriria en 404. Sin sesion local se conserva el comportamiento
        previo (None)."""
        if len(job_ref) != 32 or any(
            c not in "0123456789abcdefABCDEF" for c in job_ref
        ):
            return None
        self._guard_credential()
        try:
            async with self._client_factory() as client:
                response = await client.get(
                    "/listing-references", params={"external_id": job_ref}
                )
        except httpx.HTTPError as exc:
            raise CoreUnavailableError("core reference lookup unavailable") from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise CoreUnavailableError("core reference lookup failed or ambiguous")
        try:
            vacancy_id = str(uuid.UUID(response.json()["vacancy_id"]))
        except _PAYLOAD_ERRORS as exc:
            raise CoreUnavailableError("invalid core reference response") from exc
        return await self.get(vacancy_id)

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
            # Ni siquiera parsea como UUID: no existe en el core. Con sesion
            # local puede ser un MD5 presentado por este mismo cliente (P2-17).
            return await self._get_local_backed(job_ref)
        if str(vacancy_id) != job_ref.lower():
            # Un MD5 legacy (32 hex) parsea como UUID sin guiones: solo la
            # forma canonica con guiones es identidad del core. Cortocircuito
            # sin red (evita el GET inutil por vista de detalle y colgarse
            # CORE_HTTP_TIMEOUT_SECONDS con el core caido en core_read).
            # G1/P2-17: el feed de ESTE cliente presenta MD5 para los items
            # con respaldo local — su detalle se sirve del Job local.
            return await self._get_local_backed(job_ref)
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
            job = vacancy_to_job_response(resp.json())
        except _PAYLOAD_ERRORS as exc:
            # 200 con payload invalido/incompatible = tan inutilizable como
            # una caida => fallback real en core_read (P2 rev. externa).
            raise CoreUnavailableError(
                f"payload invalido del core para {vacancy_id}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        # DT-148 B3: aquí se resolvía el hash MD5 legacy de la oferta contra
        # un Job local de respaldo (G1/P2-17). Tras el corte de DT-144 el
        # resolutor devolvía `{}` siempre: la identidad servida es la del
        # núcleo y las escrituras actúan con ella.
        return job

    async def search(self, params: CatalogSearchParams) -> JobSearchResponse:
        reason = unsupported_search_reason(params)
        if reason is not None:
            # Cota del contrato, no fallo: fallback a local en core_read,
            # 501 en core_primary — y CERO peticiones al core.
            raise CatalogUnsupportedError(reason)
        self._guard_credential()
        async with self._client_factory() as client:
            body = await self._fetch_page(client, _feed_query(params))
        try:
            page = _VacanciesPageDTO.model_validate(body)
            if len(page.items) > min(params.limit, max(page.total - params.offset, 0)):
                raise ValueError("pagina incompatible con limit/offset/total")
            briefs = [vacancy_to_job_brief(v) for v in page.items]
        except _PAYLOAD_ERRORS as exc:
            raise CoreUnavailableError(
                f"payload invalido del feed de catalogo del core: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        # DT-148 B3: ídem en lote. El resolutor legacy ya no existe.
        total = page.total
        return JobSearchResponse(
            data=briefs,
            total=total,
            limit=params.limit,
            offset=params.offset,
            has_more=(params.offset + params.limit) < total,
        )

    async def recent(self, *, category: str, since, limit: int = 500):
        """Ofertas ACTIVAS de una categoria dadas de alta desde `since`.

        Pagina por CURSOR (keyset), no por offset: aqui no hace falta el total
        y el keyset es el camino indexado del feed. El feed del core sirve solo
        vacantes activas y presentables, que es exactamente lo que la consulta
        legacy pedia con `is_active AND duplicate_of IS NULL`.

        COTA declarada: se devuelven como maximo `limit` ofertas, y si el
        recorrido llega a ese tope se GRITA en el log — para un aviso, perder
        la cola en silencio seria peor que avisar de menos.
        """
        self._guard_credential()
        jobs: list[JobResponse] = []
        vistos: set[str] = set()
        cursor = None
        async with self._client_factory() as client:
            for _ in range(_RECENT_MAX_PAGES):
                query = {
                    "category": category,
                    "since": since.isoformat(),
                    "limit": min(_RECENT_PAGE_LIMIT, max(1, limit - len(jobs))),
                }
                if cursor is not None:
                    query["cursor"] = cursor
                pagina, cursor = await _recent_page(client, query)
                jobs.extend(pagina)
                if cursor is None:
                    return jobs
                if len(jobs) >= limit:
                    logger.error(
                        "catalogo: la categoria %r trae mas de %d ofertas desde %s; "
                        "el aviso se queda en el tope",
                        category,
                        limit,
                        since,
                    )
                    return jobs[:limit]
                if not isinstance(cursor, str) or not cursor or cursor in vistos:
                    raise CoreUnavailableError("cursor del core sin progreso")
                vistos.add(cursor)
        raise CoreUnavailableError(
            "el catalogo del core no agoto la categoria en el tope de paginas"
        )

    # ------------------------------------------------------------------ feed

    async def _fetch_page(self, client: httpx.AsyncClient, query: dict) -> dict:
        cache_key = tuple(sorted(query.items()))
        cached = _etag_cache.get(cache_key)
        if cached and time.monotonic() - cached[2] <= _PAGE_FRESH_SECONDS:
            return cached[1]
        headers = {"If-None-Match": cached[0]} if cached else {}
        resp = await _request_page(client, query, headers)
        if resp.status_code == 304:
            if cached is None:  # defensivo: 304 sin haber mandado If-None-Match
                raise CoreUnavailableError("core /v1 devolvio 304 sin cache previa")
            _etag_cache[cache_key] = (cached[0], cached[1], time.monotonic())
            return cached[1]
        body = _parse_page_body(resp)
        _cache_page(cache_key, resp, body)
        return body
