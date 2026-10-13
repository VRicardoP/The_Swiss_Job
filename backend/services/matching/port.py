"""Read port for the matching feed owned by Core.

CoreMatching supplies results, facets and saved items. Retained local
MatchResult state is overlaid only where the feedback and application gates
still require it. The local matching engine is no longer part of this port.
"""

import uuid
from typing import Protocol


class MatchingError(Exception):
    """Base de errores de la capacidad matching."""


class CoreUnavailableError(MatchingError):
    """El core no responde, fallo, o falta credencial/mapeo de identidad."""


class MatchingUnsupportedError(MatchingError):
    """La operacion no puede servirla el core con el contrato vigente."""


class MatchingPort(Protocol):
    """Operaciones de LECTURA del feed de matching de un usuario.

    Devuelven `(items, total)` con items en la forma que consume el router
    legacy: dicts `{"match": ..., "job": ...}` cuyos objetos exponen los
    atributos de MatchResult/Job usados por `_to_match_response`.
    """

    async def results(
        self, user_id: uuid.UUID, limit: int = 20, offset: int = 0
    ) -> tuple[list[dict], int]:
        """Feed vigente: orden score_final DESC, sin feedback negativo,
        solo vacantes activas."""
        ...

    async def saved(
        self, user_id: uuid.UUID, limit: int = 100, offset: int = 0
    ) -> tuple[list[dict], int]:
        """Marcados positivos (thumbs_up/applied), orden score_final DESC.

        Proyeccion PURA del estado del escritor LOCAL: hasta Fase C se sirve
        de local en TODOS los modos (criterio unificador, docstring de
        services/matching/seam.py)."""
        ...
