"""Celery task: fetch jobs from all providers, normalize, dedup, and store."""

import asyncio
import logging
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded

from celery_app import celery_app
from config import settings
from database import task_session
from models.source_health import OUTCOME_EMPTY, OUTCOME_ERROR
from providers import get_all_providers
from services import harvest_window, source_health
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
        # V.2/ADR-10 rev. J1 — descartes DELIBERADOS de la ventana de cosecha
        # (solo ALTAS; las re-vistas nunca se descartan). Separados a
        # propósito: `window_skipped` (fecha fuera de ventana) es el filtro
        # funcionando; `window_no_date` (política WINDOW sin published_at) es
        # una anomalía del inventario que pide revisión.
        # ⚠ K3 — semántica de `window_skipped` en PROVIDERS: es FLUJO POR
        # RUN, no ofertas únicas. Sin cursor se re-baja el listado entero y
        # la misma oferta fuera de ventana se re-cuenta en cada run hasta que
        # el portal la retire: leído en bruto sobreestima la pérdida real en
        # un factor de ~7 a 23. No revisar N con este número sin corregirlo.
        "window_skipped": 0,
        "window_no_date": 0,
        # G3/P2-10 — True si el run se cortó por el soft time limit: la cosecha
        # es PARCIAL. Con soft=540/hard=600 el margen es de 60 s, así que el
        # aviso hay que atenderlo, no contarlo como un error más.
        "soft_time_limit": False,
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
            except SoftTimeLimitExceeded:
                # G3/P2-10 — el aviso de soft time limit NO es un fallo de esta
                # fuente: tragarlo aquí le colgaba un OUTCOME_ERROR falso (y su
                # alerta de salud) al provider que tuviera la mala suerte de
                # estar descargando, y dejaba correr el resto hasta el SIGKILL
                # del límite duro. Sube a la fase 1 completa, que corta el run.
                raise
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

    try:
        fetch_results = await asyncio.gather(*[_fetch_one(p) for p in providers])
    except SoftTimeLimitExceeded:
        # G3/P2-10 — la fase 1 (descarga en paralelo) es donde se va casi todo
        # el presupuesto de tiempo, así que es el sitio MÁS probable del aviso.
        # Nada se ha persistido todavía (la fase 2 ni ha empezado): se devuelve
        # el summary vacío y la tarea TERMINA, en vez de seguir hasta que el
        # límite duro mate el worker por SIGKILL.
        summary["soft_time_limit"] = True
        logger.warning(
            "fetch_providers: soft time limit durante la descarga — "
            "run abortado sin persistir"
        )
        return summary

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
            # INTENTARON guardar: las descartadas por el filtro tech o por la
            # ventana nunca entran en el camino del savepoint y contarlas
            # produciría falsos "FUENTE DEGRADADA" en providers cuyo lote
            # entero se filtra (F1).
            # L2 — `None` = "el bucle no llegó a arrancar": la pre-pasada de
            # la ventana mete consultas a BD ANTES del bucle (known_hashes,
            # conteo de corpus) y un fallo recurrente ahí perdía el lote
            # entero con 0 intentos registrados — la racha de persistencia no
            # se movía y la fuente jamás se degradaba (el fallo-disfrazado-
            # de-éxito de F1, reabierto). Se pone a 0 justo antes del bucle.
            attempted_count: int | None = None
            # Lote post-filtro tech: el except externo lo usa como talla
            # honesta del lote si el fallo ocurrió antes del bucle.
            batch: list | None = None
            try:
                # Descartar empleos tech antes de normalizar o guardar en DB.
                # Las filtradas tampoco entran en la pre-pasada de la ventana:
                # no aportan fecha ni contadores (comportamiento previo).
                # VD.8 — el filtro tech, como la ventana (J1), solo puede
                # rechazar ALTAS, nunca re-vistas: una oferta YA en `jobs`
                # cuyo título case (o haya pasado a casar) con una keyword
                # tech sigue pasando por el upsert, que es lo que refresca
                # last_seen_at — si se saltara, cleanup_stale_jobs la
                # archivaría a los 60 días aunque siga viva en el portal.
                # UNA consulta por fuente (known_hashes, PK) y solo con los
                # hashes que el filtro descartaría; qué ALTAS entran lo
                # sigue decidiendo el filtro, exactamente como antes.
                tech_titled = [_is_tech_job(job.get("title", "")) for job in jobs]
                tech_hashes = {j["hash"] for j, t in zip(jobs, tech_titled) if t}
                known_tech = (
                    await repo.known_hashes(tech_hashes) if tech_hashes else set()
                )
                batch = [
                    job
                    for job, is_tech in zip(jobs, tech_titled)
                    if not is_tech or job["hash"] in known_tech
                ]

                # V.2/ADR-10 rev. J1 + K5 — ventana de cosecha en pre-pasada:
                # aplica SIEMPRE y solo puede rechazar ALTAS, nunca re-vistas
                # (una oferta ya en `jobs` sigue pasando por el upsert, que es
                # lo que refresca last_seen_at; saltarla acabaría en archivado
                # indebido por cleanup_stale_jobs). Una única consulta por
                # fuente (`known_hashes`) en vez de una por oferta. El
                # descarte es DELIBERADO, antes del savepoint y SIN contar
                # como intento de persistencia (VD.3). OJO al leer
                # `window_skipped` en providers: es FLUJO por run, no ofertas
                # únicas — sin cursor se re-baja el listado entero y la misma
                # oferta se re-cuenta cada run (K3).
                precheck = await harvest_window.precheck_batch(source, batch, repo)
                summary["window_skipped"] += precheck.skipped_by_date
                summary["window_no_date"] += precheck.skipped_no_date
                # K1 — se decide ANTES de los upserts si hay que vigilar la
                # deriva de identidad (el corpus que importa es el previo al
                # run); el veredicto final llega tras el bucle, con los
                # upserts contados.
                watch_drift = await harvest_window.watch_drift(repo, source, precheck)
                # Contadores por-fuente de este run (guardarraíles K1/K2).
                new_before = summary["new"]
                updated_before = summary["updated"]
                dupes_before = summary["dupes"]

                # L2 — el bucle arranca: a partir de aquí 0 intentos significa
                # "todo descartado deliberadamente", no "fallo pre-bucle".
                attempted_count = 0
                for job, verdict in zip(batch, precheck.verdicts):
                    if verdict != harvest_window.ACCEPT:
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

                    except SoftTimeLimitExceeded:
                        # G3/P2-10 — hereda de Exception: el genérico de abajo
                        # contaba el aviso (que se emite UNA sola vez) como un
                        # error más de la oferta y el bucle seguía hasta el
                        # SIGKILL del límite duro. Sube al bucle de fuentes.
                        raise
                    except Exception as e:
                        summary["errors"] += 1
                        logger.error("Error processing job from %s: %s", source, e)

                # V.2 rev. J1 — la ventana descarta datos en CUALQUIER run:
                # rastro por fuente + guardarraíles de fechas (ERROR total /
                # WARNING parcial, K2). Las altas conseguidas = nuevas +
                # duplicados fuzzy (ambas se ingirieron).
                harvest_window.log_window_summary(
                    source,
                    precheck,
                    new_count=(summary["new"] - new_before)
                    + (summary["dupes"] - dupes_before),
                )

                # K1 — deriva de identidad: reconocidas POR MISMO HASH =
                # re-vistas fuera de ventana (precheck) + upserts no-nuevos.
                # Los dupes NO cuentan (cláusula 1 de watch_drift: son
                # cross-source y silenciaban la deriva real).
                if watch_drift:
                    motivo = harvest_window.report_identity_drift(
                        source,
                        precheck,
                        updated_in_upserts=summary["updated"] - updated_before,
                    )
                    if motivo:
                        summary["unhealthy"].append(f"{source}: {motivo}")

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

            except SoftTimeLimitExceeded:
                # G3/P2-10 — presupuesto blando agotado: se descarta el lote en
                # curso (sin commit) y se sale del bucle con lo ya persistido —
                # «cosecha parcial», no «sin cosecha». NO se registra
                # `record_storage(attempted, 0)`: la fuente no falló al
                # guardar, se quedó sin tiempo, y contarlo producía un
                # «FUENTE DEGRADADA» falso a los dos runs lentos.
                await db.rollback()
                summary["soft_time_limit"] = True
                logger.warning(
                    "fetch_providers: soft time limit durante %s — cosecha "
                    "PARCIAL con %d fuentes completadas",
                    source,
                    summary["providers"],
                )
                break
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
                # L2 — pero si el bucle NI ARRANCÓ (fallo en la pre-pasada:
                # known_hashes, conteo de corpus...), 0 mentiría: en ese
                # camino no hubo ningún descarte deliberado y la talla del
                # lote descargado (post-filtro tech) es el valor honesto.
                if attempted_count is None:
                    attempted_count = len(batch) if batch is not None else len(jobs)
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
