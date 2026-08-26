"""Celery task: fetch jobs from all scrapers, normalize, dedup, and store.

Follows the same per-job savepoint pipeline as fetch_tasks.py but runs
on a separate schedule (every 6h vs 30min for API providers).

K3 — el cursor TAMBIÉN aprende las identidades descartadas por FECHA fuera de
ventana (`SKIP_STALE`). La premisa: ese descarte es CASI determinista y
monótono — la fecha que publica el portal no suele cambiar y el corte solo
avanza, así que una oferta fuera de ventana hoy lo estará casi siempre; su
destino está resuelto por POLÍTICA, no por un fallo transitorio. Beneficios:
no se re-descarga ni se re-cuenta cada run (`window_skipped` se aproxima a
ofertas únicas) y el early-stop vuelve a poder cortar en páginas que
contienen descartadas (financejobs/tes/irishjobs/schuljobs pagaban el
presupuesto completo de páginas en cada run). El invariante de VD.2 sigue
INTACTO para los fallos de persistencia: esos NO entran en el cursor. Las
`SKIP_NO_DATE` tampoco: su destino no está resuelto (un run posterior puede
traer la fecha).

Dos residuales de esa premisa (ronda 2 — registrados a propósito, sin
arreglo):

- La fecha la pone el PORTAL, no nosotros: un anuncio RENOVADO con la misma
  URL y fecha nueva (patrón real de `tes`) ya está aprendido como stale — si
  es la única novedad de su página, el early-stop hace que nunca se
  re-evalúe pese a estar ahora dentro de ventana. Antes de este cambio sí se
  recuperaba.
- El tope del cursor: en listados grandes (`irishjobs`) el caudal de stale
  puede superar `CURSOR_RECENT_IDENTITIES_MAX` — las identidades expulsadas
  se re-descargan y se re-cuentan, así que `window_skipped` se desliza de
  nuevo hacia "flujo por run" y el early-stop deja de cortar en esas
  páginas. Sin pérdida de datos: solo coste de crawl y contador inflado.

⚠ Las identidades stale ya aprendidas suprimen la paginación vía early-stop.
SUBIR `HARVEST_WINDOW_DAYS`, APAGAR `HARVEST_WINDOW_ENABLED` (sin vaciado,
el interruptor NO restaura el comportamiento previo: las ofertas viejas aún
listadas no se re-descargan), RECLASIFICAR una fuente a FULL o CORREGIR una
deriva de identidad exigen vaciar `recent_identities` de las fuentes WINDOW
afectadas:

    UPDATE source_cursors SET recent_identities = '[]'::jsonb
     WHERE source_key IN ('<fuentes WINDOW afectadas>');
"""

import asyncio
import logging
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded

from celery_app import celery_app
from config import settings
from database import task_session
from models.source_health import OUTCOME_ERROR
from scrapers import get_all_scrapers
from services import harvest_window, source_health
from services.crawler_budget import CrawlerBudgetService
from services.cursor_store import CursorStore
from services.data_normalizer import DataNormalizer
from services.deduplicator import Deduplicator
from services.job_repository import JobIdentityConflictError, JobRepository
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
    except SoftTimeLimitExceeded:
        # G4/P2-2 — el pool prefork levanta `SoftTimeLimitExceeded` desde un
        # SIGNAL HANDLER, y un run de cosecha pasa casi todo su tiempo
        # bloqueado en `epoll_wait` (HTTP, Playwright, BD). Si la señal llega
        # ahí, la excepción se levanta dentro de `selectors.EpollSelector.select()`
        # —FUERA del árbol de corrutinas— y ESCAPA de `asyncio.run()`: ninguno
        # de los cuatro handlers internos que añadió `f073d92` la ve. Caía en
        # el `except Exception` de abajo → `self.retry(...)` → se reintentaba
        # la cosecha ENTERA con el mismo presupuesto que ya no alcanzó, y al
        # agotar los reintentos la cadena `daily_harvest` abortaba por
        # `link_error`: ese día no había embeddings, ni dedup, ni matching, ni
        # digest. Aquí NO se reintenta: se declara la cosecha parcial y se
        # devuelve el summary, que es lo mismo que hacen los handlers
        # internos para el caso «la señal llegó durante CPU».
        logger.warning(
            "fetch_scrapers: soft time limit fuera del árbol de corrutinas — "
            "cosecha PARCIAL, sin reintento"
        )
        return {"status": "soft_time_limit", "soft_time_limit": True}
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
        # V.2/ADR-10 rev. J1 — descartes DELIBERADOS de la ventana de cosecha
        # (solo ALTAS; las re-vistas nunca se descartan). Separados a
        # propósito: `window_skipped` (fecha fuera de ventana) es el filtro
        # funcionando; `window_no_date` (política WINDOW sin published_at) es
        # una anomalía del inventario que pide revisión. K3: en scrapers las
        # descartadas por fecha entran en el cursor y no se re-cuentan en
        # runs siguientes — este contador se aproxima a ofertas ÚNICAS (a
        # diferencia del de providers, que es flujo por run).
        "window_skipped": 0,
        "window_no_date": 0,
        # G3/P2-10 — True si el run se cortó por el soft time limit: la cosecha
        # es PARCIAL (las fuentes ya commiteadas valen; las restantes no se
        # pidieron). Antes no había forma de distinguirlo de un run completo.
        "soft_time_limit": False,
        # G4/P1-1 — colisiones de IDENTIDAD (hash nuevo sobre una url que ya
        # existe): la fuente cambió su fórmula de hash y el corpus histórico no
        # se ha migrado. Contador PROPIO, separado de `errors`, porque el modo
        # de fallo es pérdida silenciosa de ofertas re-listadas, no un error
        # por-oferta cualquiera.
        "identity_conflicts": 0,
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
            # V.2 — `attempted_count` para la señal de persistencia (VD.3) =
            # ofertas que se INTENTARON guardar. NO es `len(jobs)`: las
            # descartadas por la ventana de cosecha nunca entran en el camino
            # del savepoint y contarlas produciría falsos "FUENTE DEGRADADA"
            # (mismo falso positivo que el filtro tech, F1).
            # L2 — `None` = "el bucle no llegó a arrancar": la pre-pasada de
            # la ventana mete consultas a BD ANTES del bucle (known_hashes,
            # conteo de corpus) y un fallo recurrente ahí perdía el lote
            # entero con 0 intentos registrados — la racha de persistencia no
            # se movía y la fuente jamás se degradaba (el fallo-disfrazado-
            # de-éxito de F1, reabierto). Se pone a 0 justo antes del bucle;
            # el except externo distingue ambos casos.
            attempted_count: int | None = None
            try:
                if store is not None:
                    cursor = await store.load(db, source)
                    # B-4 — con el bootstrap PENDIENTE no se inyecta el
                    # cursor: el re-bootstrap tras un run "con hambre" debe
                    # poder bajar POR DEBAJO del primer contenido ya visto.
                    # Con las identidades inyectadas, el early-stop cortaría
                    # en la página 1 (lo más nuevo ya es conocido, la cosecha
                    # es newest-first) y lo hundido bajo el horizonte del
                    # presupuesto no se recuperaría jamás. En el bootstrap
                    # genuino la ventana está vacía y no cambia nada.
                    if cursor.bootstrap_complete:
                        scraper._known_urls = store.known_identities(cursor)

                if budget_on and cursor is not None:
                    # Backoff: fuente sin novedades N runs seguidos → saltar el
                    # run hasta cumplir el intervalo ampliado (0 peticiones).
                    # Registrado a propósito, sin arreglo: una fuente WINDOW
                    # dominada por ofertas viejas acumula runs sin novedades y
                    # el backoff la espacia hasta 4x (96 h con cosecha diaria)
                    # — es latencia, no pérdida, muy por debajo de los 60 días
                    # del cleanup.
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

                # V.2/ADR-10 rev. J1 + K5 — ventana de cosecha en pre-pasada:
                # aplica SIEMPRE y solo puede rechazar ALTAS, nunca re-vistas
                # (una oferta ya en `jobs` sigue pasando por el upsert, que
                # es lo que refresca last_seen_at; saltarla acabaría en
                # archivado indebido por cleanup_stale_jobs). Una única
                # consulta por fuente (`known_hashes`) en vez de una por
                # oferta. El descarte es DELIBERADO, antes del savepoint: NO
                # cuenta como intento de persistencia (VD.3).
                precheck = await harvest_window.precheck_batch(source, jobs, repo)
                summary["window_skipped"] += precheck.skipped_by_date
                summary["window_no_date"] += precheck.skipped_no_date
                # K1 — se decide ANTES de los upserts si hay que vigilar la
                # deriva de identidad (el corpus que importa es el previo al
                # run); el veredicto final llega tras el bucle.
                watch_drift = await harvest_window.watch_drift(repo, source, precheck)

                new_before = summary["new"]
                updated_before = summary["updated"]
                dupes_before = summary["dupes"]
                # VD.2 — el cursor solo aprende identidades REALMENTE
                # persistidas: si aprendiera todo lo descargado, un fallo de
                # guardado lo envenenaría para siempre (el early-stop daría
                # esas URLs por conocidas y la fuente quedaría muda).
                stored_identities: list[str] = []
                # K3 — EXCEPCIÓN acotada y deliberada a VD.2: las descartadas
                # por FECHA fuera de ventana SÍ entran en el cursor (destino
                # resuelto por política, determinista y monótono — ver
                # docstring del módulo). Los fallos de persistencia y las
                # SKIP_NO_DATE siguen SIN entrar.
                window_stale_identities: list[str] = []
                # VD.3 — ofertas que completan su savepoint sin excepción:
                # es la señal de PERSISTENCIA para source_health.
                stored_count = 0

                # L2 — el bucle arranca: a partir de aquí 0 intentos
                # significa "todo descartado deliberadamente", no "fallo
                # pre-bucle".
                attempted_count = 0
                identity_conflicts = 0
                for job, verdict in zip(jobs, precheck.verdicts):
                    if verdict == harvest_window.SKIP_STALE:
                        window_stale_identities.append(scraper.job_identity(job))
                        continue
                    if verdict != harvest_window.ACCEPT:
                        continue

                    attempted_count += 1
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

                    except SoftTimeLimitExceeded:
                        # G3/P2-10 — el aviso de soft time limit se emite UNA
                        # sola vez y NO es un fallo de esta oferta. Como hereda
                        # de Exception, el genérico de abajo lo contaba como un
                        # error más y el bucle seguía hasta que el límite DURO
                        # mataba el worker por SIGKILL: ese día no había
                        # embeddings, ni dedup, ni matching, ni digest. Sube al
                        # bucle de fuentes, que cierra el run como «cosecha
                        # parcial» (mismo patrón que maintenance_tasks, G1/P2-14).
                        raise
                    except JobIdentityConflictError as e:
                        # G4/P1-1 — ver services/job_repository.py: se cuenta
                        # aparte de `errors` para que la deriva no se disuelva
                        # entre los fallos por-oferta.
                        summary["identity_conflicts"] += 1
                        identity_conflicts += 1
                        logger.error("%s", e)
                    except Exception as e:
                        summary["errors"] += 1
                        logger.error(
                            "Error processing scraped job from %s: %s",
                            source,
                            e,
                        )

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

                # A2-2 — en un run con OUTCOME_ERROR (404/timeout/soft-block
                # devuelven [] SIN excepción) no se aprendió nada sobre
                # novedades: el cursor NO se toca. Actualizarlo marcaba
                # `bootstrap_complete=True` / `last_success_at=now` y engordaba
                # `consecutive_empty_runs` sobre un run FALLIDO: una fuente
                # nueva con el primer run caído perdía su bootstrap para
                # siempre, y una rota entraba en el backoff de "sequía" como
                # si solo estuviera seca. De los errores se ocupan
                # source_health y compliance, no el cursor (doctrina, capas
                # 3/4). VD.2 intacto: en un run con error `stored_identities`
                # está vacío, así que aquí no se pierde aprendizaje.
                # G1/P2-4: un run PARCIALMENTE fallido (error en la página N
                # con cosecha de las previas) sale `ok` en classify
                # (degradación parcial por diseño), pero el motor lo marca
                # `_stop_reason == "error"`: terminó «con hambre». Si el
                # cursor aprendiera las identidades de la página 1, el
                # siguiente run haría early-stop en ella (`known_page`) y las
                # ofertas hundidas en la página 2+ (newest-first) no se
                # descargarían JAMÁS — la variante restante de ebb2c51. Mismo
                # trato que OUTCOME_ERROR: de los fallos se ocupan
                # source_health/compliance, no el cursor.
                if (
                    store is not None
                    and cursor is not None
                    and outcome != OUTCOME_ERROR
                    and scraper._stop_reason != "error"
                ):
                    # `pages_read` mide esfuerzo de CRAWL (lo descargado), no
                    # persistencia: sigue calculándose sobre `jobs`.
                    pages_read = max(
                        1, math.ceil(len(jobs) / max(scraper.PAGE_SIZE, 1))
                    )
                    store.update_after_run(
                        cursor,
                        # K3: lo persistido + lo descartado por fecha (destino
                        # resuelto): ninguna de las dos hace falta re-bajarla.
                        [*stored_identities, *window_stale_identities],
                        # G1/P3-17: los duplicados fuzzy SON actividad de la
                        # fuente (se ingirieron; solo se marcaron cross-source):
                        # excluirlos hacía que un agregador sindicado acumulara
                        # consecutive_empty_runs y entrara en backoff siendo
                        # productivo. Mismo criterio que log_window_summary.
                        new_count=(summary["new"] - new_before)
                        + (summary["dupes"] - dupes_before),
                        pages_read=pages_read,
                    )
                    # B-4 — lazo de autolimitación del presupuesto: `avg_new`
                    # es una EMA de `new_count`, y `new_count` nunca puede
                    # superar `presupuesto × page_size` — la EMA no puede
                    # aprender una demanda mayor que el techo que ella misma
                    # fija. Si el run AGOTÓ su presupuesto SIN early-stop
                    # (`_stop_reason is None`), terminó "con hambre": puede
                    # quedar contenido nuevo hundido bajo el horizonte y la
                    # EMA no es fiable — se re-abre el bootstrap para que el
                    # próximo run reciba la ventana completa (sin cursor
                    # inyectado, ver arriba) y re-sincronice midiendo la
                    # novedad REAL. Con `budget == MAX_PAGES` no hay nada más
                    # que pedir (cubre también fuentes de página única), y en
                    # una fuente tranquila el early-stop fija `_stop_reason`:
                    # en esos casos no se activa nunca.
                    budget_pages = scraper._max_pages_this_run
                    if (
                        scraper._stop_reason is None
                        and budget_pages is not None
                        and budget_pages < scraper.MAX_PAGES
                        and pages_read >= budget_pages
                    ):
                        cursor.bootstrap_complete = False

                await db.commit()

                # G4/P1-1 — la deriva de identidad sube a INCIDENCIA de run:
                # sin esto la fuente salía `ok` (las URLs nuevas sí entran) y
                # nadie se enteraba de que las re-listadas se estaban cayendo.
                if identity_conflicts:
                    summary["unhealthy"].append(
                        f"{source}: DERIVA DE IDENTIDAD — {identity_conflicts} "
                        "ofertas re-listadas descartadas por choque con "
                        "ix_jobs_url (corpus histórico sin migrar)"
                    )

                # VD.3 — señal de persistencia, DESPUÉS del commit a propósito:
                # `record_storage` usa su propia transacción acotada y no
                # arrastra ni el lote de ofertas ni el cursor.
                motivo = await source_health.record_storage(
                    db, source, attempted_count, stored_count
                )
                if motivo:
                    summary["unhealthy"].append(f"{source}: {motivo}")

                summary["scrapers"] += 1

            except SoftTimeLimitExceeded:
                # G3/P2-10 — se agotó el presupuesto BLANDO de la tarea. Se
                # descarta la fuente en curso (sus savepoints no están
                # commiteados; VD.2 mantiene el cursor limpio y el próximo run
                # la vuelve a bajar) y se sale del bucle devolviendo lo ya
                # cosechado: «cosecha parcial» en vez de «sin cosecha».
                # A propósito NO se registra `record_storage(attempted, 0)`:
                # la fuente no falló al guardar, se quedó sin tiempo — hacerlo
                # engordaba `consecutive_unstored` y a los dos runs lentos
                # producía un «FUENTE DEGRADADA» falso.
                await db.rollback()
                summary["soft_time_limit"] = True
                logger.warning(
                    "fetch_scrapers: soft time limit durante %s — cosecha "
                    "PARCIAL con %d fuentes completadas",
                    source,
                    summary["scrapers"],
                )
                break
            except Exception as e:
                await db.rollback()
                summary["errors"] += 1
                logger.error("Scraper %s failed: %s", source, e)
                # VD.10/H5 — si `fetch_jobs` LANZÓ (`jobs is None`), el flujo
                # normal nunca llegó a `record_and_alert` y el run no dejaba
                # NINGUNA señal de descarga: un scraper petando en cada run
                # era invisible para source_health. Se sintetiza el
                # OUTCOME_ERROR igual que hace `_fetch_one` en fetch_tasks.
                # Con `jobs` asignado NO se registra: la señal de descarga ya
                # la dejó el flujo normal y aquí se duplicaría. Mismo
                # aislamiento que `record_storage` abajo: si la BD está caída
                # el propio registro puede lanzar y mataría el bucle.
                if jobs is None:
                    summary["fetch_failed"] += 1
                    try:
                        motivo = await source_health.record_and_alert(
                            db,
                            source,
                            OUTCOME_ERROR,
                            0,
                            [
                                diag.FetchIssue(
                                    diag.KIND_NETWORK,
                                    url="",
                                    detail=f"{type(e).__name__}: {e}",
                                )
                            ],
                        )
                    except Exception as health_err:  # noqa: BLE001 — no empeorar
                        motivo = None
                        logger.error(
                            "No se pudo registrar la salud de %s en el camino "
                            "de error: %s",
                            source,
                            health_err,
                        )
                    if motivo:
                        summary["unhealthy"].append(f"{source}: {motivo}")
                # Perder el LOTE entero (commit o cursor fallidos) también es
                # señal de persistencia: sin esto la racha quedaba congelada y
                # el fallo se presentaba como éxito un nivel más arriba. Solo
                # si hubo descarga (`jobs` asignado). Se registra
                # `attempted_count` (no `len(jobs)`): las descartadas por la
                # ventana de cosecha fueron un descarte deliberado, no un
                # fallo de persistencia (V.2). `record_storage` degrada
                # a None ante fallos ordinarios, pero si la BD está caída
                # (causa probable de estar aquí) su propio rollback también
                # puede lanzar — y una excepción escapando del manejador
                # mataría el bucle de las fuentes restantes y dispararía el
                # retry de Celery (re-descarga del lote entero). Se aísla.
                # L2 — si el bucle NI ARRANCÓ (fallo en la pre-pasada:
                # known_hashes, conteo de corpus...), 0 mentiría: en ese
                # camino no hubo ningún descarte deliberado y la talla del
                # lote descargado es el valor honesto.
                if jobs is not None:
                    if attempted_count is None:
                        attempted_count = len(jobs)
                    try:
                        motivo = await source_health.record_storage(
                            db, source, attempted_count, 0
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
