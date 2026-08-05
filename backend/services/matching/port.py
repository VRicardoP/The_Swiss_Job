"""Puerto de la capacidad MATCHING — A.SEAM (plan §15bis).

Subinterfaz POR CAPACIDAD (no fachada unica `JobHunting`). Las operaciones
son las LECTURAS DEL FEED que hoy consumen los routers de match
(`routers/match.py`): `results` (feed ordenado por score; /results y
/history la comparten hoy en el legacy — la costura conserva esa igualdad)
y `saved` (marcados positivos).

ESCRITURAS FUERA DEL PUERTO: el puerto sigue siendo de LECTURA. El feedback
explicito/implicito queda SIEMPRE en local en esta etapa. El disparo del
pipeline (/analyze) dejo de ser "siempre local" en Fase D (gate
anti-doble-motor D.2, routers/match.py): con el matching gobernado por el
core responde 409 y el motor local NO se ejecuta — y NO entra al puerto
porque en ningun modo se dispara el pipeline del core desde aqui (el core
re-evalua solo al cambiar el CV o el corpus).
El state machine de candidatura (watchlist:
status/draft/calendar) tiene desde A.SEAM candidaturas su propia costura
(services/applications) — sigue sirviendose SIEMPRE de local en todos los
modos (criterio unificador: su unico escritor es local). Matriz de escritor del plan §15bis: el legacy es el
escritor autoritativo de ese estado hasta el cutover; el cambio de escritor
llega en Fase C como escritura sincrona contra el escritor activo +
idempotency key (el outbox replica eventos, no convierte comandos en
asincronos). Consecuencia LEIDA en esta etapa: el estado escrito localmente
(feedback/status/borradores) se SUPERPONE a las lecturas del feed servidas
por el core (overlay en CoreMatching) — leerlo del core seria leer a un
no-escritor.

Dos implementaciones detras del mismo puerto:
- `LocalMatching` (services/matching/local.py): motor actual, sin cambios.
- `CoreMatching` (services/matching/core_client.py): cliente del feed
  /v1/profiles/{id}/matches del core.
La eleccion la decide `jobhunt_routing` (services/matching/seam.py).
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
