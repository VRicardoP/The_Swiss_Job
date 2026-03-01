import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from config import settings
from core.rate_limit import limiter
from providers import log_provider_status
from services.scheduler import scheduler, setup_schedules
from services.sse_manager import SSEManager
from routers.applications import router as applications_router
from routers.auth import router as auth_router
from routers.documents import router as documents_router
from routers.jobs import router as jobs_router
from routers.match import router as match_router
from routers.notifications import router as notifications_router
from routers.profile import router as profile_router
from routers.saved_searches import router as searches_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — SSE Manager (Redis pub/sub)
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
    sse = SSEManager(redis_client, queue_maxsize=settings.SSE_QUEUE_MAXSIZE)
    await sse.start()
    app.state.sse_manager = sse
    app.state.redis_client = redis_client

    # Preload embedding model to avoid latency spike on first request (TD-16)
    logger.info("Preloading embedding model...")
    from services.job_matcher import JobMatcher

    JobMatcher._get_model()
    logger.info("Embedding model loaded")

    log_provider_status()
    setup_schedules()
    scheduler.start()

    yield

    # Shutdown
    scheduler.shutdown()
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

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(applications_router)
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(jobs_router)
app.include_router(match_router)
app.include_router(notifications_router)
app.include_router(profile_router)
app.include_router(searches_router)


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/api/v1/health")
async def health_v1():
    return {"status": "healthy"}
