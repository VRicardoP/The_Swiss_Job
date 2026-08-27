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
