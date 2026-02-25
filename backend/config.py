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

    # Provider API Keys (empty = provider disabled)
    JSEARCH_RAPIDAPI_KEY: str = ""
    ADZUNA_APP_ID: str = ""
    ADZUNA_APP_KEY: str = ""
    JOOBLE_API_KEY: str = ""
    CAREERJET_AFFID: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
