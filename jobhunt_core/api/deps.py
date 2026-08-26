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


def ensure_json_storable(payload, path: str = "body") -> None:
    """400 en FRONTERA para el cuerpo que Postgres NO puede almacenar.

    G7-P3-1 — la regla que faltaba en el camino VIVO. El decodificador de
    cuerpo de Starlette es el `json.loads` de la stdlib, que acepta los
    literales NO estándar `NaN`/`Infinity` y el escape `\\u0000`; los DTO los
    dejan pasar (`filters: Any`, y un NUL no viola ningún `max_length`) y
    aguas abajo acaban en un `CAST(:x AS jsonb)` —`saved_searches.filters`,
    `applications.snapshot`, `profile_revisions.content`— o en una columna
    `text`, que Postgres RECHAZA. Sin este guard el error salía como **500**
    del sobre de `api/main.py`, no como el 400 de frontera del contrato, y lo
    disparaba entrada de usuario.

    El camino de IMPORT fue endurecido para esta MISMA clase (G1-P2-1,
    G2-P2-1, G3-P3-2) pero con la respuesta que allí corresponde: COERCIÓN
    (`_json_safe` mapea NUL→U+FFFD y no-finito→texto), porque una migración no
    puede perder una fila por un byte. Aquí la respuesta correcta es la
    CONTRARIA: rechazar. Un endpoint vivo que sustituya en silencio un
    carácter del CV o del filtro del usuario le devuelve un 201 sobre datos
    que él no escribió.

    Se llama con el `model_dump` que el endpoint ya calcula para el
    `request_hash`, ANTES de reservar la idempotencia."""
    if isinstance(payload, str):
        if "\x00" in payload:
            raise ApiError(
                400, "invalid_json",
                f"{path}: carácter NUL (U+0000), que Postgres no admite ni en "
                "text ni en jsonb",
            )
        return
    if isinstance(payload, float):
        if payload != payload or payload in (float("inf"), float("-inf")):
            raise ApiError(
                400, "invalid_json",
                f"{path}: {payload} no es JSON válido (NaN/Infinity son "
                "literales no estándar que Postgres rechaza)",
            )
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            ensure_json_storable(key, f"{path}.<clave>")
            ensure_json_storable(value, f"{path}.{key}")
        return
    if isinstance(payload, (list, tuple)):
        for i, value in enumerate(payload):
            ensure_json_storable(value, f"{path}[{i}]")


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
