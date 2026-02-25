from celery import Celery

from config import settings

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
)

celery_app.conf.include = ["tasks.example_task"]
