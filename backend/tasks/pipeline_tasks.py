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

import asyncio
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

    stages = [
        fetch_providers.si(),
        fetch_scrapers.si(),
        embed_all_pending.si(batch_size=200),
        dedup_semantic_batch.si(batch_size=500),
    ]
    # Gate anti-doble-motor D.1 (§15bis): la cosecha es GLOBAL y se mantiene
    # mientras quede algún perfil legacy, pero si NINGÚN perfil activo es
    # legacy-owned en matching la etapa de matching se omite ENTERA (el core
    # ya ejecuta el suyo; correrla aquí duplicaría resultados y coste LLM).
    if _matching_stage_enabled():
        stages.append(run_all_matches.si())
    else:
        logger.info(
            "Cosecha diaria: etapa de matching OMITIDA — ningún perfil activo "
            "es legacy-owned (matching); el core emite su propio matching"
        )

    workflow = chain(*stages)
    result = workflow.apply_async()
    logger.info("Cosecha diaria: cadena despachada (id=%s)", result.id)
    return {"status": "dispatched", "chain_id": result.id}


def _matching_stage_enabled() -> bool:
    """True si queda algún perfil activo legacy-owned para la etapa de matching."""
    try:
        return asyncio.run(_any_legacy_matching_profile_async())
    except Exception as exc:
        # Default seguro del plan (ausencia de routing => 'local'): ante un
        # fallo consultando el routing se MANTIENE la etapa — mejor un
        # matching duplicado improbable que ningún matching para los legacy.
        logger.warning(
            "Gate de matching: fallo consultando el routing (%s); se mantiene la etapa",
            exc,
        )
        return True


async def _any_legacy_matching_profile_async() -> bool:
    """UNA consulta: ¿existe algún perfil con embedding aún legacy-owned?"""
    from sqlalchemy import select

    from database import task_session
    from models.user_profile import UserProfile
    from services.routing import CAPABILITY_MATCHING, legacy_owned_sql

    async with task_session() as db:
        stmt = (
            select(UserProfile.user_id)
            .where(
                UserProfile.cv_embedding.is_not(None),
                legacy_owned_sql(UserProfile.user_id, CAPABILITY_MATCHING),
            )
            .limit(1)
        )
        return (await db.execute(stmt)).first() is not None
