from urllib.parse import quote

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Enable only on SwissJob copies sharing the swissjob-shadow identity.
    CORE_ERASURE_REPLICA_ID: str = Field(
        default="", pattern="^(|swissjob-live|swissjob-cdc)$"
    )

    # PostgreSQL
    DATABASE_URL: str = (
        "postgresql+asyncpg://swissjob:swissjob_dev_2024@postgres:5432/swissjobhunter"
    )

    # Redis
    # C7: `redis` dejó de aceptar conexiones anónimas. La contraseña vive UNA
    # vez aquí y se inyecta en las tres URLs que no traigan credencial; repetirla
    # en REDIS_URL + CELERY_BROKER_URL + CELERY_RESULT_BACKEND garantizaba que
    # una de las tres se quedase atrás en la siguiente rotación.
    REDIS_PASSWORD: str = ""
    REDIS_URL: str = "redis://redis:6379/0"

    # Celery
    CELERY_BROKER_URL: str = "redis://redis:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/2"

    @model_validator(mode="after")
    def _inyecta_password_de_redis(self) -> "Settings":
        """Añade REDIS_PASSWORD a las URLs de redis que no traigan credencial.

        Una URL que YA trae credencial (`redis://user:pass@…`) se respeta tal
        cual: quien la escribió explícitamente manda sobre este atajo.
        """
        if not self.REDIS_PASSWORD:
            return self
        credencial = quote(self.REDIS_PASSWORD, safe="")
        for campo in ("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
            url = getattr(self, campo)
            for esquema in ("redis://", "rediss://"):
                if url.startswith(esquema) and "@" not in url:
                    setattr(
                        self, campo, f"{esquema}:{credencial}@{url[len(esquema) :]}"
                    )
                    break
        return self

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

    # Punto 5 (§10.3-ter): recorrer el feed en segundo plano al cambiar la
    # versión, para que la primera carga no la pague el usuario. En el NAS ese
    # recorrido eran 11 s. Corre en CADA worker: la caché del recorrido vive en
    # proceso, así que calentar en uno no sirve al otro.
    FEED_WARMUP_ENABLED: bool = True
    FEED_WARMUP_INTERVAL_SECONDS: int = 60
    # A19-15 §D: traducir los títulos del feed EN SEGUNDO PLANO (dentro del
    # calentamiento), para que la pantalla principal (translate=false) los lea de
    # Redis sin pagar un LLM por petición. Cota por pasada: son llamadas a Groq.
    TRANSLATION_WARMUP_ENABLED: bool = True
    TRANSLATION_WARMUP_MAX_PER_PASS: int = Field(default=100, ge=0)

    # App
    SECRET_KEY: str = "change-me-in-production"
    # C7: el backend legacy no tiene noción de entorno (el core sí: CORE_ENV).
    # Sin ella, una guardia de credenciales de dev no puede distinguir tu portátil
    # de un servidor. Este permiso es esa distinción, y es explícita: ausente ⇒
    # el arranque muere si la contraseña de la base es de dev o una plantilla.
    # NO aparece en .env.prod.example a propósito.
    ALLOW_DEV_CREDENTIALS: bool = False
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
    # G4/P3-6 — segunda condición del dedup semántico: Jaccard mínimo de los
    # tokens del título normalizado. 0.3 = «al menos un tercio del léxico
    # combinado es común». Era constante de módulo, y eso dejaba
    # SEMANTIC_DEDUP_THRESHOLD sin efecto como mando de remediación: bajar el
    # umbral del coseno no servía de nada porque la puerta léxica seguía
    # rechazando (medido: 20/20 pares reales de la MISMA vacante en DE↔FR/IT/EN
    # rechazados, 15 con solape exactamente 0.000).
    SEMANTIC_DEDUP_TITLE_OVERLAP_MIN: float = 0.3

    # Groq LLM
    GROQ_API_KEY: str = ""
    # Modelo pesado — generación de cartas/documentos (calidad > volumen).
    # llama-3.3-70b-versatile queda DECOMISIONADO por Groq el 2026-08-16 → migrado a
    # gpt-oss-120b (reemplazo recomendado por Groq). Es un modelo de razonamiento pero
    # su cadena de pensamiento va en un campo `reasoning` aparte: `.content` sale limpio.
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    # Modelo rápido — traducción de títulos + re-ranking Stage 3 (alto volumen).
    # Cadena de decomisos de Groq: llama-4-scout (2026-07-17) → qwen3.6-27b →
    # qwen3.8-27b (2026-09-15). qwen3.6-27b ya NO existe en el catálogo:
    # devolvía `model_not_found`, es decir, la traducción de títulos y el
    # re-ranking Stage 3 estaban CAÍDOS sin que nada lo gritara. Comprobar el
    # catálogo vivo (GET /openai/v1/models) antes de dar por bueno un modelo.
    GROQ_RERANK_MODEL: str = "qwen/qwen3.8-27b"
    # qwen3.6 razonaba por defecto y quemaba max_tokens en <think> rompiendo los
    # parsers JSON. qwen3.8 ya NO lo hace (sondeo 2026-09-15: 4 tokens de
    # completion sin el parámetro), pero se mantiene por ser inofensivo y
    # proteger de un futuro modelo que sí razone. Se envía como reasoning_effort
    # SOLO en llamadas con GROQ_RERANK_MODEL; vacío = no enviar el parámetro.
    GROQ_RERANK_REASONING_EFFORT: str = "none"
    GROQ_RERANK_BATCH_SIZE: int = 10
    GROQ_RERANK_TEMPERATURE: float = 0.2
    GROQ_RERANK_MAX_TOKENS: int = 2048
    GROQ_CACHE_TTL_DAYS: int = 7

    # Alerts & Saved Searches
    # G1/P3-28 — rate limiting tras reverse proxy: confiar en X-Forwarded-For
    # SOLO si el proxy la sobreescribe (activar en el despliegue del NAS).
    RATE_LIMIT_TRUST_PROXY: bool = False
    # H5/T8: límite por defecto de TODA la API, no sólo de las 5 rutas con
    # decorador. 240/min = 4/s sostenidos por cliente: la pantalla principal
    # hace 1 petición y una sesión de navegación normal no se acerca, pero un
    # bucle descontrolado sí choca. Medir antes de bajarlo.
    RATE_LIMIT_DEFAULT: str = "240/minute"

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

    # Groq concurrency (TD-22)
    GROQ_CONCURRENCY: int = 2

    # Document generation (CV/carta). Prioriza CALIDAD sobre latencia.
    GROQ_DOC_TEMPERATURE: float = 0.4
    GROQ_DOC_MAX_TOKENS: int = 4096
    GROQ_DOC_CACHE_TTL_HOURS: int = 24
    DOCUMENT_WRITES_FROZEN: bool = False
    SCHOOL_WRITES_FROZEN: bool = False
    # Proveedor PRIMARIO de documentos: Google Gemini (free tier de Google).
    # gpt-oss-120b en Groq free tier topa a 8k tokens/min y falla en documentos
    # largos → Gemini lo evita. Si la key falta o Gemini falla/satura,
    # DocumentGeneratorService cae a Groq (GROQ_MODEL).
    # Nota: los modelos gemma-4-* dan mala salida para esta tarea (repiten el
    # prompt) y ~30s de latencia; por eso el default es Gemini Flash, no Gemma.
    #
    # 2026-09-15: de gemini-2.5-flash a gemini-3.6-flash. Comprobado EN VIVO con
    # la API key gratuita del proyecto (ListModels + generación real): 3.6-flash
    # redacta una carta de 236 palabras en ~8 s, mientras 2.5-flash devuelve
    # 429 RESOURCE_EXHAUSTED de forma intermitente en ese mismo tier. Existen
    # además 3.7 y 3.8 flash, también operativos con esta key.
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.6-flash"
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
    # Sin default personal en el código: el destinatario se fija por entorno
    # (TEACHER_ALERT_EMAIL). Vacío = alerta omitida aunque esté habilitada.
    TEACHER_ALERT_EMAIL: str = ""
    TEACHER_ALERT_INITIAL_LOOKBACK_DAYS: int = (
        7  # primera ejecución mira atrás esta ventana
    )
    SCHEDULER_TEACHER_ALERT_INTERVAL_HOURS: int = 6

    # G3/P1-1 — marcas de agua de las tareas de notificación. `now()` de
    # Postgres es el INICIO de la transacción, así que una cosecha larga
    # persiste ofertas cuyo `first_seen_at` es ANTERIOR a la marca que la
    # tarea guardó mientras esa cosecha seguía abierta: sin solape, ese lote
    # no se notifica jamás. El LAG debe superar la transacción de
    # cosecha/matching más larga (los soft limits actuales son de 30 min en
    # scraping, pero la transacción POR FUENTE es de minutos).
    NOTIFY_WATERMARK_LAG_MINUTES: int = Field(default=15, ge=0)
    # Caducidad del marcador «ya avisado» por elemento, que es lo que evita
    # que el solape reenvíe. Debe ser >> LAG y >> intervalo de la tarea.
    NOTIFY_SENT_MARKER_TTL_DAYS: int = Field(default=14, ge=1)
    # Cota inferior de la PRIMERA corrida de una búsqueda guardada (G3/P3-2):
    # sin ella la corrida de estreno cuenta el corpus activo entero como
    # «nuevo» y notifica cientos de ofertas de hace meses.
    SAVED_SEARCH_INITIAL_LOOKBACK_DAYS: int = Field(default=1, ge=0)

    # DT-148: aquí vivía MAINTENANCE_URL_CHECK_LIMIT (chequeo nocturno de URLs
    # sobre la tabla legacy `jobs`, retirado con la tarea).

    # Core service connection for transferred capabilities.
    # URL interna del /v1 del core (plan §21: puerto dedicado, SOLO red
    # interna de compose, nunca ngrok). El servicio compose es `core-api`.
    CORE_API_BASE_URL: str = "http://core-api:8000/v1"
    # Credencial de consumer emitida por el core: "key_id.secret" (ADR-09).
    # Empty credentials fail closed before any Core request.
    CORE_CONSUMER_KEY: str = ""
    CORE_INBOX_TOKEN: str = ""
    # H9/T12: presupuesto TOTAL del recorrido del feed. CORE_HTTP_TIMEOUT_SECONDS
    # acota cada petición; esto acota la suma. Sin él, un core lento dejaba
    # ocupado el worker de gunicorn hasta que terminara —y sólo hay dos—.
    # Agotarlo da un 503, que el consumidor ya sabe tratar.
    CORE_FEED_TOTAL_BUDGET_S: float = 60.0
    CORE_HTTP_TIMEOUT_SECONDS: float = 5.0
    # Opt-in after aggregate parity has been measured against the legacy UI.
    CORE_MATCH_FACETS_ENABLED: bool = False
    # F writer cutover: enable ONLY after frozen feedback migration/reconciliation.
    # Once enabled there is no fallback to a local feedback writer or saved feed.
    CORE_FEEDBACK_ENABLED: bool = False
    # Activate only after core0049 + enrollment + frozen source reconciliation.
    # False still captures edits transactionally, but never transfers authority.
    CORE_PROFILE_SYNC_ENABLED: bool = False
    FEEDBACK_WRITES_FROZEN: bool = False
    # Freeze CRUD/manual runs AND scheduled search writers during handover.
    SAVED_SEARCH_WRITES_FROZEN: bool = False
    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
