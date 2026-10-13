"""Celery orchestrator for SwissJob-owned harvesting."""

import logging
from typing import Any

from celery import chain

from celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.pipeline.harvest_chain_failed")
def harvest_chain_failed(request, exc, traceback) -> None:
    task_name = getattr(request, "task", "?")
    logger.error(
        "Cosecha diaria: el eslabón %s FALLÓ (%r); los restantes no corren",
        task_name,
        exc,
    )
    try:
        from config import settings
        from services.email_service import EmailService

        email = EmailService()
        if settings.TEACHER_ALERT_EMAIL and email.is_available:
            email.send(
                settings.TEACHER_ALERT_EMAIL,
                "[SwissJobHunter] Cosecha diaria INTERRUMPIDA",
                f"El eslabón {task_name} falló: {exc!r}",
            )
    except Exception:
        logger.exception("Cosecha diaria: fallo enviando el email de salud")


@celery_app.task(name="tasks.pipeline.daily_harvest", bind=True, max_retries=0)
def daily_harvest(self) -> dict[str, Any]:
    """Run the two retained producer families in a deterministic order."""
    from tasks.fetch_tasks import fetch_providers
    from tasks.scraping_tasks import fetch_scrapers

    result = chain(fetch_providers.si(), fetch_scrapers.si()).apply_async(
        link_error=harvest_chain_failed.s()
    )
    logger.info("Cosecha diaria: cadena despachada (id=%s)", result.id)
    return {"status": "dispatched", "chain_id": result.id}
