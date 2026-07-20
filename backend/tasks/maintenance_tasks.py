"""Celery tasks: maintenance operations (dedup, URL health, cleanup)."""

import asyncio
import logging
from typing import Any

from celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.dedup_semantic_batch")
def dedup_semantic_batch(batch_size: int = 200) -> dict[str, Any]:
    """Semantic deduplication via embedding cosine similarity.

    Processes active jobs with embeddings, finds duplicates with cosine > 0.95.
    """
    try:
        return asyncio.run(_dedup_semantic_batch_async(batch_size))
    except Exception as exc:
        logger.error("dedup_semantic_batch failed: %s", exc)
        return {"status": "error", "error": str(exc)}


async def _dedup_semantic_batch_async(batch_size: int) -> dict[str, Any]:
    """Async implementation: find and mark semantic duplicates."""
    from sqlalchemy import select

    from config import settings
    from database import task_session
    from models.job import Job
    from services.deduplicator import Deduplicator
    from services.job_repository import JobRepository

    async with task_session() as db:
        # Get active jobs with embeddings that are not already duplicates
        stmt = (
            select(Job)
            .where(
                Job.is_active.is_(True),
                Job.duplicate_of.is_(None),
                Job.embedding.is_not(None),
            )
            .order_by(Job.first_seen_at.desc())
            .limit(batch_size)
        )
        result = await db.execute(stmt)
        jobs = result.scalars().all()

        if not jobs:
            return {"status": "success", "processed": 0, "duplicates_found": 0}

        repo = JobRepository(db)
        dupes_found = 0

        for job in jobs:
            canonical_hashes = await Deduplicator.find_semantic_duplicates(
                db, job, threshold=settings.SEMANTIC_DEDUP_THRESHOLD
            )
            if canonical_hashes:
                await repo.mark_duplicate(job.hash, canonical_hashes[0])
                dupes_found += 1

        await db.commit()

        logger.info(
            "Semantic dedup: processed %d jobs, found %d duplicates",
            len(jobs),
            dupes_found,
        )
        return {
            "status": "success",
            "processed": len(jobs),
            "duplicates_found": dupes_found,
        }


# UA de navegador para el health-check: algunos portales devuelven 403 a clientes
# "bot". Da igual para la decisión (solo desactivamos en 404/410), pero evita ruido.
_URL_CHECK_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_URL_CHECK_CONCURRENCY = 10
_URL_CHECK_TIMEOUT_SECONDS = 10.0
# Solo estos códigos significan "la oferta ya no existe". 403/405/5xx/timeouts NO
# desactivan (pueden ser bloqueos anti-bot o caídas transitorias, no bajas).
_URL_DEAD_STATUSES = frozenset({404, 410})


@celery_app.task(name="tasks.check_job_urls")
def check_job_urls(limit: int | None = None) -> dict[str, Any]:
    """Comprueba con HEAD que las URLs de las ofertas activas siguen vivas.

    Desactiva (is_active=False) solo las que devuelven 404/410. Acotado a
    `limit` ofertas por corrida (las de check más antiguo primero), de modo que
    barre el catálogo por rotación sin martillear ningún portal.
    """
    from config import settings

    effective = limit if limit is not None else settings.MAINTENANCE_URL_CHECK_LIMIT
    try:
        return asyncio.run(_check_job_urls_async(effective))
    except Exception as exc:
        logger.error("check_job_urls failed: %s", exc)
        return {"status": "error", "error": str(exc)}


async def _check_job_urls_async(limit: int) -> dict[str, Any]:
    from datetime import datetime, timezone

    import httpx
    from sqlalchemy import nulls_first, select, update

    from database import task_session
    from models.job import Job

    async with task_session() as db:
        stmt = (
            select(Job.hash, Job.url)
            .where(Job.is_active.is_(True), Job.duplicate_of.is_(None))
            # Prioriza las nunca comprobadas y las de check más antiguo.
            .order_by(nulls_first(Job.url_last_check.asc()))
            .limit(limit)
        )
        rows = (await db.execute(stmt)).all()

        if not rows:
            return {"status": "success", "checked": 0, "deactivated": 0}

        sem = asyncio.Semaphore(_URL_CHECK_CONCURRENCY)
        timeout = httpx.Timeout(_URL_CHECK_TIMEOUT_SECONDS)

        async def _probe(client: httpx.AsyncClient, job_hash: str, url: str):
            async with sem:
                try:
                    resp = await client.head(url, follow_redirects=True)
                    return job_hash, resp.status_code
                except Exception:
                    # Error de red/timeout: desconocido, no lo damos por muerto.
                    return job_hash, None

        async with httpx.AsyncClient(
            headers={"User-Agent": _URL_CHECK_UA}, timeout=timeout
        ) as client:
            results = await asyncio.gather(*(_probe(client, h, u) for h, u in rows))

        now = datetime.now(timezone.utc)
        dead = [h for h, status in results if status in _URL_DEAD_STATUSES]
        probed = [
            h for h, _status in results
        ]  # todas las sondeadas (incluidos errores)

        if dead:
            await db.execute(
                update(Job)
                .where(Job.hash.in_(dead))
                .values(is_active=False, url_last_check=now)
            )
        # Avanza url_last_check en TODAS las sondeadas, también las de error de
        # red/timeout: si no, quedan con url_last_check NULL y el order_by
        # nulls_first las re-selecciona cada semana → inanición de la rotación,
        # el resto del catálogo nunca se comprueba.
        dead_set = set(dead)
        rest = [h for h in probed if h not in dead_set]
        if rest:
            await db.execute(
                update(Job).where(Job.hash.in_(rest)).values(url_last_check=now)
            )
        await db.commit()

    logger.info(
        "check_job_urls: %d sondeadas, %d desactivadas (404/410)",
        len(probed),
        len(dead),
    )
    return {"status": "success", "checked": len(probed), "deactivated": len(dead)}


@celery_app.task(name="tasks.cleanup_stale_jobs")
def cleanup_stale_jobs(max_age_days: int = 60) -> dict[str, Any]:
    """Elimina ofertas de empleo que superan el umbral de antigüedad.

    Política: 60 días desde `last_seen_at` (última vez visto en el feed).
    Las ofertas no vistas en 60 días se consideran caducadas y se eliminan.
    """
    try:
        return asyncio.run(_cleanup_stale_jobs_async(max_age_days))
    except Exception as exc:
        logger.error("cleanup_stale_jobs failed: %s", exc)
        return {"status": "error", "error": str(exc)}


async def _cleanup_stale_jobs_async(max_age_days: int) -> dict[str, Any]:
    """Async: borra jobs caducados según política de retención por categoría.

    Política de retención:
    - Normal (sin interacción): max_age_days (por defecto 60 días)
    - Guardadas como Good (thumbs_up/applied): 90 días desde last_seen_at
    - En pipeline de candidaturas (job_applications): 180 días desde last_seen_at
    """
    from sqlalchemy import text

    from database import task_session

    async with task_session() as db:
        # 1. Borrar jobs normales caducados (excluir los que tienen retención extendida)
        r_normal = await db.execute(
            text("""
                DELETE FROM jobs
                WHERE last_seen_at < NOW() - make_interval(days => :days)
                  AND hash NOT IN (
                      SELECT DISTINCT job_hash FROM match_results
                      WHERE feedback IN ('thumbs_up', 'applied')
                  )
                  AND hash NOT IN (
                      SELECT DISTINCT job_hash FROM job_applications
                  )
            """),
            {"days": max_age_days},
        )

        # 2. Borrar jobs guardados como Good con más de 90 días
        #    (que además no estén en pipeline)
        r_good = await db.execute(
            text("""
                DELETE FROM jobs
                WHERE last_seen_at < NOW() - INTERVAL '90 days'
                  AND hash IN (
                      SELECT DISTINCT job_hash FROM match_results
                      WHERE feedback IN ('thumbs_up', 'applied')
                  )
                  AND hash NOT IN (
                      SELECT DISTINCT job_hash FROM job_applications
                  )
            """),
        )

        # 3. Borrar jobs en pipeline con más de 180 días
        r_pipeline = await db.execute(
            text("""
                DELETE FROM jobs
                WHERE last_seen_at < NOW() - INTERVAL '180 days'
                  AND hash IN (
                      SELECT DISTINCT job_hash FROM job_applications
                  )
            """),
        )

        await db.commit()

    deleted_normal = r_normal.rowcount
    deleted_good = r_good.rowcount
    deleted_pipeline = r_pipeline.rowcount
    total = deleted_normal + deleted_good + deleted_pipeline

    logger.info(
        "cleanup_stale_jobs: %d eliminadas en total "
        "(normales >%dd: %d | good >90d: %d | pipeline >180d: %d)",
        total,
        max_age_days,
        deleted_normal,
        deleted_good,
        deleted_pipeline,
    )
    return {
        "status": "success",
        "deleted_total": total,
        "deleted_normal": deleted_normal,
        "deleted_good": deleted_good,
        "deleted_pipeline": deleted_pipeline,
        "max_age_days": max_age_days,
    }
