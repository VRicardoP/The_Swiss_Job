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

    # CORS — orígenes/métodos/cabeceras explícitos (no wildcard con credenciales).
    # El frontend solo envía Authorization + Content-Type; ampliar aquí si cambia.
    BACKEND_CORS_ORIGINS: list[str] = [
        "http://localhost:5174",
        "http://localhost:5173",
    ]
    BACKEND_CORS_METHODS: list[str] = [
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ]
    BACKEND_CORS_HEADERS: list[str] = ["Authorization", "Content-Type", "Accept"]

    # App
    SECRET_KEY: str = "change-me-in-production"
    APP_NAME: str = "SwissJobHunter"

    # Nivel de logging de la app (root). La app no fijaba nivel → los loggers
    # propios quedaban en WARNING y el scheduler/cosecha (INFO) no se veían.
    LOG_LEVEL: str = "INFO"

    # JWT
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Scheduler
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_FETCH_INTERVAL_MINUTES: int = 30

    # Cosecha diaria autónoma: encadena fetch → embeddings → dedup → matching
    # UNA vez al día a hora VARIABLE (patrón circadiano; evita intervalos de
    # reloj, ver a.txt §5/§10). Cuando está activa, sustituye al fetch por
    # intervalos (providers/scrapers) y todo corre sin intervención del usuario.
    SCHEDULER_DAILY_HARVEST_ENABLED: bool = True
    SCHEDULER_DAILY_HARVEST_HOUR: int = 12  # hora base (CET)
    # Jitter en horas: la ejecución se adelanta/retrasa hasta ±N horas, de modo
    # que cada día cae a una hora distinta dentro de la franja diurna.
    SCHEDULER_DAILY_HARVEST_JITTER_HOURS: int = 4

    # Embedding model
    EMBEDDING_MODEL_NAME: str = "paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_BATCH_SIZE: int = 64
    # Precarga del modelo al arrancar. True = warming en BACKGROUND (no bloquea el
    # lifespan; el primer arranque tarda minutos en cargar el modelo). False = carga
    # perezosa en la primera petición que lo use. Nunca bloquea el event loop.
    EMBEDDING_PRELOAD_ON_STARTUP: bool = True

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
    # Re-ranking LLM ADAPTATIVO: si las ofertas cualificadas caben en
    # MATCH_LLM_RERANK_MAX se re-rankean TODAS (sin efecto de tope, caso normal con
    # extracción incremental); por encima se aplica MATCH_LLM_RERANK_TOP para proteger
    # el crédito de IA en avalanchas. Con Gemini de fallback el coste sigue acotado.
    MATCH_LLM_RERANK_TOP: int = 50  # tope de re-ranking cuando el pool es enorme
    MATCH_LLM_RERANK_MAX: int = 150  # por debajo de esto, se re-rankea todo
    SEMANTIC_DEDUP_THRESHOLD: float = 0.95

    # Groq LLM
    GROQ_API_KEY: str = ""
    # Modelo pesado — generación de cartas/documentos (calidad > volumen).
    # llama-3.3-70b-versatile queda DECOMISIONADO por Groq el 2026-08-16 → migrado a
    # gpt-oss-120b (reemplazo recomendado por Groq). Es un modelo de razonamiento pero
    # su cadena de pensamiento va en un campo `reasoning` aparte: `.content` sale limpio.
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    # Modelo rápido — traducción de títulos + re-ranking Stage 3 (alto volumen).
    # llama-4-scout DECOMISIONADO por Groq el 2026-07-17 → migrado a qwen3.6-27b
    # (reemplazo recomendado por Groq; sondeado en vivo 2026-07-17: JSON limpio,
    # 25/25 títulos y rerank 10/10 con parsers estrictos, ~0.5-2.4s por lote).
    GROQ_RERANK_MODEL: str = "qwen/qwen3.6-27b"
    # qwen3.6 razona por defecto y quemaría max_tokens en <think> rompiendo los
    # parsers JSON (verificado en vivo). Este valor se envía como reasoning_effort
    # SOLO en llamadas con GROQ_RERANK_MODEL; vacío = no enviar el parámetro.
    GROQ_RERANK_REASONING_EFFORT: str = "none"
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

    # Email (SMTP) para avisos. Vacío = envío desactivado. Gmail: host
    # smtp.gmail.com, port 587, STARTTLS, y una App Password de 16 car. como
    # SMTP_PASSWORD (requiere 2FA en la cuenta). Puerto 465 → SSL automático.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""  # remitente; si vacío se usa SMTP_USER
    SMTP_STARTTLS: bool = True

    # Alerta de ofertas de PROFESOR DE PRIMARIA en colegios suizos → email.
    # Opt-in aislado (no afecta al matching principal, que penaliza docencia).
    # Requiere SMTP_* configurado para enviar.
    TEACHER_ALERT_ENABLED: bool = True
    TEACHER_ALERT_EMAIL: str = "amoore3199@gmail.com"
    TEACHER_ALERT_INITIAL_LOOKBACK_DAYS: int = (
        7  # primera ejecución mira atrás esta ventana
    )
    SCHEDULER_TEACHER_ALERT_INTERVAL_HOURS: int = 6

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

    # Crawler incremental (cursores + early-stop). Ver a.txt / PLAN_STEALTH_SCRAPER.
    # Eje: el volumen de peticiones depende de las ofertas NUEVAS, no del total.
    CURSOR_INCREMENTAL_ENABLED: bool = True
    # Nº de identidades recientes (URLs) que guarda cada cursor para el early-stop.
    CURSOR_RECENT_IDENTITIES_MAX: int = 300

    # Presupuesto explícito de crawling (plan incremental §7). Requiere cursores
    # (CURSOR_INCREMENTAL_ENABLED): acota las páginas por run según las novedades
    # medias de la fuente y salta runs de fuentes sin novedades (backoff).
    CRAWLER_BUDGET_ENABLED: bool = True
    # Margen de páginas sobre las esperadas (cubre reorden/patrocinadas al borde).
    CRAWLER_BUDGET_SAFETY_PAGES: int = 1
    # Runs vacíos seguidos a partir de los cuales se reduce la frecuencia.
    CRAWLER_BUDGET_EMPTY_RUNS_THRESHOLD: int = 3
    # Tope del multiplicador de backoff (x intervalo base entre runs).
    CRAWLER_BUDGET_BACKOFF_MAX_MULTIPLIER: int = 4

    # Fuentes RESTRINGIDAS (jobs.ch/jobup.ch, LinkedIn, Indeed, Glassdoor, XING).
    # Vacío = conector deshabilitado (auth_missing, 0 peticiones). NO scraping
    # público: solo se activan con credencial de partner/API autorizada. Ver a.txt §6-7.
    JOBCLOUD_PARTNER_API_KEY: str = ""
    LINKEDIN_PARTNER_TOKEN: str = ""
    INDEED_PARTNER_KEY: str = ""
    GLASSDOOR_PARTNER_KEY: str = ""
    XING_PARTNER_TOKEN: str = ""

    # Provider API Keys (empty = provider disabled)
    JSEARCH_RAPIDAPI_KEY: str = ""
    ADZUNA_APP_ID: str = ""
    ADZUNA_APP_KEY: str = ""
    JOOBLE_API_KEY: str = ""
    CAREERJET_AFFID: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
