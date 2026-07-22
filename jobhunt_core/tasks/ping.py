"""Tarea de humo (A-01): prueba el broker dedicado y el enrutado core.*."""

from jobhunt_core.celery_app import celery_app


@celery_app.task(name="jobhunt.ping")
def ping() -> str:
    return "pong"
