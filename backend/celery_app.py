import os

from celery import Celery
from celery.signals import after_setup_logger, after_setup_task_logger

from config import settings
from logging_setup import install_credential_redaction

# G6/P2-2 — el worker NO llama a `configure_logging` (quien monta su logging es
# Celery), y era justo el worker el que publicaba la `GEMINI_API_KEY` real en 32
# líneas del journal vía el INFO de httpx. Estas señales corren DESPUÉS de que
# Celery ponga sus handlers, así que el filtro de redacción aterriza sobre ellos.
after_setup_logger.connect(install_credential_redaction, weak=False)
after_setup_task_logger.connect(install_credential_redaction, weak=False)

celery_app = Celery(
    "swissjobhunter",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Zurich",
    enable_utc=True,
    task_routes={
        "tasks.scraping.*": {"queue": "scraping"},
        "tasks.ai.*": {"queue": "ai"},
    },
    task_default_queue="default",
    # Task safety (TD-19)
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=300,
    task_time_limit=360,
    # Recicla el hijo prefork cada 200 tareas (anti-fuga). worker-ai lo desactiva
    # con CELERY_MAX_TASKS_PER_CHILD=0 (→ None) para NO recargar el modelo de
    # embeddings; el worker general/scraping mantiene el default 200.
    worker_max_tasks_per_child=int(os.getenv("CELERY_MAX_TASKS_PER_CHILD", "200"))
    or None,
)

celery_app.conf.include = [
    "tasks.example_task",
    "tasks.fetch_tasks",
    "tasks.maintenance_tasks",
    "tasks.embedding_tasks",
    "tasks.search_tasks",
    "tasks.scraping_tasks",
    "tasks.watchlist_tasks",
    "tasks.alert_tasks",
    "tasks.matching_tasks",
    "tasks.pipeline_tasks",
    "tasks.profile_tasks",
    "tasks.digest_tasks",
]
