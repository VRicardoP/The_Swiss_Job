"""Implementacion CORE de la capacidad colegios — A.SEAM (plan §15bis).

Contrato REAL del /v1 (jobhunt_core/api/v1.py, Fase A): solo expone
GET /vacancies/{id}, GET /profiles/{id} y GET /profiles/{id}/matches. NO hay
endpoint de colegios vigilados: la operacion del puerto levanta
SchoolsUnsupportedError.

Es la cota del contrato vigente, fijada por los contract tests (patron
search/stats de catalogo): esta clase no abre cliente HTTP ni necesita
credencial — CERO peticiones por construccion. Cuando el core publique su
endpoint de colegios, se implementa aqui sin tocar los routers.
"""

from .port import SchoolsUnsupportedError

_UNSUPPORTED_MSG = "el /v1 del core no expone la capacidad de colegios"


class CoreSchools:
    """Cota /v1 detras del puerto SchoolsPort (sin red, sin credencial)."""

    async def list(self):
        raise SchoolsUnsupportedError(_UNSUPPORTED_MSG)
