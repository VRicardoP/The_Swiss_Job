"""Puerto de la capacidad COLEGIOS — A.SEAM (plan §15bis).

Subinterfaz POR CAPACIDAD (no fachada unica `JobHunting`). La operacion es
el LISTADO de colegios vigilados (watchlist_schools) que hoy consume
`routers/watchlist.py` (GET /api/v1/watchlist/schools): metadata publica de
`scrapers/swiss_schools_config.SCHOOLS` (config estatica versionada en el
repo — su "escritor" es el propio codigo del BFF). La resolucion
colegio<->oferta (resolve_school_from_job) es un servicio interno de otros
flujos (borrador, alertas), no una operacion de este puerto.

VARIANTE LIGERA de la costura: el /v1 del core NO expone colegios
(jobhunt_core/api/v1.py solo sirve vacancies/profiles/matches en Fase A) —
`CoreSchools` levanta SchoolsUnsupportedError. Es la cota del contrato
vigente, fijada por los contract tests (patron search/stats de catalogo).

CRITERIO UNIFICADOR (heredado de A.SEAM matching): el unico escritor del
estado es local => el listado se sirve de local en TODOS los modos, incluida
core_primary — nunca 501/503 por routing (services/schools/seam.py).

Dos implementaciones detras del mismo puerto:
- `LocalSchools` (services/schools/local.py): listado actual, verbatim.
- `CoreSchools` (services/schools/core_client.py): cota /v1.
La eleccion la decide `jobhunt_routing` (services/schools/seam.py).
"""

from typing import Protocol


class SchoolsError(Exception):
    """Base de errores de la capacidad colegios."""


class CoreUnavailableError(SchoolsError):
    """El core no responde, fallo o no hay credencial de consumer.

    Hoy SIN emisor (CoreSchools no emite red: cota Unsupported total). Se
    conserva por simetria con el resto de capacidades y para la separacion
    de severidades del canary (seam.FallbackSchools)."""


class SchoolsUnsupportedError(SchoolsError):
    """La operacion no existe (aun) en el contrato /v1 del core."""


class SchoolsPort(Protocol):
    """Operacion de LECTURA del listado de colegios vigilados."""

    async def list(self) -> dict:
        """Payload {"schools": [...]} con la metadata publica de cada
        colegio (el router lo devuelve tal cual)."""
        ...
