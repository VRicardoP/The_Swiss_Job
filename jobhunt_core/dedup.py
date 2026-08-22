"""Dedup semántico nivel 3 — GENERADOR DE CANDIDATOS (F-5, 2026-08-22).

La capacidad que la Fase B exigía medir y nunca construyó (auditoría F-5:
`dedup_recall >= 0,90` con un techo real de 0,073 — el core solo detectaba
duplicados por URL/attach e intra-lote). Este módulo la aporta con el alcance
MÍNIMO que el gate necesita y el MÁXIMO que es seguro hoy:

- SOLO DETECCIÓN: pares en `dedup_candidates` (state 'pending'). La métrica
  cuenta "core dice duplicado" con `state <> 'rejected'` — no hace falta
  fusionar, y el AUTO-merge es exactamente donde el legacy se hizo daño
  (B-2: 664 ofertas desactivadas en falso). La fusión (ADR-04, con
  transferencia de estado) queda como paso posterior y CONTROLADO.
- SOLO CROSS-SOURCE: el propósito declarado del dedup semántico es la misma
  oferta publicada en portales distintos. Los pares intra-fuente fueron el
  94 % de los falsos positivos del legacy (stubs sin descripción de la misma
  empresa superando 0,95); dentro de una fuente la identidad ya la dan URL y
  external_id.

Corpus y vectores: el MISMO join probado del matching (vacante elegible →
revisión vigente → embedding del modelo activo) y el MISMO índice HNSW; la
consulta kNN es un LATERAL por vacante nueva, no un O(n²).

Incremental: cada pasada mira las vacantes cuya revisión vigente nació en la
ventana (`CORE_DEDUP_SCAN_WINDOW_H`, 48 h — un día de margen sobre el beat
diario); un par nuevo exige al menos un miembro nuevo, así que la ventana
cubre. `window_hours=None` = pasada COMPLETA (backfill inicial, una vez).
Idempotente: `uq_dedup_pair` (LEAST/GREATEST) + ON CONFLICT DO NOTHING.
"""

import logging
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core import embeddings
from jobhunt_core.config import settings

logger = logging.getLogger(__name__)

# Corpus elegible CON vector y fuente (la fuente sale del primary — una
# vacante sin primary no puede afirmar su procedencia: se salta).
_CORPUS_SQL = (
    "SELECT v.id, oe.vector, sl.source_id "
    "FROM vacancies v "
    "JOIN offer_revisions orv ON orv.id = v.current_offer_revision_id "
    "JOIN offer_embeddings oe "
    "  ON oe.text_hash = orv.text_hash AND oe.model_id = :mid "
    "JOIN source_listing_incarnations pi ON pi.id = v.primary_incarnation_id "
    "JOIN source_listings sl ON sl.id = pi.source_listing_id "
    "WHERE v.archived_at IS NULL AND v.merged_into IS NULL "
)

# kNN sobre el índice HNSW (misma forma que matching.CANDIDATES_SQL).
_KNN_SQL = (
    "SELECT v.id AS vacancy_id, sl.source_id, "
    "       1 - (oe.vector <=> CAST(:vec AS vector)) AS sim "
    "FROM vacancies v "
    "JOIN offer_revisions orv ON orv.id = v.current_offer_revision_id "
    "JOIN offer_embeddings oe "
    "  ON oe.text_hash = orv.text_hash AND oe.model_id = :mid "
    "JOIN source_listing_incarnations pi ON pi.id = v.primary_incarnation_id "
    "JOIN source_listings sl ON sl.id = pi.source_listing_id "
    "WHERE v.archived_at IS NULL AND v.merged_into IS NULL "
    "ORDER BY oe.vector <=> CAST(:vec AS vector) "
    "LIMIT :k"
)

_INSERT_SQL = (
    "INSERT INTO dedup_candidates (id, vacancy_a, vacancy_b, similarity) "
    "VALUES (:id, :a, :b, :sim) "
    "ON CONFLICT (LEAST(vacancy_a, vacancy_b), GREATEST(vacancy_a, vacancy_b)) "
    "DO NOTHING"
)


async def scan_semantic_candidates(
    session: AsyncSession, window_hours: int | None = None
) -> dict:
    """Una pasada del generador. Devuelve conteos JSON-serializables."""
    if window_hours is None:
        window_hours = int(settings.CORE_DEDUP_SCAN_WINDOW_H)
    sim_min = float(settings.CORE_DEDUP_SIM_MIN)
    k = int(settings.CORE_DEDUP_KNN)

    models = await embeddings.active_models(session)
    if not models:
        return {"status": "sin_modelo", "escaneadas": 0, "candidatos_nuevos": 0}
    model_id = models[0].id

    sql = _CORPUS_SQL
    params: dict = {"mid": model_id}
    if window_hours > 0:
        sql += "AND orv.created_at >= now() - make_interval(hours => :ventana) "
        params["ventana"] = window_hours
    nuevos = (await session.execute(sa.text(sql), params)).all()

    inserted = 0
    for row in nuevos:
        vecinos = (
            await session.execute(
                sa.text(_KNN_SQL),
                {"vec": row.vector, "mid": model_id, "k": k + 1},
            )
        ).all()
        for n in vecinos:
            if n.vacancy_id == row.id:
                continue  # la propia vacante (sim 1.0)
            if n.source_id == row.source_id:
                continue  # intra-fuente: fuera por diseño (ver docstring)
            if float(n.sim) < sim_min:
                break  # ordenados por distancia: los siguientes son peores
            r = await session.execute(
                sa.text(_INSERT_SQL),
                {
                    "id": uuid.uuid4(),
                    "a": row.id,
                    "b": n.vacancy_id,
                    # Numeric(4,3): la similitud cabe siempre (0..1)
                    "sim": round(float(n.sim), 3),
                },
            )
            inserted += r.rowcount

    result = {
        "status": "ok",
        "escaneadas": len(nuevos),
        "candidatos_nuevos": inserted,
    }
    if inserted:
        logger.info("dedup_scan: %s", result)
    return result
