"""Contratos del pipeline de ingesta (A-03): provider + sink.

El provider trae lo NUEVO según el cursor del scope; el sink persiste (A-04
implementa el real: listing + incarnación + revisión raw). El runner garantiza
que el cursor solo avanza si el sink completó sin error.
"""

from abc import ABC, abstractmethod
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.harvest.types import FetchResult, RawListing


class BaseProvider(ABC):
    """Una fuente Tier 0/1 (API/feed). Sin estado: el cursor viene del scope."""

    name: str

    @abstractmethod
    async def fetch_new(
        self, params: dict, cursor: dict | None, http: httpx.AsyncClient
    ) -> FetchResult:
        """Devuelve SOLO lo nuevo respecto al cursor, con el cursor siguiente."""


class ListingSink(Protocol):
    """Persistencia de un lote de listings DENTRO de la transacción del run.

    A-04 aporta la implementación real. Si lanza, el runner hace rollback y el
    cursor NO avanza (ningún listing se pierde entre fetch y persistencia).
    """

    async def handle(
        self, session: AsyncSession, scope_id: str, listings: tuple[RawListing, ...]
    ) -> None: ...
