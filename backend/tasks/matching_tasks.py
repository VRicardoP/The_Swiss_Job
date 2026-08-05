"""Celery: matching automático para todos los perfiles con embedding.

El matching (MatchService.run_matching) solo se disparaba vía API. Esta tarea lo
automatiza para el flujo autónomo diario, sin intervención del usuario.

Coste de IA acotado por diseño: el LLM (Groq, con Gemini de fallback) solo
re-rankea el top-N (MATCH_LLM_RERANK_TOP) por usuario. El resto del pipeline
(pgvector + scoring multi-factor) es local y no consume crédito de API — aunque
entren cientos de ofertas, a la API de IA solo llega el top-N.

Patrón `def task(): asyncio.run(_impl())` (Celery no soporta async nativo).
"""

import asyncio
import logging
from typing import Any

from celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.matching.run_all_matches",
    bind=True,
    max_retries=1,
    soft_time_limit=1200,
    time_limit=1500,
)
def run_all_matches(self) -> dict[str, Any]:
    """Ejecuta el matching para cada perfil de usuario que tenga CV embedding."""
    try:
        return asyncio.run(_run_all_matches_async())
    except Exception as exc:
        logger.error("run_all_matches failed: %s", exc)
        raise self.retry(exc=exc, countdown=300)


async def _run_all_matches_async() -> dict[str, Any]:
    """Corre MatchService.run_matching secuencialmente por cada perfil válido."""
    from sqlalchemy import select

    from database import task_session
    from models.user_profile import UserProfile
    from services.gemini_service import GeminiService
    from services.groq_service import GroqService
    from services.match_service import MatchService
    from services.routing import CAPABILITY_MATCHING, legacy_owns, resolve_modes

    # Un proveedor LLM por corrida; Gemini de fallback ante fallo/caducidad de Groq.
    groq = GroqService()
    gemini = GeminiService()

    summary: dict[str, Any] = {
        "profiles": 0,
        "results": 0,
        "skipped": 0,
        "skipped_routing": 0,
        "errors": 0,
    }

    async with task_session() as db:
        # SOLO el id: el bucle no usa ningun otro campo del perfil, y traer el
        # ORM completo arrastraria el `cv_embedding` (vector de 384) de CADA
        # perfil — incluidos los que el gate va a descartar.
        stmt = select(UserProfile.user_id).where(UserProfile.cv_embedding.is_not(None))
        user_ids = list((await db.execute(stmt)).scalars().all())

        # Gate anti-doble-motor D.1 (plan §15bis): routing resuelto EN BLOQUE
        # (una consulta para los N perfiles) y descarte de los migrados ANTES
        # de cualquier trabajo caro — en core_read/core_primary/rollback_pending
        # el core ejecuta su propio matching y correr aqui lo duplicaria (y
        # gastaria rerank LLM/embeddings para un perfil que ya no es nuestro).
        modes = await resolve_modes(db, CAPABILITY_MATCHING, user_ids)

        service = MatchService(db, groq=groq, gemini=gemini)

        for user_id in user_ids:
            if not legacy_owns(modes[user_id]):
                summary["skipped_routing"] += 1
                continue
            try:
                result = await service.run_matching(user_id)
                summary["profiles"] += 1
                if result.get("status") == "success":
                    summary["results"] += result.get("results_count", 0)
                else:
                    summary["skipped"] += 1
            except Exception as exc:
                summary["errors"] += 1
                logger.error("run_matching failed for user %s: %s", user_id, exc)
                # La sesion es compartida por todos los perfiles: tras un
                # fallo de flush/commit queda invalidada y el siguiente
                # perfil moriria con PendingRollbackError en cascada. El
                # rollback la deja usable para continuar el bucle.
                await db.rollback()

    logger.info("run_all_matches complete: %s", summary)
    return summary
