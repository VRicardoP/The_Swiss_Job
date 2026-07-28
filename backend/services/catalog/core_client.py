"""Implementacion CORE de la capacidad catalogo — A.SEAM (plan §15bis).

Cliente HTTP del /v1 del core. Contrato REAL: jobhunt_core/api/v1.py +
jobhunt_core/api/schemas.py (A-09):

- GET /v1/vacancies/{uuid} -> VacancyDTO {id, title, company, description,
  salary, tags, location, remote, primary_listing{source, external_id, url,
  apply_url, first_seen_at, last_seen_at}, listings[], translations[]}.
  Corpus GLOBAL, solo vacantes ACTIVAS; 404 con ErrorDTO si no existe.
- Auth por credencial de consumer: `Authorization: Bearer <key_id>.<secret>`
  (ADR-09), scope `vacancies:read`. 401/403 con ErrorDTO.
- El core NO expone (aun) busqueda/estadisticas/fuentes de catalogo: esas
  operaciones levantan CatalogUnsupportedError. Es la cota del contrato
  vigente y esta fijada por los contract tests; cuando el core publique su
  endpoint de busqueda, se implementa aqui sin tocar los routers.

Traduccion de identidad: el legacy identifica por hash MD5 (32 hex); el core
por UUID de vacante. OJO: un MD5 de 32 hex tambien PARSEA como UUID sin
guiones, asi que parsear no basta — `get()` solo trata como identidad del
core la forma canonica con guiones (round-trip `str(UUID(ref)) == ref.lower()`).
Cualquier otra referencia — MD5 legacy incluido — devuelve None sin emitir
ni una peticion al core.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Callable

import httpx

from config import settings
from schemas.job import JobResponse

from .port import (
    CatalogSearchParams,
    CatalogUnsupportedError,
    CoreUnavailableError,
)

logger = logging.getLogger(__name__)

_UNSUPPORTED_MSG = "el /v1 del core no expone esta operacion de catalogo"


def default_client_factory() -> httpx.AsyncClient:
    """Cliente httpx contra el /v1 del core por la red interna de compose
    (plan §21: puerto dedicado, solo red interna, nunca ngrok)."""
    return httpx.AsyncClient(
        base_url=settings.CORE_API_BASE_URL,
        headers={"Authorization": f"Bearer {settings.CORE_CONSUMER_KEY}"},
        timeout=settings.CORE_HTTP_TIMEOUT_SECONDS,
    )


def vacancy_to_job_response(vacancy: dict) -> JobResponse:
    """Mapea VacancyDTO del core a la forma legacy JobResponse.

    Solo se mapean los campos que el contrato core expone; los enriquecidos
    del legacy (canton, seniority, salario normalizado CHF...) quedan None —
    los contract tests afirman equivalencia SOLO donde el contrato lo exige.
    """
    primary = vacancy.get("primary_listing") or {}
    listings = vacancy.get("listings") or []
    url = primary.get("url") or (listings[0]["url"] if listings else "")
    # Vacante activa sin primary listing (borde del contrato): se sirve con
    # timestamps "vista ahora" en lugar de inventar historia.
    now = datetime.now(timezone.utc)
    return JobResponse(
        hash=str(vacancy["id"]),  # identidad del core (UUID), no MD5 legacy
        source=primary.get("source") or "core",
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


class CoreCatalog:
    """Cliente /v1 del core detras del puerto CatalogPort."""

    def __init__(self, client_factory: Callable[[], httpx.AsyncClient] | None = None):
        # Inyectable para tests (MockTransport); en produccion, el factory
        # por defecto con la credencial de consumer de settings.
        self._client_factory = client_factory or default_client_factory

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
        if (
            self._client_factory is default_client_factory
            and not settings.CORE_CONSUMER_KEY
        ):
            # Sin credencial no se hace ni una peticion (mismo trato que caida).
            raise CoreUnavailableError("CORE_CONSUMER_KEY no configurada")
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
        return vacancy_to_job_response(resp.json())

    async def search(self, params: CatalogSearchParams):
        raise CatalogUnsupportedError(_UNSUPPORTED_MSG)

    async def stats(self):
        raise CatalogUnsupportedError(_UNSUPPORTED_MSG)

    async def sources(self):
        raise CatalogUnsupportedError(_UNSUPPORTED_MSG)
