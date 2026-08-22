"""Barrido de archivado (ADR-07) — la SALIDA del corpus.

El sink y el proyector delegan aquí explícitamente ("el archivado ADR-07
recoge la vacante muerta") pero el barrido no existía: el único
`UPDATE ... archived_at` del core vivía dentro de `rollback_replay`
(auditoría F-2). Consecuencia medida en producción (2026-08-22): el core
servía ~4.000 vacantes activas MÁS que el legacy — todo lo que el legacy
cerró entraba como cierre de encarnación y nadie archivaba la vacante.

Dos ramas, ambas set-based e idempotentes:

1. MUERTAS — sin encarnación activa (el legacy/portal las cerró). Archivar es
   SEGURO respecto a la reactivación: un slot cerrado que revive abre una
   encarnación NUEVA con vacante NUEVA (sink `_open_incarnations`) — jamás
   reutiliza la archivada. La GRACIA (default 3 d) solo evita archivar en
   medio de un flap cierre→reapertura del mismo día.
2. RANCIAS (ADR-07 literal) — encarnación activa pero sin verse en
   `CORE_CORPUS_STALE_DAYS` (120 d) y SIN adjunto (candidatura, PF.3: con
   adjunto se conserva). Se cierra también la encarnación para conservar el
   invariante "archivada ⇒ sin encarnación activa": si la oferta reaparece,
   la vía de reactivación abre vacante nueva y limpia.

Concurrencia: `FOR UPDATE SKIP LOCKED` — el sink bloquea vacantes en sus runs
(`_lock_vacancies`, ORDER BY id); saltarse una fila bloqueada no deja hueco
(la recoge el siguiente barrido diario) y evita deadlocks por orden de lock.

El UPDATE de `vacancies` dispara `bump_corpus_generation()` (core0022): el
corpus elegible cambió y la recuperación re-evalúa perfiles sola.
"""

import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.config import settings

logger = logging.getLogger(__name__)


async def archive_sweep(session: AsyncSession) -> dict:
    """Ejecuta ambas ramas. Devuelve conteos JSON-serializables (tarea)."""
    grace_days = int(settings.CORE_ARCHIVE_GRACE_DAYS)
    stale_days = int(settings.CORE_CORPUS_STALE_DAYS)

    # Una sola AGREGACIÓN por vacante alimenta ambas ramas. La versión
    # anterior usaba subqueries CORRELACIONADAS con max(): el único índice
    # útil de incarnations es PARCIAL (vacancy_id WHERE ended_at IS NULL),
    # así que cada max() era un seq-scan de la tabla entera POR FILA —
    # ~750M visitas y 10+ min en producción, con FOR UPDATE reteniendo
    # locks todo ese tiempo. Un GROUP BY es UN pase (milisegundos).
    # El JOIN a la agregación implica "tuvo vida": una vacante sin
    # encarnación alguna (recién nacida de otro run, residuo de cuarentena)
    # no aparece y no se toca.
    agg = (
        "SELECT vacancy_id, bool_or(ended_at IS NULL) AS tiene_activa, "
        "       max(ended_at) AS ultimo_cierre, "
        "       max(last_seen_at) AS ultimo_visto "
        "FROM source_listing_incarnations GROUP BY vacancy_id"
    )

    # --- rama 1: MUERTAS (sin encarnación activa, gracia cumplida) ---------
    dead = (
        await session.execute(
            sa.text(
                f"WITH agg AS ({agg}), "
                "candidatas AS ("
                "  SELECT v.id FROM vacancies v"
                "  JOIN agg a ON a.vacancy_id = v.id"
                "  WHERE v.archived_at IS NULL AND v.merged_into IS NULL"
                "    AND NOT a.tiene_activa"
                "    AND a.ultimo_cierre < now() - make_interval(days => :grace)"
                "  ORDER BY v.id FOR UPDATE OF v SKIP LOCKED"
                ") "
                "UPDATE vacancies v SET archived_at = now() "
                "FROM candidatas c WHERE v.id = c.id RETURNING v.id"
            ),
            {"grace": grace_days},
        )
    ).rowcount

    # --- rama 2: RANCIAS ADR-07 (120 d sin visto, sin adjunto) -------------
    stale_rows = (
        await session.execute(
            sa.text(
                f"WITH agg AS ({agg}) "
                "SELECT v.id FROM vacancies v "
                "JOIN agg a ON a.vacancy_id = v.id "
                "WHERE v.archived_at IS NULL AND v.merged_into IS NULL "
                "  AND a.tiene_activa "
                "  AND a.ultimo_visto < now() - make_interval(days => :stale) "
                # PF.3: con candidatura se conserva (archivar no borra, pero
                # el contrato dice conservar la vacante VIVA para el usuario).
                "  AND NOT EXISTS (SELECT 1 FROM applications ap "
                "                  WHERE ap.vacancy_id = v.id) "
                "ORDER BY v.id FOR UPDATE OF v SKIP LOCKED"
            ),
            {"stale": stale_days},
        )
    ).all()
    stale_ids = [r.id for r in stale_rows]
    if stale_ids:
        # Cerrar la encarnación ANTES de archivar: invariante "archivada ⇒
        # sin encarnación activa". Una reaparición posterior abre vacante nueva.
        await session.execute(
            sa.text(
                "UPDATE source_listing_incarnations SET ended_at = now() "
                "WHERE vacancy_id = ANY(:ids) AND ended_at IS NULL"
            ),
            {"ids": stale_ids},
        )
        await session.execute(
            sa.text(
                "UPDATE vacancies SET archived_at = now() WHERE id = ANY(:ids)"
            ),
            {"ids": stale_ids},
        )

    result = {"archivadas_muertas": int(dead), "archivadas_rancias": len(stale_ids)}
    if dead or stale_ids:
        logger.info("archive_sweep: %s", result)
    return result
