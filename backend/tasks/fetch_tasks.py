"""Celery task: fetch jobs from all providers, normalize, dedup, and store."""

import asyncio
import logging
from typing import Any

from celery_app import celery_app
from config import settings
from database import task_session
from models.source_health import OUTCOME_EMPTY, OUTCOME_ERROR
from providers import get_all_providers
from services import source_health
from services.data_normalizer import DataNormalizer
from services.deduplicator import Deduplicator
from services.job_repository import JobRepository
from utils import fetch_diagnostics as diag
from utils.fetch_diagnostics import KIND_NETWORK, FetchIssue

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Filtro global de títulos tech — aplicado a TODOS los providers antes de
# guardar en DB. Evita que ofertas de ingeniería de software contaminen los
# matches de un perfil no técnico (Content Editor, HR, VA, L&D...).
# ---------------------------------------------------------------------------
_TECH_TITLE_KEYWORDS: frozenset[str] = frozenset(
    {
        # Ingeniería de software
        "software engineer",
        "software developer",
        "software architect",
        "backend engineer",
        "backend developer",
        "frontend engineer",
        "frontend developer",
        "full stack",
        "fullstack",
        "full-stack",
        # DevOps / Cloud / Infra
        "devops",
        "sre",
        "site reliability",
        "cloud engineer",
        "cloud architect",
        "infrastructure engineer",
        "platform engineer",
        "systems engineer",
        "network engineer",
        "network administrator",
        # Datos / ML / IA técnica
        "data engineer",
        "ml engineer",
        "machine learning engineer",
        "ai research engineer",
        "ai engineer",
        "data scientist",
        "data architect",
        "deep learning",
        "computer vision",
        "nlp engineer",
        # Móvil / Embebido
        "mobile developer",
        "ios developer",
        "android developer",
        "react native",
        "flutter developer",
        "embedded",
        "firmware",
        # Seguridad
        "cybersecurity",
        "security engineer",
        "penetration tester",
        "infosec",
        "devsecops",
        # Blockchain / Web3
        "blockchain",
        "smart contract",
        "web3 developer",
        "solidity",
        # Herramientas tech específicas
        "kubernetes",
        "terraform",
        "ansible",
        # Sanidad y enfermería — fuera del perfil
        "pflegefachperson",
        "pflegefachfrau",
        "pflegefachmann",
        "krankenpfleger",
        "krankenschwester",
        "physiotherap",
        "ergotherap",
        "logopäd",
        "psychiatriepflege",
        # Construcción y oficios — fuera del perfil
        "maurer",
        "zimmermann",
        "elektriker",
        "kaminbaumonteur",
        "sanitärmonteur",
        "metallbau",
        "tiefbau",
        "hochbau",
        "bauführer",
        "polier",
        "installateur",
        # Hostelería operativa — fuera del perfil
        "hauswirtschaft",
        "reinigungskraft",
        "küchenhilfe",
    }
)


def _is_tech_job(title: str) -> bool:
    """Devuelve True si el título corresponde a un rol puramente técnico."""
    title_lower = title.lower()
    return any(kw in title_lower for kw in _TECH_TITLE_KEYWORDS)


@celery_app.task(
    name="tasks.fetch_providers",
    bind=True,
    max_retries=2,
    soft_time_limit=540,
    time_limit=600,
)
def fetch_providers(self) -> dict[str, Any]:
    """Fetch jobs from all enabled providers, normalize, dedup, and store.

    This is the main data ingestion pipeline, dispatched by APScheduler.
    Celery tasks must be synchronous — async work runs via asyncio.run().
    """
    try:
        result = asyncio.run(_fetch_providers_async())

        # Chain: generate embeddings for newly ingested jobs.
        # Cuando la cosecha diaria está activa, la cadena (daily_harvest) ya
        # encadena embed_all_pending → dedup → matching, así que aquí NO se
        # auto-encadena (evita doble trabajo y carreras sobre los mismos jobs).
        if result.get("new", 0) > 0 and not settings.SCHEDULER_DAILY_HARVEST_ENABLED:
            from tasks.embedding_tasks import generate_job_embeddings

            generate_job_embeddings.delay(batch_size=100)
            logger.info(
                "Dispatched generate_job_embeddings for %d new jobs", result["new"]
            )

        return result
    except Exception as exc:
        logger.error("fetch_providers failed: %s", exc)
        raise self.retry(exc=exc, countdown=300)


async def _fetch_providers_async() -> dict[str, Any]:
    """Async implementation of the fetch pipeline.

    Phase 1: Parallel HTTP fetch with semaphore (TD-18).
    Phase 2: Sequential DB persist (savepoints can't interleave on same session).
    """
    providers = get_all_providers()
    summary: dict[str, Any] = {
        "providers": 0,
        "fetched": 0,
        "new": 0,
        "updated": 0,
        "dupes": 0,
        "errors": 0,
        # V.0 — un fetch fallido ya NO se confunde con una fuente vacía:
        # `fetch_failed` = no trajo nada Y hubo fallo de descarga;
        # `empty` = respondió sin ofertas, que es legítimo.
        "fetch_failed": 0,
        "empty": 0,
        "unhealthy": [],
    }

    # Phase 1: parallel fetch
    sem = asyncio.Semaphore(settings.FETCH_CONCURRENCY)

    async def _fetch_one(provider):
        """Descarga una fuente y emite su VEREDICTO, no solo sus ofertas.

        `begin()` va dentro de la tarea (no fuera): cada tarea asyncio hereda
        su propia copia del contexto, así que los fetches concurrentes no se
        pisan los diagnósticos entre sí.
        """
        source = provider.get_source_name()
        async with sem:
            diag.begin()
            try:
                jobs = await provider.fetch_jobs("", "Switzerland")
            except Exception as e:
                logger.error("Provider %s fetch failed: %s", source, e)
                return (
                    source,
                    None,
                    OUTCOME_ERROR,
                    [
                        FetchIssue(
                            KIND_NETWORK, url="", detail=f"{type(e).__name__}: {e}"
                        )
                    ],
                )

            collected = diag.issues()
            outcome = diag.classify(len(jobs), collected)
            if outcome == OUTCOME_ERROR:
                # ANTES esto se registraba como "returned 0 jobs" y se contaba
                # como éxito: el fallo sistémico que arregla V.0.
                logger.error(
                    "Provider %s SIN DATOS por fallo de descarga: %s",
                    source,
                    "; ".join(i.describe() for i in collected),
                )
            else:
                logger.info("Provider %s returned %d jobs", source, len(jobs))
            return source, jobs, outcome, collected

    fetch_results = await asyncio.gather(*[_fetch_one(p) for p in providers])

    # Phase 2: sequential DB persist
    async with task_session() as db:
        repo = JobRepository(db)

        for source, jobs, outcome, collected in fetch_results:
            # La salud se registra SIEMPRE, incluso si la descarga falló: es
            # justamente el caso que antes no dejaba rastro.
            motivo = await source_health.record_and_alert(
                db, source, outcome, len(jobs or []), collected
            )
            if motivo:
                summary["unhealthy"].append(f"{source}: {motivo}")

            if outcome == OUTCOME_ERROR:
                summary["fetch_failed"] += 1
            elif outcome == OUTCOME_EMPTY:
                summary["empty"] += 1

            if jobs is None:
                summary["errors"] += 1
                continue

            # VD.3 — ofertas que completan su savepoint sin excepción: es la
            # señal de PERSISTENCIA para source_health.
            stored_count = 0
            # `attempted_count` para la señal de persistencia = ofertas que se
            # INTENTARON guardar: las descartadas por el filtro tech nunca
            # entran en el camino del savepoint y contarlas produciría falsos
            # "FUENTE DEGRADADA" en providers cuyo lote entero se filtra.
            attempted_count = 0
            try:
                for job in jobs:
                    # Descartar empleos tech antes de normalizar o guardar en DB
                    if _is_tech_job(job.get("title", "")):
                        continue

                    attempted_count += 1
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

                        stored_count += 1

                    except Exception as e:
                        summary["errors"] += 1
                        logger.error("Error processing job from %s: %s", source, e)

                await db.commit()

                # VD.3 — señal de persistencia, DESPUÉS del commit del lote a
                # propósito: `record_storage` usa su propia transacción acotada
                # y no arrastra el lote del provider.
                motivo = await source_health.record_storage(
                    db, source, attempted_count, stored_count
                )
                if motivo:
                    summary["unhealthy"].append(f"{source}: {motivo}")

                summary["providers"] += 1

            except Exception as e:
                await db.rollback()
                summary["errors"] += 1
                logger.error("Provider %s persist failed: %s", source, e)
                # Perder el LOTE entero (commit fallido) también es señal de
                # persistencia: sin esto la racha quedaba congelada y el fallo
                # se presentaba como éxito un nivel más arriba. `record_storage`
                # degrada a None ante fallos ordinarios, pero si la BD está
                # caída (causa probable de estar aquí) su propio rollback
                # también puede lanzar — y una excepción escapando del
                # manejador mataría el bucle de las fuentes restantes y
                # dispararía el retry de Celery (re-descarga del lote entero).
                # Se aísla del todo. Si `attempted_count` es 0 no toca la
                # racha, que es justo lo correcto.
                try:
                    motivo = await source_health.record_storage(
                        db, source, attempted_count, 0
                    )
                except Exception as health_err:  # noqa: BLE001 — no empeorar el error
                    motivo = None
                    logger.error(
                        "No se pudo registrar la persistencia de %s en el "
                        "camino de error: %s",
                        source,
                        health_err,
                    )
                if motivo:
                    summary["unhealthy"].append(f"{source}: {motivo}")

    logger.info("Fetch complete: %s", summary)
    return summary
