"""Dependencias de la API /v1 (A-09): sesión BD, autenticación y scopes.

Errores con la FORMA del contrato §2 — {code, message, details} — vía
ApiError (el handler global de main.py la aplica). Ownership multi-tenant:
cross-tenant SIEMPRE 404 (no revelar existencia), sin scope → 403, credencial
inválida por cualquier causa → 401 indistinguible.
"""

import dataclasses
import logging
import uuid

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from jobhunt_core import credentials
from jobhunt_core.database import SessionLocal

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Error de API con la forma del contrato (§2)."""

    def __init__(self, status_code: int, code: str, message: str, details=None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def error_401():
    return ApiError(401, "unauthorized", "credencial ausente o inválida")


def error_403(scope: str):
    return ApiError(403, "forbidden", "scope insuficiente", {"required_scope": scope})


def error_404(resource: str):
    return ApiError(404, "not_found", f"{resource} no encontrado")


@dataclasses.dataclass(frozen=True)
class Principal:
    consumer_id: uuid.UUID
    scopes: tuple[str, ...]


async def get_session():
    """Sesión BD por request. La API es un proceso uvicorn de UN loop: el
    engine global es correcto aquí (el problema multi-loop era de Celery)."""
    async with SessionLocal() as session:
        yield session


# HTTPBearer como dependencia FORMAL (rev. A-09 #6): OpenAPI declara el
# securityScheme; auto_error=False para conservar NUESTRO 401 del contrato.
_bearer = HTTPBearer(auto_error=False)


async def get_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session=Depends(get_session),
) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials.strip():
        raise error_401()
    result = await credentials.authenticate(session, creds.credentials.strip())
    if result is None:
        raise error_401()
    consumer_id, scopes = result
    return Principal(consumer_id=consumer_id, scopes=tuple(scopes))


def require_scope(scope: str):
    """Matriz ruta→scope (§2): sin el scope → 403."""

    async def dep(principal: Principal = Depends(get_principal)) -> Principal:
        if scope not in principal.scopes:
            raise error_403(scope)
        return principal

    return dep
