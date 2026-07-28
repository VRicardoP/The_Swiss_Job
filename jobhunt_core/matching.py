"""Matching determinista por embeddings (A-08, ADR-03 + CONTRATOS §1).

- `match_evaluations` = APPEND-ONLY con los componentes como COLUMNAS y
  `eval_key` DETERMINISTA (hash de offer_revision + profile_revision + model +
  policy): re-evaluar los MISMOS componentes no duplica —
  UNIQUE(profile_id, vacancy_id, eval_key) + DO NOTHING ("reintento no
  duplica"). Sin feedback aquí (ADR-03).
- `profile_vacancy_state` = estado ESTABLE por (perfil, vacante): el matching
  solo mueve `current_eval_id` (FK compuesta, RESTRICT) y `updated_at` —
  JAMÁS pisa feedback/dismissed_at/saved_at/notes.
- Feed (DoD): evaluación VIGENTE (current_eval_id) + no-dismissed + vacante
  ACTIVA, keyset por (score_final DESC, vacancy_id ASC).
- score_final en Fase A: similitud coseno (pgvector `<=>`, HNSW del modelo)
  escalada a 0..100 con 2 decimales (NUMERIC(6,2) del contrato). El
  multi-factor/rerank es POLÍTICA VERSIONADA de Fase B (`weights` JSONB
  reservado en scoring_policies).
"""

import hashlib
import json
import logging
import uuid

import sqlalchemy as sa

logger = logging.getLogger(__name__)

# Tope de tuplas del scan ITERATIVO del HNSW (rev. A-08 #2): strict_order
# sigue escaneando hasta llenar el LIMIT tras el filtro, acotado por esto.
MAX_SCAN_TUPLES = 20000

# Namespace DETERMINISTA de los eventos de integración (ADR-05: event_id =
# uuid5(ns, type || ':' || clave-natural); para match.evaluated → eval_key).
EVENTS_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "jobhunt-core/integration-events")


def event_id_for(event_type: str, natural_key: str) -> uuid.UUID:
    return uuid.uuid5(EVENTS_NAMESPACE, f"{event_type}:{natural_key}")

# SQL de candidatos (module-level: los tests lo EXPLAINean tal cual).
CANDIDATES_SQL = (
    "SELECT v.id AS vacancy_id, "
    "v.current_offer_revision_id AS offer_revision_id, "
    "1 - (oe.vector <=> CAST(:vec AS vector)) AS sim "
    "FROM vacancies v "
    "JOIN offer_revisions orv ON orv.id = v.current_offer_revision_id "
    "JOIN offer_embeddings oe "
    "  ON oe.text_hash = orv.text_hash AND oe.model_id = :mid "
    "WHERE v.archived_at IS NULL AND v.merged_into IS NULL "
    "ORDER BY oe.vector <=> CAST(:vec AS vector) "
    "LIMIT :k"
)


def eval_key(offer_revision_id, profile_revision_id, model_id, policy_id) -> str:
    """Clave DETERMINISTA de la evaluación: mismos componentes ⇒ misma clave
    ⇒ una sola fila append-only (idempotencia por contrato)."""
    raw = f"{offer_revision_id}|{profile_revision_id}|{model_id}|{policy_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def ensure_policy(
    session, name: str, prompt_version: str, weights: dict | None = None,
    active: bool = True,
) -> uuid.UUID:
    """Alta idempotente de la política (UNIQUE(name, prompt_version)). Como
    register_model: la fila existente se relee bajo lock y `active` se
    ACTUALIZA al re-declarar (declaración operativa); weights solo al crear
    (una política versionada no muta — otra versión = otra fila)."""
    await session.execute(
        sa.text(
            "INSERT INTO scoring_policies (id, name, prompt_version, weights, active) "
            "VALUES (:id, :name, :ver, CAST(:w AS jsonb), :active) "
            "ON CONFLICT (name, prompt_version) DO NOTHING"
        ),
        {
            "id": uuid.uuid4(), "name": name, "ver": prompt_version,
            "w": json.dumps(weights or {}), "active": active,
        },
    )
    row = (
        await session.execute(
            sa.text(
                "SELECT id, active FROM scoring_policies "
                "WHERE name = :name AND prompt_version = :ver FOR UPDATE"
            ),
            {"name": name, "ver": prompt_version},
        )
    ).one()
    if row.active != active:
        await session.execute(
            sa.text("UPDATE scoring_policies SET active = :a WHERE id = :id"),
            {"a": active, "id": row.id},
        )
    return row.id


async def evaluate_profile(
    session, profile_id, model_id, policy_id, limit: int = 100,
    move_current: bool = True,
) -> dict:
    """Evalúa el perfil (revisión VIGENTE + su vector) contra las ofertas
    ACTIVAS con vector del mismo modelo, por coseno (HNSW).

    - LOCK por perfil (FOR UPDATE — mismo protocolo que save_profile_revision;
      auditoría A-08): evaluaciones del mismo perfil se SERIALIZAN, y la que
      corre después lee la revisión vigente MÁS NUEVA — current_eval_id nunca
      retrocede a una revisión vieja por una carrera.
    - `move_current`: solo el evaluador CANÓNICO (primer (modelo, política)
      activo en orden determinista — lo decide la tarea) mueve
      current_eval_id; el resto corre en SOMBRA (append-only, sin tocar el
      estado) — con varios modelos el feed es determinista (auditoría A-08).
    Todo por lotes: 1 SELECT de candidatos + 1 INSERT append-only + 1
    re-select de ganadores + 1 UPSERT de estado (solo current_eval_id y
    updated_at)."""
    locked = (
        await session.execute(
            sa.text(
                "SELECT p.id, c.name AS consumer_name FROM profiles p "
                "JOIN consumers c ON c.id = p.consumer_id "
                "WHERE p.id = :pid FOR UPDATE OF p"
            ),
            {"pid": profile_id},
        )
    ).one_or_none()
    if locked is None:
        return {
            "status": "not_found", "evaluated": 0, "new_evals": 0,
            "moved_current": False,
        }
    prof = (
        await session.execute(
            sa.text(
                "SELECT cur.revision_id, pe.vector::text AS vec "
                "FROM (SELECT DISTINCT ON (profile_id) profile_id, revision_id "
                "      FROM profile_revision_activations WHERE profile_id = :pid "
                "      ORDER BY profile_id, seq DESC) cur "
                "JOIN profile_embeddings pe "
                "  ON pe.profile_revision_id = cur.revision_id AND pe.model_id = :mid"
            ),
            {"pid": profile_id, "mid": model_id},
        )
    ).one_or_none()
    if prof is None:
        # Sin revisión vigente o sin vector para este modelo: nada que evaluar
        # (el worker de embeddings aún no pasó) — no es un error.
        return {
            "status": "sin_vector", "evaluated": 0, "new_evals": 0,
            "moved_current": False,
        }

    # ANN ROBUSTO (auditoría + rev. A-08 #2 + 2ª P2s): el filtro posterior
    # (revisión vigente + vacante activa) puede dejar el scan HNSW SIN
    # candidatos si los embeddings HISTÓRICOS/huérfanos más cercanos lo
    # consumen. Capas: ef_search en [40..1000] (rango válido del GUC; para
    # limit > 1000 cubre el fallback), iterative_scan strict_order (pgvector
    # >= 0.8: sigue escaneando hasta llenar el LIMIT tras el filtro, acotado
    # por MAX_SCAN_TUPLES) y FALLBACK EXACTO solo si el ANN devuelve menos
    # que el OBJETIVO REAL (conteo acotado de elegibles: un corpus menor que
    # limit NO dispara la segunda búsqueda en cada run — rev. 2ª P2#2).
    # Enteros validados (SET no admite binds).
    if limit < 1:
        raise ValueError(f"limit={limit}: el top-K debe ser >= 1")
    eligible = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM (SELECT 1 FROM vacancies v "
                "JOIN offer_revisions orv ON orv.id = v.current_offer_revision_id "
                "JOIN offer_embeddings oe "
                "  ON oe.text_hash = orv.text_hash AND oe.model_id = :mid "
                "WHERE v.archived_at IS NULL AND v.merged_into IS NULL "
                "LIMIT :k) t"
            ),
            {"mid": model_id, "k": limit},
        )
    ).scalar_one()
    target = min(limit, int(eligible))
    if target == 0:
        return {"status": "ok", "evaluated": 0, "new_evals": 0, "moved_current": False}
    params = {"vec": prof.vec, "mid": model_id, "k": limit}
    ef_search = min(max(limit, 40), 1000)
    await session.execute(sa.text(f"SET LOCAL hnsw.ef_search = {int(ef_search)}"))
    await session.execute(sa.text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
    await session.execute(
        sa.text(f"SET LOCAL hnsw.max_scan_tuples = {int(MAX_SCAN_TUPLES)}")
    )
    candidates = (await session.execute(sa.text(CANDIDATES_SQL), params)).all()
    if len(candidates) < target:
        # Inanición REAL del scan acotado: el exacto responde siempre bien.
        await session.execute(sa.text("SET LOCAL enable_indexscan = off"))
        await session.execute(sa.text("SET LOCAL enable_bitmapscan = off"))
        candidates = (await session.execute(sa.text(CANDIDATES_SQL), params)).all()
        await session.execute(sa.text("SET LOCAL enable_indexscan = on"))
        await session.execute(sa.text("SET LOCAL enable_bitmapscan = on"))
    if not candidates:
        return {"status": "ok", "evaluated": 0, "new_evals": 0, "moved_current": False}

    eval_rows = []
    for c in candidates:
        key = eval_key(c.offer_revision_id, prof.revision_id, model_id, policy_id)
        # Coseno en [-1, 1] → score 0..100 (2 decimales, NUMERIC(6,2)).
        score = round(max(0.0, float(c.sim)) * 100, 2)
        eval_rows.append(
            {
                "id": uuid.uuid4(), "pid": profile_id, "vid": c.vacancy_id,
                "orid": c.offer_revision_id, "prid": prof.revision_id,
                "mid": model_id, "spid": policy_id, "key": key,
                "score": score,
                "scores": json.dumps({"similarity": round(float(c.sim), 6)}),
            }
        )
    eval_rows.sort(key=lambda r: str(r["vid"]))  # orden determinista
    await session.execute(
        sa.text(
            "INSERT INTO match_evaluations "
            "(id, profile_id, vacancy_id, offer_revision_id, profile_revision_id, "
            " model_id, scoring_policy_id, eval_key, score_final, scores) "
            "VALUES (:id, :pid, :vid, :orid, :prid, :mid, :spid, :key, :score, "
            "CAST(:scores AS jsonb)) "
            "ON CONFLICT (profile_id, vacancy_id, eval_key) DO NOTHING"
        ),
        eval_rows,
    )
    # Ganadores REALES (idempotencia/carreras: la fila puede ser previa).
    winners = {
        (r.vacancy_id, r.eval_key): r.id
        for r in (
            await session.execute(
                sa.text(
                    "SELECT e.id, e.vacancy_id, e.eval_key FROM match_evaluations e "
                    "JOIN unnest(CAST(:vids AS uuid[]), CAST(:keys AS text[])) "
                    "  AS t(vid, k) ON e.vacancy_id = t.vid AND e.eval_key = t.k "
                    "WHERE e.profile_id = :pid"
                ),
                {
                    "pid": profile_id,
                    "vids": [str(r["vid"]) for r in eval_rows],
                    "keys": [r["key"] for r in eval_rows],
                },
            )
        ).all()
    }
    fresh = [r for r in eval_rows if winners.get((r["vid"], r["key"])) == r["id"]]
    new_evals = len(fresh)
    if fresh:
        # OUTBOX en la MISMA transacción que la escritura (A-10, ADR-05):
        # event_id determinista por eval_key + DO NOTHING = re-emisión
        # imposible; el estado de entrega va POR destino (ADR-06) — el BFF del
        # consumidor del perfil (§3). Payload = SOLO IDs (el consumidor
        # resuelve por /v1).
        events = sorted(
            (
                {
                    "eid": event_id_for("match.evaluated", r["key"]),
                    "agg": r["key"], "pid": profile_id,
                    "payload": json.dumps(
                        {
                            "eval_key": r["key"],
                            "profile_id": str(profile_id),
                            "vacancy_id": str(r["vid"]),
                        }
                    ),
                }
                for r in fresh
            ),
            key=lambda e: str(e["eid"]),
        )
        await session.execute(
            sa.text(
                "INSERT INTO integration_outbox "
                "(event_id, aggregate, aggregate_id, subject_profile_id, "
                " version, type, payload) "
                "VALUES (:eid, 'match_evaluation', :agg, :pid, 1, "
                "'match.evaluated', CAST(:payload AS jsonb)) "
                "ON CONFLICT (event_id) DO NOTHING"
            ),
            events,
        )
        await session.execute(
            sa.text(
                "INSERT INTO integration_outbox_deliveries "
                "(event_id, destination, next_attempt_at) "
                "VALUES (:eid, :dest, clock_timestamp()) "
                "ON CONFLICT (event_id, destination) DO NOTHING"
            ),
            [{"eid": e["eid"], "dest": locked.consumer_name} for e in events],
        )
    moved = False
    if move_current:
        state_rows = [
            {"pid": profile_id, "vid": r["vid"], "eid": winners[(r["vid"], r["key"])]}
            for r in eval_rows
            if (r["vid"], r["key"]) in winners
        ]
        if state_rows:
            # El feed representa el conjunto CANÓNICO de esta ejecución, no
            # la unión histórica de antiguos top-K. La interacción estable se
            # conserva en su fila; solo se retira el puntero de evaluación.
            await session.execute(
                sa.text(
                    "UPDATE profile_vacancy_state "
                    "SET current_eval_id = NULL, "
                    "updated_at = GREATEST(updated_at, clock_timestamp()) "
                    "WHERE profile_id = :pid AND current_eval_id IS NOT NULL "
                    "AND NOT (vacancy_id = ANY(CAST(:vids AS uuid[])))"
                ),
                {
                    "pid": profile_id,
                    "vids": [str(row["vid"]) for row in state_rows],
                },
            )
            # Estado: SOLO current_eval_id/updated_at — feedback/dismissed/
            # saved/notes se preservan SIEMPRE (ADR-03: estado estable).
            # clock_timestamp() + GREATEST (rev. A-08 #3): now() es la HORA DE
            # INICIO de la transacción — una tx vieja que escribe tarde jamás
            # debe hacer retroceder updated_at.
            await session.execute(
                sa.text(
                    "INSERT INTO profile_vacancy_state "
                    "(profile_id, vacancy_id, current_eval_id, updated_at) "
                    "VALUES (:pid, :vid, :eid, clock_timestamp()) "
                    "ON CONFLICT (profile_id, vacancy_id) DO UPDATE "
                    "SET current_eval_id = EXCLUDED.current_eval_id, "
                    "updated_at = GREATEST(profile_vacancy_state.updated_at, "
                    "clock_timestamp())"
                ),
                state_rows,
            )
            moved = True
    return {
        "status": "ok", "evaluated": len(eval_rows), "new_evals": new_evals,
        "moved_current": moved,
    }


async def feed(session, profile_id, limit: int = 20, cursor=None, consumer_id=None):
    """Feed del perfil (DoD A-08): evaluación VIGENTE + no-dismissed + vacante
    ACTIVA, keyset por (score_final DESC, vacancy_id ASC).

    `cursor` = (score_final, vacancy_id) de la última fila entregada; devuelve
    (filas, next_cursor) con next_cursor None al agotar. `consumer_id`
    (rev. A-09 #1): el OWNERSHIP multi-tenant se filtra EN LA QUERY (§2) —
    una reasignación de tenant a mitad de request jamás puede filtrar filas."""
    where_cursor = ""
    tenant_join = ""
    params = {"pid": profile_id, "lim": limit}
    if consumer_id is not None:
        tenant_join = "JOIN profiles p ON p.id = s.profile_id AND p.consumer_id = :cid "
        params["cid"] = consumer_id
    if cursor is not None:
        where_cursor = (
            "AND (e.score_final < :cs "
            "OR (e.score_final = :cs AND e.vacancy_id > :cv)) "
        )
        params["cs"], params["cv"] = cursor
    rows = (
        await session.execute(
            sa.text(
                "SELECT e.vacancy_id, e.score_final, e.id AS eval_id, e.scores, "
                "e.offer_revision_id, s.saved_at, s.feedback, s.notes "
                "FROM profile_vacancy_state s "
                f"{tenant_join}"
                "JOIN match_evaluations e ON e.id = s.current_eval_id "
                "  AND e.profile_id = s.profile_id AND e.vacancy_id = s.vacancy_id "
                "JOIN vacancies v ON v.id = s.vacancy_id "
                "  AND v.archived_at IS NULL AND v.merged_into IS NULL "
                "WHERE s.profile_id = :pid AND s.dismissed_at IS NULL "
                f"{where_cursor}"
                "ORDER BY e.score_final DESC, e.vacancy_id ASC "
                "LIMIT :lim"
            ),
            params,
        )
    ).all()
    # `rows and`: con limit=0 no hay última fila (auditoría A-08).
    next_cursor = (
        (rows[-1].score_final, rows[-1].vacancy_id)
        if rows and len(rows) == limit
        else None
    )
    return rows, next_cursor


async def set_dismissed(session, profile_id, vacancy_id, dismissed: bool) -> None:
    """Descartar/restaurar: upsert que SOLO toca dismissed_at/updated_at.
    clock_timestamp() + GREATEST (rev. A-08 #3): la hora real de ESCRITURA,
    no la de inicio de la tx — sin retrocesos temporales entre tx solapadas."""
    await session.execute(
        sa.text(
            "INSERT INTO profile_vacancy_state "
            "(profile_id, vacancy_id, dismissed_at, updated_at) "
            "VALUES (:pid, :vid, CASE WHEN :d THEN clock_timestamp() END, "
            "clock_timestamp()) "
            "ON CONFLICT (profile_id, vacancy_id) DO UPDATE "
            "SET dismissed_at = CASE WHEN :d THEN clock_timestamp() END, "
            "updated_at = GREATEST(profile_vacancy_state.updated_at, clock_timestamp())"
        ),
        {"pid": profile_id, "vid": vacancy_id, "d": dismissed},
    )


async def set_saved(session, profile_id, vacancy_id, saved: bool) -> None:
    """Bookmark (saved = aquí, ADR-03): solo saved_at/updated_at — misma
    disciplina temporal que set_dismissed (rev. A-08 #3)."""
    await session.execute(
        sa.text(
            "INSERT INTO profile_vacancy_state "
            "(profile_id, vacancy_id, saved_at, updated_at) "
            "VALUES (:pid, :vid, CASE WHEN :sv THEN clock_timestamp() END, "
            "clock_timestamp()) "
            "ON CONFLICT (profile_id, vacancy_id) DO UPDATE "
            "SET saved_at = CASE WHEN :sv THEN clock_timestamp() END, "
            "updated_at = GREATEST(profile_vacancy_state.updated_at, clock_timestamp())"
        ),
        {"pid": profile_id, "vid": vacancy_id, "sv": saved},
    )
