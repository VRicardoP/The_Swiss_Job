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
    "SELECT v.id, oe.vector, sl.source_id, "
    "       coalesce(orv.content->>'location', '') AS loc "
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
#
# TRACK R.2a (2026-08-24): guard de UBICACIÓN compatible, también en SQL y
# antes del LIMIT (misma lección). El examen del holdout midió precision
# 0.636: el ANN no tenía la regla multi-ciudad que sí tiene el exacto-intra
# y proponía como duplicado el mismo texto publicado en ciudades distintas.
# Medido en DEVELOPMENT (81 pares re-adjudicados): sim>=0.95 a secas ⇒
# 17/53 distinct como FP (precision ~0.61); con este guard ⇒ 1/53 y 24/28
# dup (~0.96). Regla: compatible si alguna ubicación está VACÍA (sin dato
# no se veta) o si una contiene a la otra (case-insensitive, con btrim) —
# «Zürich» ~ «Zürich, Zürich». Ver ANALISIS_TRACK_R_2026-08-24.md.
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
    "  AND (btrim(:loc) = '' "
    "       OR btrim(coalesce(orv.content->>'location', '')) = '' "
    "       OR position(btrim(lower(:loc)) IN "
    "                   btrim(lower(coalesce(orv.content->>'location', '')))) > 0 "
    "       OR position(btrim(lower(coalesce(orv.content->>'location', ''))) IN "
    "                   btrim(lower(:loc))) > 0) "
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


# Generador LÉXICO cross-portal (TRACK R.2b, 2026-08-24). El examen del
# holdout midió recall 0.259 y el ANN a SIM_MIN=0.95 detecta 0/9 duplicados
# cross-portal reales también en development-2: entre portales el MISMO
# puesto llega con descripciones distintas o vacías (sim 0.65–0.94) y la
# EMPRESA escrita diferente («Kanton Zug» vs «Kantonale Verwaltung Zug»).
# Señal que sí funciona (dev-2: 9/9 dup, 0 FP): token SIGNIFICATIVO de
# empresa compartido (>=3 letras, sin sufijos legales/stopwords, con tope
# de frecuencia en corpus para no explotar en «stiftung») + trigram de
# título >= CORE_DEDUP_LEX_TRGM_MIN + ubicación compatible v2 (vacío o
# código corto no vetan; remoto solo con remoto; si no, contención).
# Set-based e incremental como el ANN: un miembro del par en la ventana.
_LEX_STOP = ("'ag','gmbh','mbh','est','ltd','inc','sa','kg','co','llc',"
             "'bv','as','the','and','und','de','of','für','fur','im'")
_LEX_REMOTO = ("(lower(btrim(%s)) IN ('global','remote','worldwide',"
               "'international') OR position('anywhere' IN lower(%s)) > 0)")


def _lex_sql(window: bool) -> str:
    rem_a, rem_b = _LEX_REMOTO % ("p.loc_n", "p.loc_n"), _LEX_REMOTO % ("p.loc_c", "p.loc_c")
    filtro_ventana = (
        "WHERE created_at >= now() - make_interval(hours => :ventana) "
        if window else ""
    )
    return (
        "WITH corpus AS ("
        "  SELECT v.id, sl.source_id, orv.content->>'title' AS title, "
        "         lower(coalesce(orv.content->>'company','')) AS comp, "
        "         coalesce(orv.content->>'location','') AS loc, "
        "         orv.created_at "
        "  FROM vacancies v "
        "  JOIN offer_revisions orv ON orv.id = v.current_offer_revision_id "
        "  JOIN source_listing_incarnations pi ON pi.id = v.primary_incarnation_id "
        "  JOIN source_listings sl ON sl.id = pi.source_listing_id "
        "  WHERE v.archived_at IS NULL AND v.merged_into IS NULL"
        "), tok AS ("
        "  SELECT c.id, c.source_id, c.title, c.loc, c.created_at, t.tok "
        "  FROM corpus c, LATERAL unnest(regexp_split_to_array("
        "       c.comp, '[^a-zäöüéèß]+')) AS t(tok) "
        f"  WHERE length(t.tok) >= 3 AND t.tok NOT IN ({_LEX_STOP})"
        "), frec AS ("
        "  SELECT tok FROM tok GROUP BY tok "
        "  HAVING count(DISTINCT id) <= :maxfreq"
        "), nuevos AS ("
        f"  SELECT * FROM tok {filtro_ventana}"
        "), pares AS ("
        "  SELECT DISTINCT ON (LEAST(n.id, c.id), GREATEST(n.id, c.id)) "
        "         LEAST(n.id, c.id) AS a, GREATEST(n.id, c.id) AS b, "
        "         n.title AS t_n, c.title AS t_c, "
        "         n.loc AS loc_n, c.loc AS loc_c "
        "  FROM nuevos n "
        "  JOIN frec f ON f.tok = n.tok "
        "  JOIN tok c ON c.tok = n.tok AND c.source_id <> n.source_id "
        "       AND c.id <> n.id"
        ") "
        "INSERT INTO dedup_candidates (id, vacancy_a, vacancy_b, similarity) "
        "SELECT gen_random_uuid(), p.a, p.b, "
        "       round(similarity(p.t_n, p.t_c)::numeric, 3) "
        "FROM pares p "
        "WHERE similarity(p.t_n, p.t_c) >= :trgm "
        "  AND (btrim(p.loc_n) = '' OR btrim(p.loc_c) = '' "
        "       OR length(btrim(p.loc_n)) <= 3 OR length(btrim(p.loc_c)) <= 3 "
        f"      OR ({rem_a} AND {rem_b}) "
        f"      OR (NOT {rem_a} AND NOT {rem_b} "
        "           AND (position(btrim(lower(p.loc_n)) IN btrim(lower(p.loc_c))) > 0 "
        "                OR position(btrim(lower(p.loc_c)) IN btrim(lower(p.loc_n))) > 0))) "
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
                      "vid": row.id, "src": row.source_id, "loc": row.loc}
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

    # Léxico cross-portal (R.2b): misma ventana incremental que el ANN.
    lex_params = {
        "trgm": float(settings.CORE_DEDUP_LEX_TRGM_MIN),
        "maxfreq": int(settings.CORE_DEDUP_LEX_TOKEN_MAX_FREQ),
    }
    if window_hours > 0:
        lex_params["ventana"] = window_hours
    lexicos = (
        await session.execute(
            sa.text(_lex_sql(window=window_hours > 0)), lex_params
        )
    ).rowcount

    result = {
        "status": "ok",
        "escaneadas": len(nuevos),
        "candidatos_nuevos": inserted,
        "candidatos_exactos_intra": int(exactos),
        "candidatos_lexicos": int(lexicos),
    }
    if inserted or exactos:
        logger.info("dedup_scan: %s", result)
    return result
