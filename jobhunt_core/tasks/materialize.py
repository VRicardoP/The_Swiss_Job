"""Materialización incremental del cross-encoder por watermark (P7-b).

Cadencia diaria en el NAS: un COORDINADOR (`materialize_all`) encola un
trabajo por (perfil, política CE activa) y retorna enseguida; cada trabajo
puntúa los misses del corpus vigente dentro de un FRAGMENTO acotado que cabe
en el límite blando de Celery y, SOLO si queda al día
(remaining == 0), dispara la evaluación canónica — cuya valla F3 publica una
fotografía completa de una única generación o descarta. Con backlog, el feed
vigente sigue intacto y se emite la señal (log WARNING + resultado con
`status: "backlog"`, backlog y antigüedad) — jamás un feed parcial.

Convención del repo: `def` + asyncio.run(_impl()).
"""

import asyncio
import logging
from typing import Any

import sqlalchemy as sa

from jobhunt_core import matching
from jobhunt_core.celery_app import celery_app
from jobhunt_core.database import task_session_factory

logger = logging.getLogger(__name__)

# FRAGMENTO de materialización (revisión externa 2026-09-07, P1-2): el
# presupuesto de la predeclaración (3600 s/ciclo) NO cabía en el límite blando
# de Celery (1800 s) — la tarea moría por SoftTimeLimitExceeded antes de
# agotarlo, así que el contrato «trabajo acotado + corte + señal» no podía
# cumplirse en producción. Ahora el trabajo se parte en FRAGMENTOS que
# terminan holgadamente dentro del límite blando; el backlog continúa en el
# fragmento o ciclo siguiente. Los límites de Celery NO se suben: están atados
# al visibility_timeout del canal.
MATERIALIZE_FRAGMENT_SECONDS = 1200.0
# Presupuesto de CICLO predeclarado (3d21bb4): techo de la suma de fragmentos
# de un mismo (perfil, política) dentro de una jornada. Es contable, no un
# time-limit: cada fragmento se acota por MATERIALIZE_FRAGMENT_SECONDS.
MATERIALIZE_BUDGET_SECONDS = 3600.0


@celery_app.task(name="jobhunt.matching.materialize_ce", bind=True,
                 max_retries=1)
def materialize_ce_task(self, profile_id: str, policy_id: str,
                        budget_seconds: float = MATERIALIZE_FRAGMENT_SECONDS
                        ) -> dict[str, Any]:
    try:
        return asyncio.run(_impl(profile_id, policy_id, budget_seconds))
    except Exception as exc:
        logger.error("materialize_ce %s falló: %s", profile_id, exc)
        raise self.retry(exc=exc, countdown=300)


async def _impl(profile_id: str, policy_id: str, budget_seconds: float,
                session_factory=None) -> dict[str, Any]:
    if session_factory is None:
        async with task_session_factory() as factory:
            return await _con_factory(factory, profile_id, policy_id,
                                      budget_seconds)
    return await _con_factory(session_factory, profile_id, policy_id,
                              budget_seconds)


async def _con_factory(factory, profile_id, policy_id, budget_seconds):
    """One deadline includes model lookup, preparation and final publication."""
    work_budget = max(0.0, budget_seconds - matching._margen_cierre(budget_seconds))
    if not work_budget:
        return {"profile_id": str(profile_id), "status": "backlog",
                "scored": 0, "remaining": None, "agotado": True}
    try:
        async with asyncio.timeout(work_budget):
            return await _con_factory_within_budget(factory, profile_id, policy_id, work_budget)
    except TimeoutError:
        logger.warning("materialize: end-to-end deadline expired for %s", profile_id)
        return {"profile_id": str(profile_id), "status": "backlog",
                "scored": None, "remaining": None, "agotado": True}


async def _con_factory_within_budget(factory, profile_id, policy_id, budget_seconds):
    async with factory() as session:
        model_id = await matching.canonical_model_id(session, profile_id)
    if model_id is None:
        return {"status": "sin_modelo", "profile_id": str(profile_id)}
    r = await matching.materialize_misses(
        factory, profile_id, model_id, policy_id,
        budget_seconds=budget_seconds,
    )
    resultado: dict[str, Any] = {"profile_id": str(profile_id), **r}
    if r["status"] == "ok":
        # Al día: la evaluación publica la fotografía completa (o la
        # descarta su valla). move_current SOLO si esta política es la
        # canónica AHORA — con el contrato 2026-09-04, un move_current sobre
        # política no canónica se DESCARTA entero; en sombra (pre-promoción)
        # lo correcto es registrar append-only sin mover el feed. La valla
        # F3 revalida la canonicidad bajo el lock de todas formas.
        async with factory() as session:
            canonica = (
                await session.execute(sa.text(
                    "SELECT id FROM scoring_policies WHERE active "
                    "ORDER BY name, prompt_version LIMIT 1"
                ))
            ).scalar_one_or_none()
        ev = await matching.evaluate_profile(
            factory, profile_id, model_id, policy_id,
            limit=matching.CANONICAL_EVAL_LIMIT,
            move_current=str(canonica) == str(policy_id),
            # La publicación NO puede inferir: si entre la materialización y
            # este punto cambió la revisión del perfil o el corpus, los misses
            # nuevos vuelven al materializador (revisión externa 2026-09-07).
            require_cache_only=True,
        )
        resultado["evaluacion"] = {
            k: ev.get(k) for k in ("status", "evaluated", "moved_current")
        }
        if ev["status"] == "misses_pendientes":
            resultado["status"] = "backlog"
            resultado["remaining"] = ev.get("misses")
            logger.warning(
                "materialize: %s tiene %s misses nuevos tras materializar "
                "(cambio de revisión o de corpus) — se publicará en el ciclo "
                "siguiente, sin inferir fuera de presupuesto",
                profile_id, ev.get("misses"))
    return resultado


@celery_app.task(name="jobhunt.matching.materialize_all", bind=True,
                 max_retries=0)
def materialize_all_task(self) -> dict[str, Any]:
    """Cadencia diaria (beat): materializa por watermark TODOS los perfiles
    contra cada política ACTIVA de cross-encoder. Sin políticas CE activas es
    un no-op barato (pre-promoción, la candidata inactiva no entra: su caché
    la mantiene el circuito del examen, no el beat)."""
    return asyncio.run(_all_impl())


async def _all_impl(session_factory=None) -> dict[str, Any]:
    if session_factory is None:
        async with task_session_factory() as factory:
            return await _all_con_factory(factory)
    return await _all_con_factory(session_factory)


async def _all_con_factory(factory) -> dict[str, Any]:
    async with factory() as session:
        politicas = (
            await session.execute(sa.text(
                "SELECT id, name, prompt_version, weights FROM scoring_policies "
                "WHERE active ORDER BY name, prompt_version"
            ))
        ).all()
        perfiles = (
            await session.execute(sa.text("SELECT id FROM profiles"))
        ).scalars().all()
    ce_activas = [
        p for p in politicas
        if str((p.weights or {}).get("algorithm", "")).startswith("cross_encoder")
    ]
    # COORDINADOR rápido (P1-2): encola UN trabajo por (perfil, política) y
    # retorna. Iterar aquí concedía un presupuesto completo a cada perfil sin
    # cota global y con el reloj de Celery corriendo sobre el conjunto.
    encolados = []
    for pol in ce_activas:
        for pid in perfiles:
            materialize_ce_task.apply_async(
                args=[str(pid), str(pol.id)],
                queue="core.matching",
            )
            encolados.append(f"{pol.name}:{pol.prompt_version}/{pid}")
    return {"status": "ok", "politicas_ce": len(ce_activas),
            "perfiles": len(perfiles), "encolados": encolados}
