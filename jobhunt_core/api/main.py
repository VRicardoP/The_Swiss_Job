"""API /v1 del core (A-01 sondas + A-09 negocio read-only multi-tenant).

Endpoints de negocio en api/v1.py (DTOs de CONTRATOS_FASE_A.md §2, matriz
ruta→scope→ownership, ETag, cursor keyset opaco); errores del contrato
{code, message, details} vía ApiError + handler global.
"""

import logging
from functools import lru_cache
from pathlib import Path

import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from jobhunt_core import __version__
from jobhunt_core.api.deps import ApiError
from jobhunt_core.api.v1 import router as v1_router
from jobhunt_core.database import engine

logger = logging.getLogger(__name__)

app = FastAPI(
    title="jobhunt-core",
    version=__version__,
    docs_url="/v1/docs",
    openapi_url="/v1/openapi.json",
)
app.include_router(v1_router)


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    """Errores con la FORMA del contrato §2: {code, message, details}."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "details": exc.details},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """La entrada MALFORMADA (uuid/limit ilegibles) también lleva el sobre del
    contrato (auditoría A-09 P2: el 422 por defecto de FastAPI rompía a un BFF
    que parsea resp.json()['code'] en todo 4xx). El 400 se suma al catálogo
    para input malformado, con la MISMA forma."""
    return JSONResponse(
        status_code=400,
        content={
            "code": "invalid_request",
            "message": "petición malformada",
            "details": {
                "errors": [
                    {"loc": [str(x) for x in e.get("loc", [])], "msg": str(e.get("msg", ""))}
                    for e in exc.errors()
                ]
            },
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """El 500 también respeta el sobre; el detalle SOLO va al log (jamás
    internals al cliente)."""
    logger.exception("API: error no gestionado")
    return JSONResponse(
        status_code=500,
        content={"code": "internal_error", "message": "error interno", "details": {}},
    )


@app.get("/v1/health")
async def health() -> dict:
    """Liveness: el proceso responde (no implica BD migrada — ver /v1/ready)."""
    return {"status": "ok", "service": "jobhunt-core", "version": __version__}


@lru_cache(maxsize=1)
def _expected_head() -> str:
    """Head de la cadena de migraciones del core (según el código desplegado)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    return ScriptDirectory.from_config(cfg).get_heads()[0]


@app.get("/v1/ready")
async def ready() -> JSONResponse:
    """Readiness: la BD del core responde Y está migrada al head esperado.

    Evita el falso-verde de un health estático cuando core-migrate falló
    (revisión externa A-01 #4). La query resuelve por search_path (fijado a
    jobhunt en la conexión), sin interpolar el nombre del esquema.
    """
    try:
        async with engine.connect() as conn:
            current = (
                await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
            ).scalar()
    except Exception:  # BD caída, esquema/tabla ausente, credencial mala...
        # El detalle (host/usuario/SQL) se queda en el log; al cliente solo un
        # código genérico (rev. #6: no filtrar internals por la sonda).
        logger.exception("readiness: la BD del core no está disponible/migrada")
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "database_unavailable"},
        )
    expected = _expected_head()
    if current != expected:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "alembic": current, "expected": expected},
        )
    return JSONResponse(content={"status": "ready", "alembic": current})
