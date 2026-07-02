from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # PostgreSQL
    DATABASE_URL: str = (
        "postgresql+asyncpg://swissjob:swissjob_dev_2024@postgres:5432/swissjobhunter"
    )

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # Celery
    CELERY_BROKER_URL: str = "redis://redis:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/2"

    # CORS
    BACKEND_CORS_ORIGINS: list[str] = [
        "http://localhost:5174",
        "http://localhost:5173",
    ]

    # App
    SECRET_KEY: str = "change-me-in-production"
    APP_NAME: str = "SwissJobHunter"

    # JWT
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Scheduler
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_FETCH_INTERVAL_MINUTES: int = 30

    # Embedding model
    EMBEDDING_MODEL_NAME: str = "paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_BATCH_SIZE: int = 64

    # CV upload
    CV_MAX_SIZE_MB: int = 10
    CV_ALLOWED_TYPES: list[str] = [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]

    # SSE
    SSE_QUEUE_MAXSIZE: int = 100

    # AI Matching
    MATCH_SCORE_THRESHOLD: float = 35.0  # minimum score to qualify as a match
    MATCH_LLM_RERANK_TOP: int = 50  # how many top candidates get LLM re-ranking
    SEMANTIC_DEDUP_THRESHOLD: float = 0.95

    # Groq LLM
    GROQ_API_KEY: str = ""
    # Modelo pesado — generación de cartas/documentos (calidad > volumen).
    # llama-3.3-70b-versatile queda DECOMISIONADO por Groq el 2026-08-16 → migrado a
    # gpt-oss-120b (reemplazo recomendado por Groq). Es un modelo de razonamiento pero
    # su cadena de pensamiento va en un campo `reasoning` aparte: `.content` sale limpio.
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    # Modelo rápido — traducción de títulos + re-ranking Stage 3 (alto volumen).
    # Scout (MoE, ~17B activos) es rápido y barato, devuelve JSON limpio y sigue vigente.
    GROQ_RERANK_MODEL: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    GROQ_RERANK_BATCH_SIZE: int = 10
    GROQ_RERANK_TEMPERATURE: float = 0.2
    GROQ_RERANK_MAX_TOKENS: int = 2048
    GROQ_CACHE_TTL_DAYS: int = 7

    # Scraper schedule
    SCHEDULER_SCRAPER_INTERVAL_HOURS: int = 6

    # Alerts & Saved Searches
    ALERTS_MAX_PUSH_PER_DAY: int = 10
    ALERTS_MIN_SCORE_THRESHOLD: int = 50
    SCHEDULER_SEARCH_INTERVAL_MINUTES: int = 60

    # Watchlist colegios — thresholds del dual-channel
    # Push inmediato si score_final + urgency_score >= threshold.
    WATCHLIST_PUSH_THRESHOLD: float = 70.0
    # Digest diario incluye matches con score_final entre [min, push_threshold).
    WATCHLIST_DIGEST_MIN_SCORE: float = 40.0

    # Database pool (TD-20)
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE: int = 1800
    DB_POOL_PRE_PING: bool = True

    # Celery task pool (smaller, disposable)
    DB_TASK_POOL_SIZE: int = 2
    DB_TASK_MAX_OVERFLOW: int = 3

    # Parallel fetch (TD-18)
    FETCH_CONCURRENCY: int = 5

    # Groq concurrency (TD-22)
    GROQ_CONCURRENCY: int = 2

    # Document generation (CV/carta). Prioriza CALIDAD sobre latencia.
    GROQ_DOC_TEMPERATURE: float = 0.4
    GROQ_DOC_MAX_TOKENS: int = 4096
    GROQ_DOC_CACHE_TTL_HOURS: int = 24
    # Proveedor PRIMARIO de documentos: Google Gemini (free tier de Google).
    # gemini-2.5-flash genera CVs de calidad (~9s); gpt-oss-120b en Groq free tier
    # topa a 8k tokens/min y falla en documentos largos → Gemini lo evita. Si la key
    # falta o Gemini falla/satura, DocumentGeneratorService cae a Groq (GROQ_MODEL).
    # Nota: los modelos gemma-4-* dan mala salida para esta tarea (repiten el prompt)
    # y ~30s de latencia; por eso el default es gemini-2.5-flash, no Gemma.
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_TIMEOUT_SECONDS: float = 60.0

    # Compliance (TD-06)
    COMPLIANCE_BLOCK_THRESHOLD: int = 3

    # Scraper defaults (TD-06)
    SCRAPER_HTTPX_TIMEOUT: float = 20.0
    SCRAPER_PLAYWRIGHT_TIMEOUT_MS: int = 30000

    # Anti-detección del scraper (apuntes del curso de web scraping)
    # Jitter: fracción aleatoria extra sobre RATE_LIMIT_SECONDS para evitar
    # intervalos constantes (0.5 = hasta +50%).
    SCRAPER_DELAY_JITTER_RATIO: float = 0.5
    # Reintentos ante errores transitorios (timeouts, 5xx) con backoff exponencial.
    SCRAPER_MAX_RETRIES: int = 2
    SCRAPER_RETRY_BACKOFF_SECONDS: float = 2.0
    # Proxy opcional (httpx + Playwright). Vacío = desactivado. Permite enrutar
    # portales muy protegidos a través de un proxy/rotación residencial externo.
    SCRAPER_PROXY_URL: str = ""
    # Browser remoto opcional vía CDP (p.ej. un browser stealth de pago). Vacío =
    # se lanza Chromium local. Si se define, Playwright se conecta por CDP.
    SCRAPER_BROWSER_CDP_URL: str = ""
    # Chromium exige --no-sandbox al correr como root dentro de un contenedor, pero
    # eso REDUCE el aislamiento del renderer. True por defecto (compat Docker root);
    # ponlo en False donde el runtime permita mantener el sandbox (contenedor no-root).
    SCRAPER_PLAYWRIGHT_NO_SANDBOX: bool = True

    # Provider API Keys (empty = provider disabled)
    JSEARCH_RAPIDAPI_KEY: str = ""
    ADZUNA_APP_ID: str = ""
    ADZUNA_APP_KEY: str = ""
    JOOBLE_API_KEY: str = ""
    CAREERJET_AFFID: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
