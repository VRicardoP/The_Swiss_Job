"""Dependencias de la API /v1 (A-09): sesión BD, autenticación y scopes.

Errores con la FORMA del contrato §2 — {code, message, details} — vía
ApiError (el handler global de main.py la aplica). Ownership multi-tenant:
cross-tenant SIEMPRE 404 (no revelar existencia), sin scope → 403, credencial
inválida por cualquier causa → 401 indistinguible.
"""

import dataclasses
import logging
import uuid

from fastapi import Depends, Request

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


async def get_principal(request: Request, session=Depends(get_session)) -> Principal:
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise error_401()
    result = await credentials.authenticate(session, token.strip())
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
