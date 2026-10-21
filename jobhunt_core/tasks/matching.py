"""Tarea Celery de matching por perfil (A-08) — cola core.matching.

Convención del repo: `def` + asyncio.run(_impl()). Evalúa el perfil con cada
(modelo activo 384, política activa): la evaluación es idempotente por
eval_key, así que el reintento no duplica (DoD).
"""

import asyncio
import logging
from typing import Any
import uuid

import sqlalchemy as sa

from jobhunt_core import embeddings, matching
from jobhunt_core.celery_app import celery_app
from jobhunt_core.database import task_session_factory
from jobhunt_core.tasks.embedding import _run_pending_impl

logger = logging.getLogger(__name__)

RECOVERY_MAX_PROFILES = 1
RECOVERY_EMBEDDING_ROUNDS = 1
_NO_PROFILE = uuid.UUID(int=0)

_ACTIVE_COMBOS_SQL = f"""
SELECT m.id AS model_id, sp.id AS policy_id,
       EXISTS (SELECT 1 {matching.ELIGIBLE_CORPUS_FROM.format(model="m.id")} LIMIT 1) AS has_corpus
  FROM embedding_models m
  CROSS JOIN scoring_policies sp
 WHERE m.active AND m.dim = :dim AND sp.active
   AND COALESCE(sp.weights->>'algorithm', '') NOT LIKE 'cross_encoder%'
"""

_RECOVERY_NEEDED_SQL = f"""
WITH combos AS ({_ACTIVE_COMBOS_SQL}),
gen AS (SELECT generation FROM corpus_generation WHERE id = 1),
candidates AS (
    SELECT p.id, cur.revision_id
      FROM profiles p
      JOIN LATERAL (
            SELECT a.revision_id
              FROM profile_revision_activations a
             WHERE a.profile_id = p.id
             ORDER BY a.seq DESC
             LIMIT 1
           ) cur ON true
     WHERE p.projection_active
       AND p.id NOT IN :evaluated
       AND EXISTS (
            SELECT 1
              FROM combos c
              LEFT JOIN profile_recovery_state w
                ON w.profile_revision_id = cur.revision_id
               AND w.model_id = c.model_id
               AND w.policy_id = c.policy_id
             WHERE c.has_corpus
               AND EXISTS (SELECT 1 FROM profile_embeddings pe
                            WHERE pe.profile_revision_id = cur.revision_id
                              AND pe.model_id = c.model_id)
               AND (w.profile_revision_id IS NULL
                    OR w.corpus_generation IS DISTINCT FROM (SELECT generation FROM gen)))
)
SELECT ca.id,
       (SELECT min(rs.attempted_at)
          FROM profile_recovery_state rs
          JOIN combos c2
            ON c2.model_id = rs.model_id AND c2.policy_id = rs.policy_id
         WHERE rs.profile_revision_id = ca.revision_id
           AND c2.has_corpus
           AND EXISTS (SELECT 1 FROM profile_embeddings pe2
                        WHERE pe2.profile_revision_id = ca.revision_id
                          AND pe2.model_id = c2.model_id)) AS last_try
  FROM candidates ca
 ORDER BY last_try ASC NULLS FIRST, ca.id
 LIMIT :cap
"""

_RECORD_ATTEMPT_SQL = """
INSERT INTO profile_recovery_state
    (profile_revision_id, profile_id, model_id, policy_id, corpus_generation, attempted_at)
VALUES (:rev, :pid, :model, :policy, :gen, now())
ON CONFLICT (profile_revision_id, model_id, policy_id) DO UPDATE
SET corpus_generation = EXCLUDED.corpus_generation, attempted_at = EXCLUDED.attempted_at
"""


@celery_app.task(name="jobhunt.matching.run_profile", bind=True, max_retries=1)
def run_profile_task(
    self, profile_id: str, limit: int = matching.CANONICAL_EVAL_LIMIT
) -> dict[str, Any]:
    try:
        return asyncio.run(_run_profile_impl(profile_id, limit))
    except Exception as exc:
        # Transitorios (BD): retry. La config inválida no llega aquí — los
        # modelos/políticas activos ya pasaron sus validaciones de registro.
        logger.error("matching.run_profile %s falló: %s", profile_id, exc)
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="jobhunt.matching.recover", bind=True, max_retries=1)
def recover_task(self) -> dict[str, Any]:
    """Recover one stale active profile per tick with one embedding pass."""
    try:
        return asyncio.run(_recover_impl())
    except Exception as exc:
        logger.error("matching.recover failed: %s", exc)
        raise self.retry(exc=exc, countdown=120)


async def _recover_impl(session_factory=None) -> dict[str, Any]:
    if session_factory is None:
        async with task_session_factory() as factory:
            return await _recover_with(factory)
    return await _recover_with(session_factory)


async def _recover_with(session_factory) -> dict[str, Any]:
    await _drain_embeddings(session_factory)
    async with session_factory() as session:
        targets = await _recovery_targets(session)
    for profile_id in targets:
        await _evaluate_and_record(session_factory, profile_id)
    return {"profiles_evaluated": len(targets)}


async def _drain_embeddings(session_factory, limit: int = 200) -> None:
    # DT-148 B4: el parámetro `max_rounds` existía y se acotaba con
    # `min(max_rounds, RECOVERY_EMBEDDING_ROUNDS)` sobre una constante que vale
    # 1: ningún valor pasado podía superarla, así que el mando no mandaba.
    for _ in range(RECOVERY_EMBEDDING_ROUNDS):
        result = await _run_pending_impl(limit, session_factory=session_factory)
        if not any(result["embedded"].values()) and not any(
            result["profiles_embedded"].values()
        ):
            return
    logger.info("matching recovery: embedding pass reached its per-tick limit")


async def _recovery_targets(session) -> list:
    # DT-148 B4: `limit` se acotaba con `min(limit, RECOVERY_MAX_PROFILES)` — la
    # constante vale 1 y nadie pasaba un valor— y `evaluated` llegaba SIEMPRE
    # vacío desde el único llamador, con lo que el NOT IN se ataba al UUID cero.
    # Ese centinela se queda: un `IN ()` vacío no es SQL válido.
    rows = (
        await session.execute(
            sa.text(_RECOVERY_NEEDED_SQL).bindparams(
                sa.bindparam("evaluated", expanding=True)
            ),
            {
                "evaluated": [_NO_PROFILE],
                "dim": embeddings.EMBED_DIM,
                "cap": RECOVERY_MAX_PROFILES,
            },
        )
    ).all()
    return [row.id for row in rows]


async def _evaluate_and_record(session_factory, profile_id) -> None:
    async def record(session, result, model_id, policy_id) -> None:
        if result["corpus_generation"] is None:
            return
        await session.execute(
            sa.text(_RECORD_ATTEMPT_SQL),
            {
                "rev": result["profile_revision_id"],
                "pid": profile_id,
                "model": model_id,
                "policy": policy_id,
                "gen": result["corpus_generation"],
            },
        )

    await _run_profile_impl(
        str(profile_id),
        matching.CANONICAL_EVAL_LIMIT,
        session_factory=session_factory,
        on_evaluated=record,
    )


async def _run_profile_impl(
    profile_id: str, limit: int, session_factory=None, on_evaluated=None
) -> dict[str, Any]:
    """`session_factory` opcional (2º análisis B-02, P3): el proyector de la
    sombra corre TODO dentro de un único asyncio.run y pasa SU propia factory
    — sin crear/desechar un engine NullPool por llamada. Sin ella (tarea
    Celery standalone: event loop nuevo por asyncio.run), el engine
    desechable de task_session_factory sigue siendo obligatorio.

    `on_evaluated` (opcional): corrutina (session, resultado, model_id, policy_id) que se invoca
    SOLO tras una evaluación EFECTIVA (`ok` con candidatos) y DENTRO de su misma transacción — con
    el lock del perfil aún tomado. Su presencia activa además la lectura de la generación del corpus. Es la costura que usa el proyector para registrar su watermark de intento sin
    que este módulo sepa nada de él (P1 rev. externa ronda 3: registrarlo aparte y re-consultando la
    revisión vigente podía apagar la señal de una revisión que NADIE evaluó)."""
    if session_factory is not None:
        return await _run_profile_with(session_factory, profile_id, limit, on_evaluated)
    async with task_session_factory() as factory:
        return await _run_profile_with(factory, profile_id, limit, on_evaluated)


async def _run_profile_with(
    session_factory, profile_id: str, limit: int, on_evaluated=None
) -> dict[str, Any]:
    results: dict[str, dict] = {}
    async with session_factory() as session:
        exists = (
            await session.execute(
                sa.text("SELECT 1 FROM profiles WHERE id = :pid"),
                {"pid": profile_id},
            )
        ).scalar_one_or_none()
        if exists is None:
            # Perfil eliminado tras encolar: permanente y normal, sin retry
            # (disciplina de clasificación de A-04).
            return {"status": "not_found", "results": {}}
        models = await embeddings.active_models(session)
        policies = (
            await session.execute(
                sa.text(
                    "SELECT id, name, prompt_version, weights "
                    "FROM scoring_policies "
                    "WHERE active ORDER BY name, prompt_version"
                )
            )
        ).all()
        # Evaluador CANÓNICO (A-08 + revisión 2026-09-04 1B): el modelo lo
        # fija canonical_model_id — LA MISMA definición que revalida la valla
        # final de evaluate_profile con el id exacto —, y la política es la
        # primera activa del orden determinista. El resto corre en SOMBRA
        # (append-only). La decisión de aquí puede caducar durante una
        # inferencia larga: la valla la recomputa bajo el lock.
        canon_modelo = await matching.canonical_model_id(session, profile_id)
    for model in models:
        if model.dim != embeddings.EMBED_DIM:
            logger.error(
                "matching: modelo %s/%s dim=%d != %d — saltado",
                model.name,
                model.version,
                model.dim,
                embeddings.EMBED_DIM,
            )
            continue
        for policy in policies:
            # P1-3: evaluate_profile es TRIFÁSICO (prepara/inflere/persiste
            # con transacciones cortas propias); on_evaluated corre dentro de
            # su fase de persistencia — misma transacción, o ambas o ninguna.
            # Un resultado vacío también es un intento válido tras revalidar
            # la generación y publicar el feed vacío. Los caminos sin vector,
            # no encontrados o descartados retornan antes de esta costura.
            if str((policy.weights or {}).get("algorithm", "")).startswith(
                "cross_encoder"
            ):
                # P1-3 (revisión externa 2026-09-07): la inferencia del
                # cross-encoder cuesta horas y TIENE un camino presupuestado
                # (jobhunt.matching.materialize_ce). Evaluarla aquí —desde el
                # ciclo diario o desde el proyector— la ejecutaba entera y sin
                # cota, saltándose el contrato de P7-b. El ciclo evalúa los
                # algoritmos baratos; el CE se delega a su materializador, que
                # publica cuando queda al día.
                key_ce = (
                    f"{model.name}@{model.version}/"
                    f"{policy.name}@{policy.prompt_version}"
                )
                results[key_ce] = {
                    "status": "delegado_a_materializacion",
                    "evaluated": 0,
                    "new_evals": 0,
                    "moved_current": False,
                }
                continue
            es_canonico = (
                canon_modelo is not None
                and str(model.id) == str(canon_modelo)
                and str(policy.id) == str(policies[0].id)
            )
            r = await matching.evaluate_profile(
                session_factory,
                profile_id,
                model.id,
                policy.id,
                limit=limit,
                move_current=es_canonico,
                on_evaluated=on_evaluated,
            )
            if r.get("status") == "descartado_por_deriva":
                # Revisión 2026-09-04: la tupla revalidada derivó durante la
                # evaluación — se reintenta UNA vez desde la fase 1 con lo
                # vigente. Si vuelve a derivar (corpus en mutación continua),
                # se deja para el ciclo siguiente: jamás publicar lo rancio.
                logger.warning(
                    "matching: deriva durante la evaluación de %s con %s/%s "
                    "— reintento único desde la fase 1",
                    profile_id,
                    model.name,
                    policy.name,
                )
                # «Desde la fase 1» incluye la decisión de canonicidad: se
                # recomputa con lo VIGENTE antes de reintentar.
                async with session_factory() as s2:
                    canon_modelo = await matching.canonical_model_id(s2, profile_id)
                es_canonico = (
                    canon_modelo is not None
                    and str(model.id) == str(canon_modelo)
                    and str(policy.id) == str(policies[0].id)
                )
                r = await matching.evaluate_profile(
                    session_factory,
                    profile_id,
                    model.id,
                    policy.id,
                    limit=limit,
                    move_current=es_canonico,
                    on_evaluated=on_evaluated,
                )
            recipe = (
                ""
                if model.recipe_version == "legacy_v1"
                else f"#{model.recipe_version}"
            )
            key = (
                f"{model.name}@{model.version}{recipe}/"
                f"{policy.name}@{policy.prompt_version}"
            )
            results[key] = r
    return {"status": "ok", "results": results}
