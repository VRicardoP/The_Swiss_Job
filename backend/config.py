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
    MATCH_STAGE1_TOP_N: int = 50
    MATCH_DEFAULT_TOP_K: int = 20
    SEMANTIC_DEDUP_THRESHOLD: float = 0.95

    # Groq LLM
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_RERANK_MODEL: str = "llama-3.1-8b-instant"
    GROQ_RERANK_BATCH_SIZE: int = 10
    GROQ_RERANK_TEMPERATURE: float = 0.2
    GROQ_RERANK_MAX_TOKENS: int = 2048
    GROQ_CACHE_TTL_DAYS: int = 7
    MATCH_LLM_RERANK_TOP_N: int = 20

    # Scraper schedule
    SCHEDULER_SCRAPER_INTERVAL_HOURS: int = 6

    # Alerts & Saved Searches
    ALERTS_MAX_PUSH_PER_DAY: int = 10
    ALERTS_MIN_SCORE_THRESHOLD: int = 50
    SCHEDULER_SEARCH_INTERVAL_MINUTES: int = 60

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

    # Document generation
    GROQ_DOC_TEMPERATURE: float = 0.4
    GROQ_DOC_MAX_TOKENS: int = 4096
    GROQ_DOC_CACHE_TTL_HOURS: int = 24

    # Compliance (TD-06)
    COMPLIANCE_BLOCK_THRESHOLD: int = 3

    # Scraper defaults (TD-06)
    SCRAPER_HTTPX_TIMEOUT: float = 20.0
    SCRAPER_PLAYWRIGHT_TIMEOUT_MS: int = 30000

    # Provider API Keys (empty = provider disabled)
    JSEARCH_RAPIDAPI_KEY: str = ""
    ADZUNA_APP_ID: str = ""
    ADZUNA_APP_KEY: str = ""
    JOOBLE_API_KEY: str = ""
    CAREERJET_AFFID: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
