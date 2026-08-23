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
from jobhunt_core.matching import MAX_SCAN_TUPLES

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
# B-2 auditoría externa (2026-08-23): la exclusión de la PROPIA vacante y de la
# MISMA fuente va ANTES del ORDER BY/LIMIT. Filtrarlas después, en Python,
# hacía que una concentración de anuncios muy próximos de una fuente consumiera
# el presupuesto k y OCULTARA vecinos cross-source válidos (reproducido: 6
# vacantes intra a sim 1.0 + 1 cross a 0.96 con k=5 → el par cross ni llegaba
# al filtro). Con el filtro en SQL, LIMIT :k significa "k vecinos cross-source".
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
    "  AND v.id <> :vid AND sl.source_id <> :src "
    "ORDER BY oe.vector <=> CAST(:vec AS vector) "
    "LIMIT :k"
)

_INSERT_SQL = (
    "INSERT INTO dedup_candidates (id, vacancy_a, vacancy_b, similarity) "
    "VALUES (:id, :a, :b, :sim) "
    "ON CONFLICT (LEAST(vacancy_a, vacancy_b), GREATEST(vacancy_a, vacancy_b)) "
    "DO NOTHING"
)


# Duplicados EXACTOS intra-fuente (regla ratificada por el propietario el
# 2026-08-23 al curar el oráculo): mismo texto canónico (text_hash) Y misma
# location ⇒ duplicado; contenido idéntico con ciudad DISTINTA = publicación
# multi-ciudad legítima (Flix Berlín vs Múnich) y NO se marca. Determinista —
# sin umbral ni embedding — y por eso NO comparte la ambigüedad de los stubs
# que dejó lo intra-fuente fuera del ANN. Motivado además por el hallazgo del
# canario (§12.9: grupos de hasta 9 repetidas en el feed). Set-based: un pase.
_EXACT_INTRA_SQL = (
    "WITH corpus AS ("
    "  SELECT v.id, orv.text_hash, sl.source_id, "
    "         coalesce(orv.content->>'location', '') AS loc "
    "  FROM vacancies v "
    "  JOIN offer_revisions orv ON orv.id = v.current_offer_revision_id "
    "  JOIN source_listing_incarnations pi ON pi.id = v.primary_incarnation_id "
    "  JOIN source_listings sl ON sl.id = pi.source_listing_id "
    "  WHERE v.archived_at IS NULL AND v.merged_into IS NULL"
    ") "
    "INSERT INTO dedup_candidates (id, vacancy_a, vacancy_b, similarity) "
    "SELECT gen_random_uuid(), a.id, b.id, 1.000 "
    "FROM corpus a JOIN corpus b "
    "  ON a.text_hash = b.text_hash AND a.source_id = b.source_id "
    "  AND a.loc = b.loc AND a.id < b.id "
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

    # UNDERFILL del HNSW (auditoría Nº2, IMPORTANTE 1): pgvector aplica el
    # WHERE DESPUÉS de sacar candidatos del índice aproximado — sin scan
    # iterativo, "LIMIT :k" puede devolver < k vecinos cross-source aunque
    # existan (reproducido: 2 de 5 con el GUC por defecto). MISMO patrón ya
    # probado en matching: ef_search acotado + iterative_scan strict_order
    # (sigue escaneando hasta llenar el LIMIT tras el filtro, con tope
    # MAX_SCAN_TUPLES) + FALLBACK EXACTO si aun así llegan menos filas que
    # el objetivo REAL (nº de vacantes elegibles de OTRAS fuentes, acotado
    # por k — un corpus pequeño no dispara el exacto en cada fila).
    ef_search = min(max(k, 40), 1000)
    await session.execute(sa.text(f"SET LOCAL hnsw.ef_search = {int(ef_search)}"))
    await session.execute(sa.text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
    await session.execute(
        sa.text(f"SET LOCAL hnsw.max_scan_tuples = {int(MAX_SCAN_TUPLES)}")
    )
    por_fuente = {
        r.source_id: r.n
        for r in (
            await session.execute(
                sa.text(
                    "SELECT c.source_id AS source_id, count(*) AS n "
                    "FROM (" + _CORPUS_SQL + ") c GROUP BY c.source_id"
                ),
                {"mid": model_id},
            )
        ).all()
    }
    total_corpus = sum(por_fuente.values())

    inserted = 0
    for row in nuevos:
        knn_params = {"vec": row.vector, "mid": model_id, "k": k,
                      "vid": row.id, "src": row.source_id}
        vecinos = (
            await session.execute(sa.text(_KNN_SQL), knn_params)
        ).all()
        objetivo = min(k, total_corpus - por_fuente.get(row.source_id, 0))
        if len(vecinos) < objetivo:
            # Inanición REAL del scan acotado: el exacto responde siempre.
            await session.execute(sa.text("SET LOCAL enable_indexscan = off"))
            await session.execute(sa.text("SET LOCAL enable_bitmapscan = off"))
            vecinos = (
                await session.execute(sa.text(_KNN_SQL), knn_params)
            ).all()
            await session.execute(sa.text("SET LOCAL enable_indexscan = on"))
            await session.execute(sa.text("SET LOCAL enable_bitmapscan = on"))
        for n in vecinos:
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

    # Exactos intra-fuente: pase completo siempre (barato: un join indexado;
    # la idempotencia la da uq_dedup_pair).
    exactos = (await session.execute(sa.text(_EXACT_INTRA_SQL))).rowcount

    result = {
        "status": "ok",
        "escaneadas": len(nuevos),
        "candidatos_nuevos": inserted,
        "candidatos_exactos_intra": int(exactos),
    }
    if inserted or exactos:
        logger.info("dedup_scan: %s", result)
    return result
