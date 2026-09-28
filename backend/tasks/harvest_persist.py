"""Persistencia de UNA oferta cosechada, común a providers y scrapers (T16 §1).

Hasta el 2026-09-28 este bloque vivía duplicado, línea a línea, dentro de
`_fetch_providers_async` y `_fetch_scrapers_async` (grado E y F de radon: 37 y
47). Es el corazón del pipeline —normalizar, upsert, dedup difuso, detección de
clones de identidad— y estaba en dos sitios, así que cada arreglo (G4, G5, G7)
había que hacerlo dos veces y comprobarlo dos veces.

Contrato: se llama DENTRO del savepoint que abre el bucle (`db.begin_nested()`),
lanza exactamente lo que lanzaba el bloque original (el `except` del llamante no
cambia), y sólo toca `summary` en las mismas claves y con los mismos incrementos.
Devuelve la oferta normalizada y si se detectó un clon HISTÓRICO (G5), que el
llamante cuenta en su contador local para el aviso `mark_chronic`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded

from services import harvest_window, source_health
from services.data_normalizer import DataNormalizer
from services.deduplicator import Deduplicator
from services.job_repository import JobIdentityConflictError, JobRepository

logger = logging.getLogger(__name__)


async def persist_harvested_job(
    db,
    repo: JobRepository,
    job: dict,
    source: str,
    run_started_at: datetime,
    summary: dict[str, Any],
) -> tuple[dict, bool]:
    """Normaliza, hace upsert y clasifica la oferta (nueva/duplicada/actualizada).

    G4/G5/G7 viven aquí: un alta que resulta ser CLON de una fila histórica se
    cuenta en `summary["identity_clones"]` y se registra como ERROR; una gemela de
    la misma corrida sólo se avisa (pueden ser dos plazas). Devuelve
    `(job_normalizada, clon_historico)`.
    """
    job = DataNormalizer.normalize(job)
    job["fuzzy_hash"] = Deduplicator.compute_fuzzy_hash(job["title"], job["company"])
    is_new = await repo.upsert_job(job)
    clon_historico = False

    if is_new:
        canonical = await Deduplicator.find_fuzzy_duplicate(
            db, job["fuzzy_hash"], job["source"]
        )
        if canonical:
            await repo.mark_duplicate(job["hash"], canonical)
            summary["dupes"] += 1
        else:
            summary["new"] += 1
            twin = await Deduplicator.find_same_source_clone(
                db, job["fuzzy_hash"], job["source"], job["hash"], run_started_at
            )
            if twin:
                twin_hash, twin_historica = twin
                if twin_historica:
                    summary["identity_clones"] += 1
                    clon_historico = True
                    logger.error(
                        "DERIVA DE IDENTIDAD (clon) en %s: %s entra como ALTA nueva "
                        "pero %s ya cubre la misma vacante (%s) — la histórica "
                        "dejará de refrescar last_seen_at",
                        source,
                        job["hash"],
                        twin_hash,
                        job["url"],
                    )
                else:
                    logger.warning(
                        "GEMELA EN LA MISMA CORRIDA en %s: %s y %s comparten título y "
                        "empresa (%s). Pueden ser dos plazas distintas publicadas a la "
                        "vez; no se cuenta como deriva de identidad",
                        source,
                        job["hash"],
                        twin_hash,
                        job["url"],
                    )
    else:
        summary["updated"] += 1

    summary["fetched"] += 1
    return job, clon_historico


def identity_drift_notes(source: str, conflicts: int, clones: int) -> list[str]:
    """Los dos avisos crónicos de deriva de identidad (G4 y G5), con su texto
    exacto de siempre. Vacío si no hubo ninguno."""
    from utils.fetch_diagnostics import mark_chronic

    notas = []
    if conflicts:
        notas.append(
            mark_chronic(
                f"{source}: DERIVA DE IDENTIDAD — {conflicts} ofertas re-listadas "
                "descartadas por choque con ix_jobs_url (corpus histórico sin migrar)"
            )
        )
    if clones:
        notas.append(
            mark_chronic(
                f"{source}: DERIVA DE IDENTIDAD — {clones} ofertas re-listadas con id "
                "NUEVO entraron como CLON (sin choque de url; la fila histórica ya no "
                "refresca last_seen_at)"
            )
        )
    return notas


@dataclass
class BatchResult:
    """Lo que el bucle de una fuente necesita saber tras persistir su lote."""

    attempted: int = 0  # VD.3 — ofertas que ENTRARON al savepoint
    stored: int = 0  # VD.3 — savepoints completados sin excepción
    identity_conflicts: int = 0  # G4
    identity_clones: int = 0  # G5
    # VD.2 — sólo identidades REALMENTE persistidas: si el cursor aprendiera
    # todo lo descargado, un fallo de guardado lo envenenaría para siempre.
    stored_identities: list[str] = field(default_factory=list)
    stored_hashes: set[str] = field(default_factory=set)
    # K3 — excepción acotada a VD.2: las descartadas por FECHA sí entran en el
    # cursor (destino resuelto por política, determinista y monótono).
    stale_identities: list[str] = field(default_factory=list)


async def persist_batch(
    db,
    repo: JobRepository,
    source: str,
    run_started_at: datetime,
    summary: dict[str, Any],
    jobs: list[dict],
    verdicts,
    *,
    identity_of: Callable[[dict], str] | None = None,
) -> BatchResult:
    """El bucle por oferta de ambas cosechas: un savepoint por oferta.

    `identity_of` lo dan los scrapers (cursor incremental); los providers no
    tienen cursor y no lo pasan. La identidad se toma sobre el job CRUDO:
    `normalize` reasigna `job` dentro del savepoint y la perdería.

    Sube `SoftTimeLimitExceeded` sin tocarlo (G3/P2-10): se emite UNA sola vez
    y no es un fallo de esta oferta; contarlo como error dejaba correr el bucle
    hasta el SIGKILL del límite duro. El conflicto de identidad (G4) se cuenta
    aparte de `errors` para que la deriva no se disuelva entre los fallos
    por-oferta.
    """
    lote = BatchResult()
    for job, verdict in zip(jobs, verdicts):
        if verdict == harvest_window.SKIP_STALE and identity_of is not None:
            lote.stale_identities.append(identity_of(job))
            continue
        if verdict != harvest_window.ACCEPT:
            continue

        lote.attempted += 1
        identity = identity_of(job) if identity_of is not None else None
        try:
            async with db.begin_nested():
                job, clon = await persist_harvested_job(
                    db, repo, job, source, run_started_at, summary
                )
                if clon:
                    lote.identity_clones += 1
            # El savepoint se completó sin excepción: SOLO ahora la identidad
            # puede entrar en el cursor (VD.2).
            lote.stored += 1
            lote.stored_hashes.add(job["hash"])
            if identity is not None:
                lote.stored_identities.append(identity)
        except SoftTimeLimitExceeded:
            raise
        except JobIdentityConflictError as e:
            summary["identity_conflicts"] += 1
            lote.identity_conflicts += 1
            logger.error("%s", e)
        except Exception as e:
            summary["errors"] += 1
            logger.error("Error processing job from %s: %s", source, e)
    return lote


async def record_lost_batch(db, source: str, attempted: int, summary: dict) -> None:
    """Perder el LOTE entero (commit fallido) también es señal de persistencia.

    Sin esto la racha quedaba congelada y el fallo se presentaba como éxito un
    nivel más arriba. `record_storage` degrada a None ante fallos ordinarios,
    pero si la BD está caída (causa probable de estar aquí) su propio rollback
    también puede lanzar — y una excepción escapando del manejador mataría el
    bucle de las fuentes restantes y dispararía el retry de Celery (re-descarga
    del lote entero). Se aísla del todo. Con `attempted == 0` no toca la racha.
    """
    try:
        motivo = await source_health.record_storage(db, source, attempted, 0)
    except Exception as health_err:  # noqa: BLE001 — no empeorar el error
        motivo = None
        logger.error(
            "No se pudo registrar la persistencia de %s en el camino de error: %s",
            source,
            health_err,
        )
    if motivo:
        summary["unhealthy"].append(f"{source}: {motivo}")
