import asyncio
import logging
import re
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from services.documents.port import DocumentsError
from services.documents.delivery import DocumentDeliveryError
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from config import settings
from core.rate_limit import LimiteGlobalMiddleware, limiter
from logging_setup import configure_logging
from providers import log_provider_status
from services.scheduler import run_scheduler_with_leader_lock
from services.exclusions_sync import run_exclusion_delivery
from services.sse_manager import SSEManager
from routers.analytics import router as analytics_router
from routers.applications import router as applications_router
from routers.auth import router as auth_router
from routers.documents import router as documents_router
from routers.integration_inbox import router as integration_inbox_router
from routers.jobs import router as jobs_router
from routers.match import router as match_router
from routers.notifications import router as notifications_router
from routers.profile import router as profile_router
from routers.saved_searches import router as searches_router
from routers.watchlist import router as watchlist_router

# Fija el nivel de logging (INFO por defecto) antes de que nada emita, para que
# el scheduler y la cosecha diaria sean visibles en los logs.
configure_logging()

logger = logging.getLogger(__name__)


_INSECURE_SECRET_KEY = "change-me-in-production"
# C7: contraseñas que SOLO valen en dev y marcadores de plantilla sin rellenar.
# La lista es la del core (`jobhunt_core.config`) más la del postgres legacy,
# que estaba publicada en .env.example y en este repositorio.
_DEV_DB_PASSWORDS = frozenset(
    {"swissjob_dev_2024", "jobhunt_core_dev", "postgres", "password"}
)
_PLACEHOLDER_RE = re.compile(r"CAMBIA|CHANGE_?ME|PLACEHOLDER|EXAMPLE", re.IGNORECASE)


def _validate_security_config() -> None:
    """Aborta el arranque si SECRET_KEY conserva el valor por defecto.

    Permitir explícitamente el default solo en entorno test (config conftest).
    Cualquier instancia accesible que no sea tests es candidata a falsificación
    de JWTs con una secret públicamente conocida.
    """
    import os

    if settings.SECRET_KEY != _INSECURE_SECRET_KEY:
        return
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    raise RuntimeError(
        "SECRET_KEY is set to the insecure default 'change-me-in-production'. "
        'Generate a random value (e.g. `python -c "import secrets; '
        'print(secrets.token_urlsafe(32))"`) and set it in your .env.'
    )


def _es_credencial_de_dev(password: str | None) -> bool:
    """Misma lista negra que `jobhunt_core.config._bad_secret`, para la base."""
    return (
        not password
        or password in _DEV_DB_PASSWORDS
        or bool(_PLACEHOLDER_RE.search(password))
    )


def _validate_database_credentials(*, en_pruebas: bool | None = None) -> None:
    """Aborta el arranque si la base usa una contraseña de dev o de plantilla.

    `ALLOW_DEV_CREDENTIALS=true` lo permite a propósito: el backend legacy no
    tiene noción de entorno (el core sí, `CORE_ENV`), así que ese permiso
    explícito es lo único que distingue un portátil de un servidor. Ausente,
    manda la lectura segura: esto no es dev, luego no se arranca.

    `en_pruebas` existe para poder probar la guardia MISMA: pytest reescribe
    `PYTEST_CURRENT_TEST` al entrar en cada fase del test, así que borrarla en
    un fixture no sirve de nada — la guardia se seguiría saltando y los casos
    pasarían en verde sin haber comprobado nada.
    """
    import os

    if en_pruebas is None:
        en_pruebas = bool(os.getenv("PYTEST_CURRENT_TEST"))
    if settings.ALLOW_DEV_CREDENTIALS or en_pruebas:
        return
    if not _es_credencial_de_dev(urlsplit(settings.DATABASE_URL).password):
        return
    raise RuntimeError(
        "DATABASE_URL usa una contraseña de desarrollo o un marcador de "
        "plantilla sin rellenar. Pon una contraseña real, o declara "
        "ALLOW_DEV_CREDENTIALS=true si esto es de verdad un entorno de "
        "desarrollo."
    )


async def _warm_embedding_model() -> None:
    """Carga el modelo de embeddings en un hilo, sin bloquear el event loop.

    El primer arranque tarda minutos en cargar el SentenceTransformer; hacerlo
    síncrono en el lifespan dejaba el servidor sin responder (health incluido)
    todo ese tiempo. Lo cargamos en background: las peticiones que lo necesiten
    esperan en el loader perezoso (thread-safe). Un fallo aquí no tumba el
    arranque; la carga perezosa lo reintentará en la primera petición.
    """
    from services.job_matcher import JobMatcher

    try:
        await asyncio.to_thread(JobMatcher._get_model)
        logger.info("Embedding model warmed up")
    except Exception:
        logger.exception("Embedding model warmup failed; se cargará bajo demanda")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_security_config()
    _validate_database_credentials()
    # Validate handover BEFORE SSE, warmup, or scheduler tasks are armed.
    from scrapers import get_scraper_names
    from services.legacy_sources import disabled_sources

    disabled_sources(settings.LEGACY_DISABLED_SCRAPERS, get_scraper_names())
    log_provider_status()

    # Startup — SSE Manager (Redis pub/sub)
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
    sse = SSEManager(redis_client, queue_maxsize=settings.SSE_QUEUE_MAXSIZE)
    await sse.start()
    app.state.sse_manager = sse
    app.state.redis_client = redis_client

    # Warming del modelo de embeddings en background (no bloquea el arranque).
    warmup_task = (
        asyncio.create_task(_warm_embedding_model())
        if settings.EMBEDDING_PRELOAD_ON_STARTUP
        else None
    )

    # El scheduler corre en UN SOLO proceso (leader-lock en Redis) para evitar
    # el doble disparo con varios workers de gunicorn.
    scheduler_task = asyncio.create_task(run_scheduler_with_leader_lock())
    exclusion_delivery_task = asyncio.create_task(run_exclusion_delivery())
    from services.profile_sync import run_profile_delivery

    profile_delivery_task = (
        asyncio.create_task(run_profile_delivery())
        if settings.CORE_PROFILE_SYNC_ENABLED
        else None
    )
    from services.profile_erasure import run_erasure_delivery

    erasure_delivery_task = asyncio.create_task(run_erasure_delivery())

    # Punto 5 §10.3-ter: calienta el recorrido del feed fuera de la petición.
    # SIN leader-lock a propósito: la caché del recorrido vive en proceso, así
    # que cada worker tiene que calentar la suya.
    from services.matching.warm import run_feed_warmup

    feed_warmup_task = asyncio.create_task(run_feed_warmup())

    # M3/T12: la caché del recorrido vive en proceso y gunicorn corre con -w 2,
    # así que cada worker tiene que enterarse de lo que invalidan los demás.
    from services.matching.cache_bus import escuchar as escuchar_invalidaciones

    cache_bus_task = asyncio.create_task(escuchar_invalidaciones())

    yield

    # Shutdown
    if warmup_task is not None and not warmup_task.done():
        warmup_task.cancel()
    feed_warmup_task.cancel()
    cache_bus_task.cancel()
    for tarea in (feed_warmup_task, cache_bus_task):
        try:
            await tarea
        except asyncio.CancelledError:
            pass
    erasure_delivery_task.cancel()
    if profile_delivery_task is not None:
        profile_delivery_task.cancel()
        try:
            await profile_delivery_task
        except asyncio.CancelledError:
            pass
    try:
        await erasure_delivery_task
    except asyncio.CancelledError:
        pass
    exclusion_delivery_task.cancel()
    try:
        await exclusion_delivery_task
    except asyncio.CancelledError:
        pass
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
    await sse.stop()
    await redis_client.aclose()


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
# H5/T8: sin esto, `default_limits` del Limiter es configuración MUERTA y sólo
# las rutas con decorador tienen límite — eran 5 de toda la API. NO se usa el
# middleware de slowapi: con FastAPI 0.141 no encuentra los handlers de los
# routers incluidos y exime todas las rutas en silencio (ver rate_limit.py).
# Se añade ANTES que CORS: en Starlette el último `add_middleware` queda más
# externo, así que el 429 sale con las cabeceras de CORS puestas y el navegador
# puede leerlo, en vez de verlo como un fallo de red opaco.
app.add_middleware(LimiteGlobalMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=settings.BACKEND_CORS_METHODS,
    allow_headers=settings.BACKEND_CORS_HEADERS,
)

# Routers
app.include_router(analytics_router)
app.include_router(applications_router)
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(integration_inbox_router)
app.include_router(jobs_router)
from services.matching.feedback import block_feedback_writes  # noqa: E402  (import tardío a propósito: la dependencia se aplica a match_router justo aquí)

app.include_router(match_router, dependencies=[Depends(block_feedback_writes)])
app.include_router(notifications_router)
app.include_router(profile_router)
app.include_router(searches_router)
app.include_router(watchlist_router)


from services.schools.port import CoreUnavailableError as SchoolCoreUnavailableError  # noqa: E402  (import tardío a propósito: sólo lo usa el manejador de excepciones de abajo)


@app.exception_handler(SchoolCoreUnavailableError)
async def school_unavailable_handler(request, exc):
    return JSONResponse(
        status_code=503, content={"detail": "School storage temporarily unavailable"}
    )


@app.get("/health/schools")
async def school_health():
    return {"writes": "frozen" if settings.SCHOOL_WRITES_FROZEN else "enabled"}


@app.exception_handler(DocumentsError)
async def document_unavailable_handler(request, exc):
    return JSONResponse(
        status_code=503, content={"detail": "Document storage temporarily unavailable"}
    )


@app.exception_handler(DocumentDeliveryError)
async def document_delivery_error_handler(request, exc):
    code = 409 if str(exc) == "operation_conflict" else 503
    return JSONResponse(status_code=code, content={"detail": str(exc)})


@app.get("/health/documents")
async def document_health():
    return {"writes": "frozen" if settings.DOCUMENT_WRITES_FROZEN else "enabled"}


@app.get("/health/feedback")
async def feedback_health():
    return {
        "writes": "frozen" if settings.FEEDBACK_WRITES_FROZEN else "enabled",
        "writer": "core" if settings.CORE_FEEDBACK_ENABLED else "local",
    }


@app.get("/health/searches")
async def search_health():
    return {"writes": "frozen" if settings.SAVED_SEARCH_WRITES_FROZEN else "enabled"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/api/v1/health")
async def health_v1():
    return {"status": "healthy"}
