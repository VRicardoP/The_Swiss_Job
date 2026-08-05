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
def run_profile_task(self, profile_id: str, limit: int = 100) -> dict[str, Any]:
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
    # Evaluador CANÓNICO (auditoría A-08): el PRIMER (modelo, política)
    # válido en el orden determinista es el único que mueve
    # current_eval_id — el resto corre en SOMBRA (append-only). Con varios
    # modelos activos el score del feed es siempre el mismo.
    canonical_pending = True
    for model in models:
        if model.dim != embeddings.EMBED_DIM:
            logger.error(
                "matching: modelo %s/%s dim=%d != %d — saltado",
                model.name, model.version, model.dim, embeddings.EMBED_DIM,
            )
            continue
        for policy in policies:
            async with session_factory() as session:
                r = await matching.evaluate_profile(
                    session, profile_id, model.id, policy.id, limit=limit,
                    move_current=canonical_pending,
                    with_corpus_generation=on_evaluated is not None,
                )
                # MISMA transacción que la evaluación: o se registran ambas o ninguna. Solo cuenta
                # como intento si de verdad se evaluó algo: un combo sin corpus sale por 'ok' con
                # evaluated=0 y registrarlo apagaría una señal que nadie atendió (P1 rev. ronda 4).
                if on_evaluated is not None and r.get("status") == "ok" and r["evaluated"]:
                    await on_evaluated(session, r, model.id, policy.id)
                await session.commit()
            if r.get("moved_current"):
                # El canónico es el primer combo que DE VERDAD movió el
                # estado (rev. A-08 #1): un 'ok' con 0 vacantes evaluadas
                # (modelo sin embeddings de ofertas) NO lo consume — el
                # siguiente modelo puede poblar el feed.
                canonical_pending = False
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
