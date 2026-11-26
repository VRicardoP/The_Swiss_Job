"""Barrido de archivado (ADR-07) — la SALIDA del corpus.

El sink y el proyector delegan aquí explícitamente ("el archivado ADR-07
recoge la vacante muerta") pero el barrido no existía: el único
`UPDATE ... archived_at` del core vivía dentro de `rollback_replay`
(auditoría F-2). Consecuencia medida en producción (2026-08-22): el core
servía ~4.000 vacantes activas MÁS que el legacy — todo lo que el legacy
cerró entraba como cierre de encarnación y nadie archivaba la vacante.

Tres ramas, todas set-based e idempotentes:

0. DESAPARECIDAS (D7, `close_disappeared`) — la oferta dejó de publicarse en
   un portal que recorremos ENTERO. Ver su docstring: sólo cuenta en fuentes
   cuyo productor declara barridos exhaustivos; las incrementales (ventana de
   fechas, presupuesto de páginas, búsqueda por palabra) siguen esperando a la
   rama 2, porque en ellas no ver una oferta no prueba nada.
1. MUERTAS — sin encarnación activa (el legacy/portal las cerró). Archivar
   es SEGURO respecto a la reactivación: un slot cerrado que revive abre una
   encarnación NUEVA con vacante NUEVA (sink `_open_incarnations`) — jamás
   reutiliza la archivada. La GRACIA (default 3 d) solo evita archivar en
   medio de un flap cierre→reapertura del mismo día.
   El adjunto del usuario NO exime aquí (G4-P2-3): esta rama es la única que
   pone `archived_at` para una vacante muerta, así que eximirla la dejaba
   circulando COMO ACTIVA para siempre —ningún consumidor del corpus exige
   encarnación activa: catálogo global `GET /v1/vacancies`, matching, dedup y
   embeddings se conforman con `archived_at IS NULL`— y el ancla era
   CROSS-TENANT (una candidatura del consumer A retenía la vacante en el
   corpus del consumer B). Reaparecía la patología de las ~4.000 vacantes de
   más. Lo que G3-A-P2-3 protegía —que archivar no deje el adjunto en 404
   irreversible— lo da ahora `applications.resolve_direct`, que resuelve la
   vacante ARCHIVADA cuando el perfil que pregunta ya la tiene adjunta; y
   cubre también el BOOKMARK PURO (G4-P3-2), que este guard no veía.
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
from jobhunt_core.harvest.runner import EXHAUSTIVE_SWEEPS
from jobhunt_core.harvest.sink import RawListingSink

logger = logging.getLogger(__name__)

# Corte por FUENTE (D7): el sello del N-ésimo barrido exhaustivo más reciente
# de CADA uno de sus scopes, y el más antiguo de ellos. Una encarnación es de
# la fuente, no del scope (dos scopes de arbeitnow con keywords distintas ven
# filas distintas del mismo listado), así que sólo puede darse por retirada si
# NINGÚN scope la ha visto en sus N últimos barridos exhaustivos. Un scope sin
# N sellos —incremental, deshabilitado antes de acumularlos o recién creado—
# excluye a su fuente entera. Las `legacy:*` no tienen scopes: nunca entran.
_CUTOFF_BY_SOURCE = (
    "SELECT hs.source_id, "
    "       min(sss.exhaustive_sweep_at[CAST(:n AS int)]) AS cutoff "
    "FROM harvest_scopes hs "
    "LEFT JOIN source_scope_state sss ON sss.scope_id = hs.id "
    "GROUP BY hs.source_id "
    "HAVING bool_and(COALESCE(cardinality(sss.exhaustive_sweep_at), 0) "
    "                >= CAST(:n AS int))"
)


# Tope de vacantes por transacción del cierre D7. El primer barrido tras
# desplegar cierra de golpe el atraso acumulado (~48.000 encarnaciones medidas
# el 2026-11-25): en una sola transacción retendría FOR UPDATE sobre todas esas
# vacantes y generaría un pico de WAL. Por lotes, cada uno confirma y suelta.
CLOSE_DISAPPEARED_BATCH = 2000


async def close_disappeared(
    session: AsyncSession, batch: int = CLOSE_DISAPPEARED_BATCH
) -> dict:
    """D7 por lotes acotados: ver `_close_disappeared_batch`. Confirma tras cada
    lote; para cuando un lote sale más corto que el tope (no queda trabajo o lo
    que queda está bloqueado por una cosecha, que lo verá mañana)."""
    total = {"cerradas_desaparecidas": 0, "archivadas_desaparecidas": 0}
    while True:
        lote, vistas = await _close_disappeared_batch(session, batch)
        for k in total:
            total[k] += lote[k]
        await session.commit()
        if vistas < batch:
            return total


async def _close_disappeared_batch(
    session: AsyncSession, batch: int
) -> tuple[dict, int]:
    """D7: cierra las encarnaciones que su portal ya no publica.

    Criterio: fuente de barrido EXHAUSTIVO (`FetchResult.exhaustive`, sellado
    por el runner en `source_scope_state.exhaustive_sweep_at`) cuya encarnación
    no se ha visto en los `EXHAUSTIVE_SWEEPS` últimos barridos exhaustivos de
    ninguno de sus scopes. El sello es el `now()` de la transacción que escribe
    `last_seen_at`, así que la condición es `last_seen_at < corte`, exacta.

    El cierre produce lo mismo que los cierres que ya existían, por sus mismos
    caminos:
    - vacante que conserva otra encarnación activa (compartida entre fuentes):
      primary reasignado y canónica que lo sigue, como en el reciclado
      (`RawListingSink.repair_after_close`);
    - vacante que se queda sin ninguna: archivada YA, con el mismo UPDATE que
      la rama de rancias. Sin gracia, porque la gracia de la rama 1 cubre un
      cierre→reapertura del mismo día y aquí hacen falta N barridos de
      ausencia; una reaparición posterior abre vacante nueva igual que hoy.
      Sin eximir el adjunto, como la rama 1 (G4-P2-3): es una muerte en el
      portal, no una rancia, y `applications.resolve_direct` sigue resolviendo
      la vacante archivada para el perfil que la tiene adjunta.
    El UPDATE de `vacancies` dispara `bump_corpus_generation()` como siempre.

    Concurrencia: mismo protocolo que el sink y que la rama 2 — se bloquean
    primero las vacantes (ORDER BY id, SKIP LOCKED) y el cierre RE-EVALÚA
    `last_seen_at < corte` sobre la fila vigente: una cosecha que refrescó la
    encarnación entre la selección y el lock la deja abierta.
    """
    params = {"n": EXHAUSTIVE_SWEEPS, "lim": int(batch)}
    vac_ids = (
        (
            await session.execute(
                sa.text(
                    f"WITH cut AS ({_CUTOFF_BY_SOURCE}) "
                    "SELECT v.id FROM vacancies v "
                    "WHERE v.archived_at IS NULL AND v.merged_into IS NULL "
                    "  AND v.id IN ("
                    "    SELECT i.vacancy_id FROM source_listing_incarnations i "
                    "    JOIN source_listings sl ON sl.id = i.source_listing_id "
                    "    JOIN cut c ON c.source_id = sl.source_id "
                    "    WHERE i.ended_at IS NULL AND i.last_seen_at < c.cutoff) "
                    "ORDER BY v.id LIMIT :lim FOR UPDATE OF v SKIP LOCKED"
                ),
                params,
            )
        )
        .scalars()
        .all()
    )
    if not vac_ids:
        return {"cerradas_desaparecidas": 0, "archivadas_desaparecidas": 0}, 0
    closed_vacs = (
        (
            await session.execute(
                sa.text(
                    f"WITH cut AS ({_CUTOFF_BY_SOURCE}) "
                    "UPDATE source_listing_incarnations i SET ended_at = now() "
                    "FROM source_listings sl, cut c "
                    "WHERE i.vacancy_id = ANY(:ids) AND i.ended_at IS NULL "
                    "  AND sl.id = i.source_listing_id "
                    "  AND c.source_id = sl.source_id "
                    "  AND i.last_seen_at < c.cutoff "
                    "RETURNING i.vacancy_id"
                ),
                {**params, "ids": vac_ids},
            )
        )
        .scalars()
        .all()
    )
    touched = sorted(set(closed_vacs), key=str)
    await RawListingSink().repair_after_close(session, touched)
    archived = (
        await session.execute(
            sa.text(
                "UPDATE vacancies v SET archived_at = now() "
                "WHERE v.id = ANY(:ids) AND NOT EXISTS ("
                "  SELECT 1 FROM source_listing_incarnations i "
                "  WHERE i.vacancy_id = v.id AND i.ended_at IS NULL)"
            ),
            {"ids": touched},
        )
    ).rowcount
    return {
        "cerradas_desaparecidas": len(closed_vacs),
        "archivadas_desaparecidas": int(archived),
    }, len(vac_ids)


async def archive_sweep(session: AsyncSession) -> dict:
    """Ejecuta las tres ramas. Devuelve conteos JSON-serializables (tarea)."""
    # Rama 0 primero: lo que cierra y archiva aquí ya no lo ven las otras dos.
    disappeared = await close_disappeared(session)
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
                # G5-N-7(a): el guard es inocuo por lo que se DEJA DE HACER, no
                # por el estado del SELECT — la vacante tiene encarnación
                # activa AHORA, pero el bloque de abajo la CIERRA antes de
                # archivar (invariante «archivada ⇒ sin encarnación activa»).
                # Conservarla evita anclar en el catálogo global una vacante
                # rancia con candidatura; la resolubilidad del adjunto ya no
                # depende de este guard: la garantiza `resolve_direct`.
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
            sa.text("UPDATE vacancies SET archived_at = now() WHERE id = ANY(:ids)"),
            {"ids": stale_ids},
        )

    result = {
        **disappeared,
        "archivadas_muertas": int(dead),
        "archivadas_rancias": len(stale_ids),
    }
    if dead or stale_ids or disappeared["cerradas_desaparecidas"]:
        logger.info("archive_sweep: %s", result)
    return result
