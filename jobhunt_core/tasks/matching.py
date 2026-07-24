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


async def _run_profile_impl(profile_id: str, limit: int) -> dict[str, Any]:
    results: dict[str, dict] = {}
    async with task_session_factory() as session_factory:
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
            models = await embeddings.active_models(session)  # ORDER BY name, version
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
                    )
                    await session.commit()
                if r.get("moved_current"):
                    # El canónico es el primer combo que DE VERDAD movió el
                    # estado (rev. A-08 #1): un 'ok' con 0 vacantes evaluadas
                    # (modelo sin embeddings de ofertas) NO lo consume — el
                    # siguiente modelo puede poblar el feed.
                    canonical_pending = False
                # La clave incluye la VERSIÓN del modelo (auditoría A-08: dos
                # modelos con el mismo name colapsaban en una sola entrada).
                key = f"{model.name}@{model.version}/{policy.name}@{policy.prompt_version}"
                results[key] = r
    return {"status": "ok", "results": results}
