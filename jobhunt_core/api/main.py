"""API /v1 del core (A-01 sondas + A-09 negocio read-only multi-tenant).

Endpoints de negocio en api/v1.py (DTOs de CONTRATOS_FASE_A.md §2, matriz
ruta→scope→ownership, ETag, cursor keyset opaco); errores del contrato
{code, message, details} vía ApiError + handler global.
"""

import logging
import os
from pathlib import Path

import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from jobhunt_core import UNKNOWN_RELEASE, __release_sha__, __version__
from jobhunt_core.api.deps import ApiError
from jobhunt_core.api.v1 import router as v1_router
from jobhunt_core.api.v1_applications import router as applications_router
from jobhunt_core.api.v1_saved_searches import router as saved_searches_router
from jobhunt_core.database import engine

logger = logging.getLogger(__name__)

app = FastAPI(
    title="jobhunt-core",
    version=__version__,
    docs_url="/v1/docs",
    openapi_url="/v1/openapi.json",
)
app.include_router(v1_router)
# C-4: escrituras de candidaturas/bookmarks y búsquedas guardadas.
app.include_router(applications_router)
app.include_router(saved_searches_router)


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


_HTTP_CODES = {404: "not_found", 405: "method_not_allowed", 429: "rate_limited"}


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Los HTTPException de Starlette (ruta inexistente → 404, método
    incorrecto → 405...) TAMBIÉN llevan el sobre del contrato (rev. A-09 #3:
    'TODOS los caminos' significa todos)."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": _HTTP_CODES.get(exc.status_code, "http_error"),
            "message": str(exc.detail),
            "details": {},
        },
    )


def _openapi_with_contract_errors():
    """OpenAPI FIEL al contrato (rev. A-09 #6): la validación responde 400
    (no el 422 que FastAPI anuncia por defecto) y todo endpoint puede dar el
    500 uniforme. El securityScheme Bearer lo aporta la dependencia."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title, version=app.version, routes=app.routes,
    )
    error_ref = {
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorDTO"}
            }
        }
    }
    for path in schema.get("paths", {}).values():
        for op in path.values():
            resps = op.get("responses", {})
            if "422" in resps:
                resps.pop("422")
                resps.setdefault("400", {"description": "Bad Request", **error_ref})
            resps.setdefault("500", {"description": "Internal Server Error", **error_ref})
    app.openapi_schema = schema
    return schema


app.openapi = _openapi_with_contract_errors


# Perfil de DESARROLLO: `docker-compose.dev.yml` monta ./jobhunt_core como
# volumen, así que el código en disco puede no ser el de la imagen. Ese readiness
# NO autoriza operaciones (flip, maniobras de datos) y lo dice en su respuesta.
CODE_MUTABLE = os.getenv("CORE_CODE_MUTABLE", "").strip().lower() in {"1", "true", "yes"}

# El montaje del código es un HECHO OBSERVABLE del proceso (auditoría G11 P2-2). La
# variable de arriba solo dice lo que alguien escribió en el compose: montando
# `/app/jobhunt_core` sin ponerla, la sonda publicaba la release verdadera con
# `authoritative: true` mientras respondía código de otra procedencia — una vía MÁS
# fuerte que la que cerró G10, porque no falsifica el nombre de la release sino el código
# que responde bajo un nombre verdadero. `/proc/self/mountinfo` lista los montajes del
# namespace de ESTE proceso, así que la suposición se puede sustituir por una lectura.
_MOUNTINFO_PATH = Path("/proc/self/mountinfo")

# La release HORNEADA en la imagen, FUERA de todo árbol que un compose sustituya
# (auditorías G10 P2-2 y externa R2 P1-1). `RELEASE_SHA` es un ENV de la imagen y el ENV
# de una imagen lo pisa cualquier `environment:`/`env_file:` del contenedor —y los
# servicios del core arrancan con `env_file`—, así que un `-e RELEASE_SHA=deadbee`
# publicaba `deadbee` con `authoritative: true` sobre el código de otra construcción. El
# fichero lo escribe el Dockerfile con el mismo build arg.
#
# Vivía en `/app/RELEASE` y el comentario lo llamaba «un sitio que el entorno no puede
# pisar»: era FALSO. `/app` es el WORKDIR y contiene el paquete, así que un bind mount en
# `/app` sustituye de un golpe el código Y el marcador. Ahora vive en `/opt`, que la
# imagen no usa para nada más, y —esto es lo que de verdad cierra el hueco— su montaje se
# OBSERVA igual que el del código, en vez de suponerse imposible.
_BAKED_RELEASE_PATH = Path("/opt/jobhunt-release/RELEASE")


def _mounted_over(raiz: str) -> bool:
    """¿Algún montaje del namespace de este proceso sustituye lo que hay en `raiz`?

    Cuentan tres formas de sustituirlo, y las tres se han demostrado:
    - el punto de montaje IGUAL a `raiz` (montar el paquete entero);
    - un punto DEBAJO de `raiz` — montar `main.py` suelto sustituye el código que
      responde igual de bien, y mirar solo la raíz lo daba por limpio (comprobado);
    - un punto ANCESTRO de `raiz`: un bind mount en `/app` sustituye `/app/jobhunt_core`
      sin ser ni igual ni descendiente, y era el ÚNICO caso que no se miraba
      (auditoría externa R2 P1-1, reproducido con un `mountinfo` sintético).

    `/` queda fuera de la regla de ancestros a propósito: es el rootfs de CUALQUIER
    contenedor y con él dentro toda imagen se declararía mutable, que es lo mismo que no
    comprobar nada.
    """
    try:
        lineas = _MOUNTINFO_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        # Sin `/proc` legible no se puede DESCARTAR el montaje. Dirección segura: la
        # marca solo afirma cuando puede comprobarlo.
        return True
    for linea in lineas:
        campos = linea.split(" ")
        if len(campos) <= 4:
            continue
        # Formato de mountinfo: el 5º campo es el punto de montaje (los caracteres raros
        # viajan escapados como \040, y aquí la ruta no lleva ninguno).
        punto = campos[4]
        if (
            punto == raiz
            or punto.startswith(raiz + "/")
            or (punto != "/" and raiz.startswith(punto + "/"))
        ):
            return True
    return False


def _code_is_mounted() -> bool:
    """Si el código que este proceso importa —o CUALQUIER trozo suyo— viene de un montaje."""
    return _mounted_over(str(Path(__file__).resolve().parent.parent))


def _release_marker_is_mounted() -> bool:
    """Si el ANCLA contra la que se contrasta `RELEASE_SHA` viene de un montaje.

    Sacar el marcador del árbol de código no basta por sí solo: montar su ruta nueva
    reabriría el agujero de G10 en otro sitio. Se lee al importar, así que un montaje ya
    presente al arrancar habría envenenado el valor congelado.
    """
    return _mounted_over(str(_BAKED_RELEASE_PATH))


def _read_baked_release() -> str | None:
    """None si la imagen no la hornea (o el proceso no corre en la imagen): sin ancla
    con la que contrastar el ENV, la marca no puede afirmar nada."""
    try:
        return _BAKED_RELEASE_PATH.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


_BAKED_RELEASE = _read_baked_release()  # CONGELADO al importar, como el head esperado


def _authoritative() -> bool:
    """Si lo que publican las sondas se puede usar para VERIFICAR una release.

    Exige las cuatro cosas (auditoría G9 P2-A/P2-B, G10 P2-2, G11 P2-2 y externa R2 P1-1):
    - código INMUTABLE: ni la variable de opt-out `CORE_CODE_MUTABLE` NI un bind mount
      sobre el árbol de código —comprobado en `/proc/self/mountinfo`, porque la variable
      solo dice lo que alguien escribió en el compose y montando el código sin ponerla la
      marca seguía en verde—. Sin montaje, el `release` del ENV y el `alembic_expected`
      leído del disco vienen de la MISMA imagen; con él, el proceso puede estar sirviendo
      código que no es el del SHA que publica;
    - ANCLA INMUTABLE: tampoco puede venir de un montaje el fichero horneado contra el
      que se contrasta el ENV, o la comparación de abajo se compara consigo misma;
    - release NOMBRABLE: una imagen construida sin el build arg hornea `unknown`, y el
      paso «todos publican el mismo SHA» se satisface entre `unknown`s sin decir nada;
    - release ATADA A LA IMAGEN: el SHA que se publica tiene que ser el que la imagen
      lleva horneado en `_BAKED_RELEASE_PATH`. Sin esto la marca certificaba dos cosas
      que sí podía comprobar y ninguna que atara el SHA al código que responde, y un
      `RELEASE_SHA` obsoleto en un `.env` —el modo de fallo más banal que hay— producía
      un verde con nombre falso justo donde `docs/DEPLOY_NAS.md` lo convierte en la
      autorización para operar.

    LO QUE ESTA MARCA **NO** ES (acotación de la auditoría externa R2 P1-1, conservada
    a propósito): una garantía industrial de procedencia. Todo lo que compara lo observa
    el propio proceso desde DENTRO del contenedor, así que cierra la falsificación local
    demostrada —montar código o el ancla, o inyectar `RELEASE_SHA`— y nada más. La
    garantía definitiva exige además contrastar el DIGEST de la imagen tal y como lo ve
    el orquestador (`docker inspect`/registro), que es la única señal que no vive dentro
    del contenedor que se está auditando.
    """
    return (
        not CODE_MUTABLE
        and not _code_is_mounted()
        and not _release_marker_is_mounted()
        and __release_sha__ != UNKNOWN_RELEASE
        and __release_sha__ == _BAKED_RELEASE
    )


@app.get("/v1/health")
async def health() -> dict:
    """Liveness: el proceso responde (no implica BD migrada — ver /v1/ready).

    Publica la IDENTIDAD de la release: `version` es la constante 0.1.0 y no
    distingue despliegues, así que van también el SHA de la imagen y el head de
    migraciones que este proceso espera. Con esas dos señales el paso de
    verificación de un despliegue («todos publican el mismo SHA y head») es
    comprobable en vez de confiado (auditoría externa 2026-08-27 P1-3).

    Los dos datos tienen PROCEDENCIA distinta —`release` del ENV horneado en la
    imagen, `alembic_expected` del sistema de ficheros— y esta es la sonda con la que
    empieza el ritual de verificación, así que lleva la MISMA marca que `/v1/ready`:
    sin ella publicaba el SHA de una imagen mientras corría otro código (G9 P2-A).
    """
    return {
        "status": "ok",
        "service": "jobhunt-core",
        "version": __version__,
        "release": __release_sha__,
        "alembic_expected": _expected_head(),
        "authoritative": _authoritative(),
    }


def _read_expected_head() -> str:
    """Lee de disco el head de la cadena de migraciones del paquete. En el perfil
    operativo el disco ES la imagen (no hay bind mount); en el de desarrollo es el
    árbol de trabajo montado, y por eso ese readiness se declara no autoritativo."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    return ScriptDirectory.from_config(cfg).get_heads()[0]


# CONGELADO AL IMPORTAR, desde la misma imagen que trae los handlers.
#
# Historia de las dos caras del mismo error, ambas vividas en este despliegue:
# con `@lru_cache` sin clave la expectativa quedaba fijada al arrancar mientras
# el volumen de código sí cambiaba → 503 con la BD sana durante dos días (falso
# ROJO, bf3fbfd). Releerla del volumen en caliente cierra ese caso y abre el
# simétrico: los ficheros pasan a la release B, `core-migrate` migra a B y
# `/v1/ready` certifica B mientras los módulos ya importados siguen siendo los
# de A (falso VERDE). Lo único coherente es que la expectativa venga de donde
# viene el código que responde. La incoherencia desaparece porque el perfil
# operativo ya no monta el código: cambiar la cadena exige cambiar la imagen, y
# eso recrea el proceso. Si la cadena no se puede leer, el proceso NO arranca —
# preferible a servir peticiones con una expectativa inventada.
_EXPECTED_HEAD = _read_expected_head()


def _expected_head() -> str:
    """Head de migraciones que espera el CÓDIGO QUE ESTÁ CORRIENDO."""
    return _EXPECTED_HEAD


# El `status` del readiness LISTO. Es contrato, no prosa: el smoke del Paso 7 de
# `docs/DEPLOY_NAS.md` exigía `ok` mientras esta API devolvía `ready` desde siempre
# (auditoría externa R3 P2-1), así que un cutover correcto habría terminado en falso
# rojo DESPUÉS de la parte irreversible. `backend/scripts/nas_cutover.sh` lleva la
# misma constante en `READY_STATUS_ESPERADO` y `test_deploy_order.py` comprueba que las
# dos no puedan divergir.
_READY_STATUS = "ready"


@app.get("/v1/ready")
async def ready() -> JSONResponse:
    """Readiness: la BD del core responde Y está migrada al head que espera ESTE código.

    Evita el falso-verde de un health estático cuando core-migrate falló
    (revisión externa A-01 #4). La query resuelve por search_path (fijado a
    jobhunt en la conexión), sin interpolar el nombre del esquema.

    La expectativa se congela al arrancar (ver `_EXPECTED_HEAD`) y la respuesta
    lleva la release del proceso: en el perfil operativo un 200 certifica el par
    (código, esquema) de UNA release, no el esquema de una y los handlers de otra.
    En el de desarrollo el código va montado y el 200 llega con
    `authoritative: false` — informativo, no autorización para operar; lo mismo si la
    imagen no sabe nombrar su release (`RELEASE_SHA=unknown`, G9 P2-B).
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
            content={
                "status": "not_ready",
                "reason": "database_unavailable",
                # La identidad de la release va en los DOS 503, no solo en el de head
                # desalineado (auditoría G10 P3-2): con la BD caída es justo cuando el
                # operador necesita saber qué proceso tiene delante, y esta rama le
                # dejaba sin `release` ni `authoritative`. No filtra internals: el SHA
                # ya es público en /v1/health.
                "release": __release_sha__,
                "authoritative": _authoritative(),
            },
        )
    expected = _expected_head()
    if current != expected:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "alembic": current,
                "expected": expected,
                "release": __release_sha__,
                "authoritative": _authoritative(),
            },
        )
    return JSONResponse(
        content={
            "status": _READY_STATUS,
            "alembic": current,
            "release": __release_sha__,
            # False en el perfil de desarrollo (código montado) y con la release sin
            # nombrar: verde informativo, no autorización para operar.
            "authoritative": _authoritative(),
        }
    )
