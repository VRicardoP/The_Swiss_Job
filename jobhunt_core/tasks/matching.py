"""Tarea Celery de matching por perfil (A-08) — cola core.matching.

Convención del repo: `def` + asyncio.run(_impl()). Evalúa el perfil con cada
(modelo activo 384, política activa): la evaluación es idempotente por
eval_key, así que el reintento no duplica (DoD).
"""

import asyncio
import logging
from typing import Any

import sqlalchemy as sa

from jobhunt_core import embeddings, matching
from jobhunt_core.celery_app import celery_app
from jobhunt_core.database import task_session_factory

logger = logging.getLogger(__name__)


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
                    "SELECT id, name, prompt_version FROM scoring_policies "
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
                model.name, model.version, model.dim, embeddings.EMBED_DIM,
            )
            continue
        for policy in policies:
            # P1-3: evaluate_profile es TRIFÁSICO (prepara/inflere/persiste
            # con transacciones cortas propias); on_evaluated corre dentro de
            # su fase de persistencia — misma transacción, o ambas o ninguna.
            # Solo cuenta como intento si de verdad se evaluó algo (P1 rev.
            # ronda 4): el propio evaluate lo garantiza invocándolo solo con
            # evaluated > 0.
            es_canonico = (
                canon_modelo is not None
                and str(model.id) == str(canon_modelo)
                and str(policy.id) == str(policies[0].id)
            )
            r = await matching.evaluate_profile(
                session_factory, profile_id, model.id, policy.id, limit=limit,
                move_current=es_canonico,
                with_corpus_generation=on_evaluated is not None,
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
                    profile_id, model.name, policy.name,
                )
                # «Desde la fase 1» incluye la decisión de canonicidad: se
                # recomputa con lo VIGENTE antes de reintentar.
                async with session_factory() as s2:
                    canon_modelo = await matching.canonical_model_id(
                        s2, profile_id)
                es_canonico = (
                    canon_modelo is not None
                    and str(model.id) == str(canon_modelo)
                    and str(policy.id) == str(policies[0].id)
                )
                r = await matching.evaluate_profile(
                    session_factory, profile_id, model.id, policy.id,
                    limit=limit, move_current=es_canonico,
                    with_corpus_generation=on_evaluated is not None,
                    on_evaluated=on_evaluated,
                )
            recipe = (
                "" if model.recipe_version == "legacy_v1"
                else f"#{model.recipe_version}"
            )
            key = (
                f"{model.name}@{model.version}{recipe}/"
                f"{policy.name}@{policy.prompt_version}"
            )
            results[key] = r
    return {"status": "ok", "results": results}
