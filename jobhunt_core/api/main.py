"""API /v1 del core — esqueleto de Fase A (A-01): health, readiness y version.

Los endpoints de negocio (vacancies/matches, DTOs de CONTRATOS_FASE_A.md §2)
llegan en A-09; este módulo solo establece la app, /v1 y las sondas.
"""

from functools import lru_cache
from pathlib import Path

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from jobhunt_core import __version__
from jobhunt_core.database import engine

app = FastAPI(
    title="jobhunt-core",
    version=__version__,
    docs_url="/v1/docs",
    openapi_url="/v1/openapi.json",
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
    except Exception as exc:  # BD caída, esquema/tabla ausente, credencial mala...
        return JSONResponse(
            status_code=503, content={"status": "not_ready", "error": str(exc)[:200]}
        )
    expected = _expected_head()
    if current != expected:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "alembic": current, "expected": expected},
        )
    return JSONResponse(content={"status": "ready", "alembic": current})
