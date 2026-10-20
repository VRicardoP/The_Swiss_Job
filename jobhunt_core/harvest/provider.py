"""Contratos del pipeline de ingesta (A-03): provider + sink.

El provider trae lo NUEVO según el cursor del scope; el sink persiste (A-04
implementa el real: listing + incarnación + revisión raw). El runner garantiza
que el cursor solo avanza si el sink completó sin error.
"""

import hashlib
import json
from abc import ABC, abstractmethod
from typing import ClassVar, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.harvest.types import FetchResult, RawListing

# Runner-owned, semantic scope policy; not an upstream provider parameter.
ADMISSION_WINDOW_PARAM = "admission_window_days"
LEGACY_TITLE_FILTER_PARAM = "legacy_title_filter"


class ProviderConfigError(ValueError):
    """Configuración del scope inválida (p.ej. hard_max_pages=0) — error
    PERMANENTE (rev. A-04 2ª #3): reintentar no lo arregla. El runner lo deja
    subir SIN contarlo como fallo de fuente y la tarea falla sin retry."""


class ProviderResponseError(RuntimeError):
    """La fuente respondió con una FORMA que no es la contractual (sobre no-objeto,
    colección de items que no es lista, `links` que no es objeto) — error
    TRANSITORIO de frontera.

    Distinto de `ProviderConfigError` (permanente, no cuenta como fallo de fuente):
    aquí el scope SÍ falla. El runner lo trata como cualquier fallo de fetch:
    rollback, `consecutive_failures + 1`, cursor y `last_complete_at` intactos.

    Motivo (auditoría externa 2026-08-27 P1-1): degradar un sobre inválido a
    "página vacía" lo hacía indistinguible del FINAL CONTRACTUAL del feed, y el
    runner confirmaba una cosecha completa que nunca ocurrió — corpus truncado,
    contador de fallos a cero y, pasado `CORE_CORPUS_STALE_DAYS`, archivado de
    vacantes todavía publicadas.
    """


# Statuses the retiring engine counted as a BLOCK, not as a generic failure
# (backend/services/scraper_engine.py:BLOCK_STATUS). 503 is in the list because
# the adapters already retry it: when it still surfaces, the portal has been
# answering "unavailable" for the whole retry window.
BLOCK_STATUSES = frozenset({403, 429, 503})

# Un desafío anti-bot llega como HTTP 200, así que no tiene estatus propio: se
# cuenta como el 403 que representa (DT-148 A8).
SOFT_BLOCK_STATUS = 403


class SoftBlocked(ProviderResponseError):
    """El portal respondió 200 con una página de DESAFÍO (CAPTCHA/challenge).

    Es un `ProviderResponseError` para que los adaptadores lo dejen salir sin
    reintentar —`_read` relanza esta familia antes de su rama de reintento—, y
    tiene estatus propio para que `blocking_status` lo cuente como bloqueo.

    Antes era un `ValueError`: `_read` lo clasificaba como reintentable, así
    que una página que nos estaba diciendo «para» recibía hasta cuatro
    peticiones con espera exponencial, y al agotarse salía como
    «request failed (ValueError)» — HTTP 200, ningún estatus, ningún bloqueo
    contabilizado (DT-148 A8).
    """


def blocking_status(exc: BaseException | None) -> int | None:
    """The blocking HTTP status behind a failure, or None.

    The chain is walked, not just the exception: adapters wrap the transport
    error in `ProviderResponseError`, and `raise … from None` only hides the
    cause from the traceback — `__context__` still carries it.
    """
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, SoftBlocked):
            # 200 con desafío: no hay estatus que leer, pero es un bloqueo.
            return SOFT_BLOCK_STATUS
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int) and status in BLOCK_STATUSES:
            return status
        exc = exc.__cause__ or exc.__context__
    return None


class BaseProvider(ABC):
    """Una fuente Tier 0/1 (API/feed). Sin estado: el cursor viene del scope."""

    name: str
    # Parámetros SEMÁNTICOS del scope (definen QUÉ se cosecha): si cambian con
    # cursor existente, el runner reinicia el cursor — un watermark heredado
    # enterraría ofertas que el filtro nuevo sí querría (rev. A-03 #3). Los
    # operativos (p.ej. max_pages) NO entran en el fingerprint.
    SEMANTIC_PARAMS: ClassVar[tuple[str, ...]] = ()

    @abstractmethod
    async def fetch_new(
        self, params: dict, cursor: dict | None, http: httpx.AsyncClient
    ) -> FetchResult:
        """Devuelve SOLO lo nuevo respecto al cursor, con el cursor siguiente."""

    def params_fingerprint(self, params: dict) -> str:
        """Hash canónico del subconjunto semántico de params."""
        semantic = {k: params.get(k) for k in self.SEMANTIC_PARAMS}
        for key in (ADMISSION_WINDOW_PARAM, LEGACY_TITLE_FILTER_PARAM):
            if key in params:
                semantic[key] = params[key]
        raw = json.dumps(semantic, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()


class ListingSink(Protocol):
    """Persistencia de un lote de listings DENTRO de la transacción del run.

    A-04 aporta la implementación real. Si lanza, el runner hace rollback y el
    cursor NO avanza (ningún listing se pierde entre fetch y persistencia).
    """

    async def handle(
        self, session: AsyncSession, scope_id: str, listings: tuple[RawListing, ...]
    ) -> None: ...
