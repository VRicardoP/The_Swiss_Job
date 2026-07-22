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
