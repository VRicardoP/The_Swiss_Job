"""Celery tasks: maintenance operations (dedup, URL health, cleanup)."""

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

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
# Concurrencia POR HOST (V.4): con 3000 sondas diarias y un corpus donde
# arbeitnow+ostjob son el 56%, el límite global no basta — un solo portal
# recibiría ~1200 peticiones a ráfagas de 10 y podría bloquearnos, que es
# justo el fallo que estamos arreglando en otras fuentes (proz/zebis dan 403).
_URL_CHECK_HOST_CONCURRENCY = 2
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
        # Un semáforo por host, creado perezosamente. Acota las peticiones
        # SIMULTÁNEAS a un mismo portal sin frenar el barrido global.
        host_sems: dict[str, asyncio.Semaphore] = {}

        def _host_sem(url: str) -> asyncio.Semaphore:
            host = urlparse(url).netloc or "?"
            if host not in host_sems:
                host_sems[host] = asyncio.Semaphore(_URL_CHECK_HOST_CONCURRENCY)
            return host_sems[host]

        async def _probe(client: httpx.AsyncClient, job_hash: str, url: str):
            async with sem, _host_sem(url):
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
    """Archiva o elimina ofertas caducadas (no vistas en `max_age_days`).

    Con adjuntos del usuario → se ARCHIVA (is_active=False); sin adjuntos → se
    BORRA. Ver _cleanup_stale_jobs_async para el detalle de la política.
    """
    try:
        return asyncio.run(_cleanup_stale_jobs_async(max_age_days))
    except Exception as exc:
        logger.error("cleanup_stale_jobs failed: %s", exc)
        return {"status": "error", "error": str(exc)}


async def _cleanup_stale_jobs_async(max_age_days: int) -> dict[str, Any]:
    """Async: archiva o borra las ofertas caducadas según tengan adjuntos del usuario.

    Caducada = no vista en el feed en `max_age_days` (last_seen_at). Política:
    - CON adjuntos del usuario (candidatura, documento generado, o match con
      feedback/borrador/estado avanzado) → se ARCHIVA (is_active=False): se oculta
      del board y del matching, pero la fila sobrevive, conservando el contexto de
      la candidatura/documento y permitiendo el re-enlace si la oferta reaparece
      (mismo hash). NUNCA se borra una oferta con datos del usuario (evita que la
      cascada de la FK destruya candidaturas, documentos o el match) (PF.3).
    - SIN adjuntos → se BORRA.
    """
    from sqlalchemy import text

    from database import task_session

    # Una oferta tiene "adjuntos" si el usuario interactuó de forma NO recomputable:
    # candidatura, documento generado, o match con feedback / borrador de carta /
    # estado de candidatura avanzado (más allá del inicial "detected").
    attached = """
        hash IN (SELECT job_hash FROM job_applications)
        OR hash IN (SELECT job_hash FROM generated_documents)
        OR hash IN (
            SELECT job_hash FROM match_results
            WHERE feedback IS NOT NULL
               OR feedback_implicit IS NOT NULL
               OR draft_letter IS NOT NULL
               OR application_status <> 'detected'
        )
    """
    stale = "last_seen_at < NOW() - make_interval(days => :days)"

    async with task_session() as db:
        # Promover a canónicos los duplicados cuya canónica va a borrarse
        # (A1-1): duplicate_of no tiene FK, así que el DELETE los dejaría
        # apuntando a un hash inexistente y el upsert los mantendría inactivos
        # para siempre (is_active = CASE(duplicate_of IS NOT NULL -> False))
        # aunque la oferta se siga publicando. El siguiente ciclo de dedup los
        # re-agrupa si procede. MISMA transacción y ANTES del
        # archive y del DELETE: así un promovido que además esté caducado con
        # adjuntos lo recaptura el archivado en este mismo ciclo.
        r_promoted = await db.execute(
            text(  # noqa: S608 — idem
                f"UPDATE jobs SET duplicate_of = NULL, is_active = TRUE "
                f"WHERE duplicate_of IN ("
                f"SELECT hash FROM jobs WHERE {stale} AND NOT ({attached})"
                f")"
            ),
            {"days": max_age_days},
        )
        # Archivar (no borrar) las caducadas CON adjuntos que sigan activas.
        r_archived = await db.execute(
            text(  # noqa: S608 — `stale`/`attached` son SQL estático de confianza
                f"UPDATE jobs SET is_active = FALSE "
                f"WHERE {stale} AND is_active = TRUE AND ({attached})"
            ),
            {"days": max_age_days},
        )
        # Borrar las caducadas SIN adjuntos.
        r_deleted = await db.execute(
            text(  # noqa: S608 — idem
                f"DELETE FROM jobs WHERE {stale} AND NOT ({attached})"
            ),
            {"days": max_age_days},
        )
        await db.commit()

    archived = r_archived.rowcount or 0
    promoted = r_promoted.rowcount or 0
    deleted = r_deleted.rowcount or 0
    logger.info(
        "cleanup_stale_jobs: %d archivadas (con adjuntos), %d duplicados "
        "promovidos a canónicos, %d borradas (sin adjuntos)",
        archived,
        promoted,
        deleted,
    )
    return {
        "status": "success",
        "archived": archived,
        "promoted": promoted,
        "deleted": deleted,
        "max_age_days": max_age_days,
    }
