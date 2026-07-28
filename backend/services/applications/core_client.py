"""Implementacion CORE de la capacidad candidaturas — A.SEAM (plan §15bis).

Contrato REAL del /v1 (jobhunt_core/api/v1.py, Fase A): solo expone
GET /vacancies/{id}, GET /profiles/{id} y GET /profiles/{id}/matches. NO hay
endpoints de candidatura (las tablas existen en el esquema del core, sin
API): TODAS las operaciones del puerto levantan ApplicationsUnsupportedError.

Es la cota del contrato vigente, fijada por los contract tests (patron
search/stats de catalogo): esta clase no abre cliente HTTP ni necesita
credencial — CERO peticiones por construccion. Cuando el core publique sus
endpoints de candidatura, se implementan aqui sin tocar los routers.
"""

from .port import ApplicationsUnsupportedError

_UNSUPPORTED_MSG = "el /v1 del core no expone la capacidad de candidaturas"


class CoreApplications:
    """Cota /v1 detras del puerto ApplicationsPort (sin red, sin credencial)."""

    async def list(self, user_id, status=None, limit=50, offset=0):
        raise ApplicationsUnsupportedError(_UNSUPPORTED_MSG)

    async def create(self, user_id, job_hash, notes=None):
        raise ApplicationsUnsupportedError(_UNSUPPORTED_MSG)

    async def stats(self, user_id):
        raise ApplicationsUnsupportedError(_UNSUPPORTED_MSG)

    async def update(self, user_id, application_id, changes):
        raise ApplicationsUnsupportedError(_UNSUPPORTED_MSG)

    async def delete(self, user_id, application_id):
        raise ApplicationsUnsupportedError(_UNSUPPORTED_MSG)

    async def set_match_status(self, user_id, job_hash, application_status):
        raise ApplicationsUnsupportedError(_UNSUPPORTED_MSG)

    async def get_match(self, user_id, job_hash):
        raise ApplicationsUnsupportedError(_UNSUPPORTED_MSG)

    async def save_draft(self, user_id, job_hash, draft):
        raise ApplicationsUnsupportedError(_UNSUPPORTED_MSG)

    async def get_draft(self, user_id, job_hash):
        raise ApplicationsUnsupportedError(_UNSUPPORTED_MSG)
