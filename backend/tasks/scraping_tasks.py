"""Celery task: fetch jobs from all scrapers, normalize, dedup, and store.

Follows the same per-job savepoint pipeline as fetch_tasks.py but runs
on a separate schedule (every 6h vs 30min for API providers).
"""

import asyncio
import logging
from typing import Any

from celery_app import celery_app
from config import settings
from database import task_session
from models.source_health import OUTCOME_ERROR
from scrapers import get_all_scrapers
from services import source_health
from services.crawler_budget import CrawlerBudgetService
from services.cursor_store import CursorStore
from services.data_normalizer import DataNormalizer
from services.deduplicator import Deduplicator
from services.job_repository import JobRepository
from utils import fetch_diagnostics as diag

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.scraping.fetch_scrapers",
    bind=True,
    max_retries=1,
    soft_time_limit=1800,
    time_limit=2400,
)
def fetch_scrapers(self) -> dict[str, Any]:
    """Fetch jobs from all enabled scrapers."""
    try:
        result = asyncio.run(_fetch_scrapers_async())

        # Ver nota en fetch_tasks: con la cosecha diaria activa, la cadena
        # daily_harvest ya cubre embeddings/dedup/matching.
        if result.get("new", 0) > 0 and not settings.SCHEDULER_DAILY_HARVEST_ENABLED:
            from tasks.embedding_tasks import generate_job_embeddings

            generate_job_embeddings.delay(batch_size=100)

        return result
    except Exception as exc:
        logger.error("fetch_scrapers failed: %s", exc)
        raise self.retry(exc=exc, countdown=600)


async def _fetch_scrapers_async() -> dict[str, Any]:
    """Async implementation — sequential scraper execution.

    Incremental: por cada scraper carga su cursor, inyecta las identidades ya
    vistas (`_known_urls`) para que el scraper deje de paginar en cuanto llega a
    contenido conocido (early-stop), y tras el run actualiza el cursor. Así el nº
    de páginas pedidas depende de las ofertas NUEVAS, no del total del portal.
    """
    import math

    scrapers = get_all_scrapers()
    store = CursorStore() if settings.CURSOR_INCREMENTAL_ENABLED else None
    # Presupuesto explícito: solo tiene sentido con cursores (usa su historial).
    budget_on = store is not None and settings.CRAWLER_BUDGET_ENABLED
    # Intervalo base entre runs: la cosecha diaria corre 1 vez/día; en modo
    # intervalos, cada SCHEDULER_SCRAPER_INTERVAL_HOURS.
    base_interval_hours = (
        24.0
        if settings.SCHEDULER_DAILY_HARVEST_ENABLED
        else float(settings.SCHEDULER_SCRAPER_INTERVAL_HOURS)
    )
    summary: dict[str, Any] = {
        "scrapers": 0,
        "skipped": 0,
        "fetched": 0,
        "new": 0,
        "updated": 0,
        "dupes": 0,
        "errors": 0,
        # V.0 — `fetch_failed`: no trajo nada Y hubo fallo de descarga.
        # Distinto de `skipped` (backoff del presupuesto) y de 0 ofertas por
        # early-stop incremental, que son resultados legítimos.
        "fetch_failed": 0,
        "unhealthy": [],
    }

    async with task_session() as db:
        repo = JobRepository(db)

        for scraper in scrapers:
            source = scraper.get_source_name()
            cursor = None
            # `None` hasta que la descarga responde: el except externo lo usa
            # para saber si hubo descargas que registrar como no-guardadas.
            jobs = None
            try:
                if store is not None:
                    cursor = await store.load(db, source)
                    scraper._known_urls = store.known_identities(cursor)

                if budget_on and cursor is not None:
                    # Backoff: fuente sin novedades N runs seguidos → saltar el
                    # run hasta cumplir el intervalo ampliado (0 peticiones).
                    if not CrawlerBudgetService.should_run(
                        cursor,
                        base_interval_hours,
                        exempt_from_backoff=getattr(scraper, "WATCHLIST_SOURCE", False),
                    ):
                        summary["skipped"] += 1
                        logger.info(
                            "%s saltado por presupuesto: %d runs sin novedades",
                            source,
                            cursor.consecutive_empty_runs,
                        )
                        continue
                    # Tope de páginas del run según las novedades medias.
                    scraper._max_pages_this_run = (
                        CrawlerBudgetService.max_pages_this_run(
                            cursor, scraper.PAGE_SIZE, scraper.MAX_PAGES
                        )
                    )

                diag.begin()
                jobs = await scraper.fetch_jobs("", "Switzerland")

                # V.0 — veredicto explícito: un 404/403 ya NO se confunde con
                # "el portal no tiene ofertas nuevas". Aquí importa el doble,
                # porque el early-stop incremental hace que 0 ofertas sea un
                # resultado NORMAL y por eso un fallo pasaba aún más inadvertido.
                collected = diag.issues()
                outcome = diag.classify(len(jobs), collected)
                motivo = await source_health.record_and_alert(
                    db, source, outcome, len(jobs), collected
                )
                if motivo:
                    summary["unhealthy"].append(f"{source}: {motivo}")
                if outcome == OUTCOME_ERROR:
                    summary["fetch_failed"] += 1
                    logger.error(
                        "Scraper %s SIN DATOS por fallo de descarga: %s",
                        source,
                        "; ".join(i.describe() for i in collected),
                    )
                else:
                    logger.info("Scraper %s returned %d jobs", source, len(jobs))

                new_before = summary["new"]
                # VD.2 — el cursor solo aprende identidades REALMENTE
                # persistidas: si aprendiera todo lo descargado, un fallo de
                # guardado lo envenenaría para siempre (el early-stop daría
                # esas URLs por conocidas y la fuente quedaría muda).
                stored_identities: list[str] = []
                # VD.3 — ofertas que completan su savepoint sin excepción:
                # es la señal de PERSISTENCIA para source_health.
                stored_count = 0

                for job in jobs:
                    # Identidad sobre el job CRUDO: `normalize` reasigna `job`
                    # dentro del savepoint y la perdería.
                    identity = scraper.job_identity(job)
                    try:
                        async with db.begin_nested():
                            job = DataNormalizer.normalize(job)
                            job["fuzzy_hash"] = Deduplicator.compute_fuzzy_hash(
                                job["title"], job["company"]
                            )
                            is_new = await repo.upsert_job(job)

                            if is_new:
                                canonical = await Deduplicator.find_fuzzy_duplicate(
                                    db, job["fuzzy_hash"], job["source"]
                                )
                                if canonical:
                                    await repo.mark_duplicate(job["hash"], canonical)
                                    summary["dupes"] += 1
                                else:
                                    summary["new"] += 1
                            else:
                                summary["updated"] += 1

                            summary["fetched"] += 1

                        # El savepoint se completó sin excepción: SOLO ahora la
                        # identidad puede entrar en el cursor (VD.2).
                        stored_identities.append(identity)
                        stored_count += 1

                    except Exception as e:
                        summary["errors"] += 1
                        logger.error(
                            "Error processing scraped job from %s: %s",
                            source,
                            e,
                        )

                if store is not None and cursor is not None:
                    # `pages_read` mide esfuerzo de CRAWL (lo descargado), no
                    # persistencia: sigue calculándose sobre `jobs`.
                    pages_read = max(
                        1, math.ceil(len(jobs) / max(scraper.PAGE_SIZE, 1))
                    )
                    store.update_after_run(
                        cursor,
                        stored_identities,
                        new_count=summary["new"] - new_before,
                        pages_read=pages_read,
                    )

                await db.commit()

                # VD.3 — señal de persistencia, DESPUÉS del commit a propósito:
                # `record_storage` usa su propia transacción acotada y no
                # arrastra ni el lote de ofertas ni el cursor.
                motivo = await source_health.record_storage(
                    db, source, len(jobs), stored_count
                )
                if motivo:
                    summary["unhealthy"].append(f"{source}: {motivo}")

                summary["scrapers"] += 1

            except Exception as e:
                await db.rollback()
                summary["errors"] += 1
                logger.error("Scraper %s failed: %s", source, e)
                # Perder el LOTE entero (commit o cursor fallidos) también es
                # señal de persistencia: sin esto la racha quedaba congelada y
                # el fallo se presentaba como éxito un nivel más arriba. Solo
                # si hubo descarga (`jobs` asignado). `record_storage` degrada
                # a None ante fallos ordinarios, pero si la BD está caída
                # (causa probable de estar aquí) su propio rollback también
                # puede lanzar — y una excepción escapando del manejador
                # mataría el bucle de las fuentes restantes y dispararía el
                # retry de Celery (re-descarga del lote entero). Se aísla.
                if jobs is not None:
                    try:
                        motivo = await source_health.record_storage(
                            db, source, len(jobs), 0
                        )
                    except Exception as health_err:  # noqa: BLE001 — no empeorar
                        motivo = None
                        logger.error(
                            "No se pudo registrar la persistencia de %s en el "
                            "camino de error: %s",
                            source,
                            health_err,
                        )
                    if motivo:
                        summary["unhealthy"].append(f"{source}: {motivo}")

    logger.info("Scraper fetch complete: %s", summary)
    return summary
