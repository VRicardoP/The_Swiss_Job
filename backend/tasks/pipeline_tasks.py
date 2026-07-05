"""Celery: orquestador de la cosecha diaria autónoma.

Encadena, en orden y sin intervención del usuario:

    fetch_providers → fetch_scrapers → embed_all_pending
      → dedup_semantic_batch → run_all_matches

Cada eslabón usa una firma inmutable (`.si()`): el siguiente arranca solo cuando
el anterior TERMINA, así el matching corre con los embeddings ya generados. La
alerta de colegios suizos (tasks.alert_tasks.detect_teacher_alerts) corre en su
propio schedule cada N horas — no necesita ir en la cadena, ya que solo depende
de la ingesta (categoría H asignada en normalización) y usa su propia marca de
agua para no reenviar.

El scheduler dispara esta tarea UNA vez al día a hora variable (patrón circadiano,
ver a.txt §5/§10 y config.SCHEDULER_DAILY_HARVEST_*).
"""

import logging
from typing import Any

from celery import chain

from celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.pipeline.daily_harvest", bind=True, max_retries=0)
def daily_harvest(self) -> dict[str, Any]:
    """Lanza la cadena secuencial de extracción + matching diario."""
    # Import diferido: evita ciclos de import entre módulos de tareas al cargar.
    from tasks.embedding_tasks import embed_all_pending
    from tasks.fetch_tasks import fetch_providers
    from tasks.maintenance_tasks import dedup_semantic_batch
    from tasks.matching_tasks import run_all_matches
    from tasks.scraping_tasks import fetch_scrapers

    workflow = chain(
        fetch_providers.si(),
        fetch_scrapers.si(),
        embed_all_pending.si(batch_size=200),
        dedup_semantic_batch.si(batch_size=500),
        run_all_matches.si(),
    )
    result = workflow.apply_async()
    logger.info("Cosecha diaria: cadena despachada (id=%s)", result.id)
    return {"status": "dispatched", "chain_id": result.id}
