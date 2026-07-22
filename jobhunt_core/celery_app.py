"""Celery del core — broker en redis-core (DEDICADO), colas core.* (§15bis).

Aislamiento estructural, no por convención: el worker legacy escucha
default/scraping/ai en el Redis de caché; este app usa OTRO broker y OTRO
namespace de colas. Aunque un worker legacy apuntara por error al broker del
core, ninguna cola coincide.
"""

from celery import Celery

from jobhunt_core.config import settings

celery_app = Celery(
    "jobhunt_core",
    broker=settings.CORE_BROKER_URL,
    backend=settings.CORE_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Zurich",
    enable_utc=True,
    # TODAS las colas del core viven en el namespace core.* (plan §15bis).
    task_default_queue="core.default",
    task_routes={
        "jobhunt.harvest.*": {"queue": "core.harvest"},
        "jobhunt.embedding.*": {"queue": "core.embedding"},
        "jobhunt.matching.*": {"queue": "core.matching"},
        "jobhunt.notifications.*": {"queue": "core.notifications"},
    },
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

celery_app.conf.include = ["jobhunt_core.tasks.ping"]
