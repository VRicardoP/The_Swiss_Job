"""Celery: análisis del CV y autocompletado del perfil (con progreso vía SSE).

Al subir el CV se dispara esta tarea. Extrae campos del perfil con un LLM
(Gemini→Groq, salida en inglés), los guarda, regenera el embedding y emite
eventos `cv_analysis_progress` al canal SSE del usuario (`sse:{user_id}`) para
alimentar una barra de progreso en el frontend.

Patrón `def task(): asyncio.run(_impl())` (Celery no soporta async nativo).
"""

import asyncio
import logging
from typing import Any

from celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.ai.analyze_cv_and_autofill",
    bind=True,
    max_retries=1,
    soft_time_limit=210,
    time_limit=240,
)
def analyze_cv_and_autofill(self, user_id: str) -> dict[str, Any]:
    """Analiza el CV del usuario y autocompleta su perfil, con progreso por SSE."""
    try:
        return asyncio.run(_analyze_and_autofill_async(user_id))
    except Exception as exc:
        logger.error("analyze_cv_and_autofill failed for %s: %s", user_id, exc)
        # Avisar al frontend para que la barra no se quede colgada.
        _publish_error(user_id)
        raise self.retry(exc=exc, countdown=30)


def _publish_error(user_id: str) -> None:
    """Publica un evento de error en el canal SSE (cliente redis SÍNCRONO)."""
    try:
        import json

        import redis

        from config import settings

        r = redis.from_url(settings.REDIS_URL)
        r.publish(
            f"sse:{user_id}",
            json.dumps(
                {
                    "event": "cv_analysis_progress",
                    "data": {
                        "stage": "error",
                        "percent": 100,
                        "message": "CV analysis failed",
                    },
                }
            ),
        )
        r.close()
    except Exception:
        pass


async def _analyze_and_autofill_async(user_id: str) -> dict[str, Any]:
    import json
    import uuid as uuid_mod

    from redis import asyncio as aioredis
    from sqlalchemy import select

    from config import settings
    from database import task_session
    from models.enums import RemotePreference
    from models.user_profile import UserProfile
    from services.cv_analyzer import CVAnalyzer
    from services.gemini_service import GeminiService
    from services.groq_service import GroqService
    from services.job_matcher import JobMatcher

    uid = uuid_mod.UUID(user_id)
    r = aioredis.from_url(settings.REDIS_URL)

    async def progress(stage: str, percent: int, message: str) -> None:
        try:
            await r.publish(
                f"sse:{user_id}",
                json.dumps(
                    {
                        "event": "cv_analysis_progress",
                        "data": {
                            "stage": stage,
                            "percent": percent,
                            "message": message,
                        },
                    }
                ),
            )
        except Exception:
            pass  # el progreso es best-effort; nunca debe tumbar la tarea

    try:
        await progress("start", 5, "Reading your CV…")

        async with task_session() as db:
            p = (
                await db.execute(
                    select(UserProfile).where(UserProfile.user_id == uid)
                )
            ).scalar_one_or_none()
            if p is None or not p.cv_text:
                await progress("error", 100, "No CV found to analyze")
                return {"status": "no_cv"}
            cv_text = p.cv_text

        # --- Extracción con LLM (la parte lenta) ---
        await progress("analyzing", 25, "Analyzing your CV with AI…")
        analyzer = CVAnalyzer(GroqService(), GeminiService())
        fields = await analyzer.extract_fields(cv_text)

        # --- Guardar campos (transacción corta) ---
        await progress("saving", 70, "Completing your profile…")
        async with task_session() as db:
            p = (
                await db.execute(
                    select(UserProfile).where(UserProfile.user_id == uid)
                )
            ).scalar_one()
            if fields.get("title"):
                p.title = fields["title"]
            if fields.get("skills"):
                # Fusiona con las skills que ya extrajo el regex en la subida.
                p.skills = sorted({*(p.skills or []), *fields["skills"]})
            if fields.get("languages"):
                p.languages = fields["languages"]
            if fields.get("locations"):
                p.locations = fields["locations"]
            if fields.get("experience_years") is not None:
                p.experience_years = fields["experience_years"]
            if fields.get("remote_pref"):
                p.remote_pref = RemotePreference(fields["remote_pref"])
            title_for_emb = p.title or ""
            skills_for_emb = list(p.skills or [])
            await db.commit()

        # --- Embedding FUERA de transacción (evita idle_in_transaction si tarda) ---
        await progress("embedding", 88, "Indexing your profile for matching…")
        matcher = JobMatcher()
        combined = " ".join(
            x for x in [title_for_emb, cv_text, " ".join(skills_for_emb)] if x
        )
        emb = await asyncio.to_thread(matcher.encode, combined)
        async with task_session() as db:
            p = (
                await db.execute(
                    select(UserProfile).where(UserProfile.user_id == uid)
                )
            ).scalar_one()
            p.cv_embedding = emb.tolist()
            await db.commit()

        done_msg = (
            "Profile auto-completed from your CV"
            if fields
            else "Profile indexed for matching"
        )
        await progress("done", 100, done_msg)
        logger.info(
            "analyze_cv_and_autofill OK user=%s fields=%s", user_id, sorted(fields)
        )
        return {"status": "success", "fields": sorted(fields.keys())}
    finally:
        await r.aclose()
