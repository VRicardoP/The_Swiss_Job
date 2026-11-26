"""Core matching feed, facets, saved items and feedback endpoints.

Core owns matching computation; there is no interactive analyze endpoint.
The retained feedback switch and local application overlay preserve the state
whose separate migration gates are still active.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.security import get_current_user
from database import get_db
from models.user import User
from schemas.match import (
    ImplicitFeedbackRequest,
    ImplicitFeedbackResponse,
    MatchFeedbackRequest,
    MatchFeedbackResponse,
    MatchFacetsResponse,
    MatchResultResponse,
    MatchResultsResponse,
    MatchScoreBreakdown,
)
from scrapers.swiss_schools_config import get_school
from services.schools.presentation import overlay_school_results
from services.profile_defaults import DEFAULT_WEIGHTS
from services import language_store
from services.matching.core_client import CoreMatching
from services.matching.feedback import feedback_writer
from services.matching import (
    CoreUnavailableError,
    MatchingError,
    MatchingUnsupportedError,
    resolve_matching,
)
from services.matching.warm import mark_active
from utils.text import strip_html_tags

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/match", tags=["match"])


def _redis(request: Request):
    return getattr(request.app.state, "redis_client", None)


def _matching_http_error(exc: MatchingError) -> HTTPException:
    """Traduce errores de la costura a HTTP (solo alcanzable con routing a core)."""
    if isinstance(exc, CoreUnavailableError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Match feed temporarily unavailable",
        )
    if isinstance(exc, MatchingUnsupportedError):
        return HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Operation not available on the active matching backend",
        )
    raise exc  # error de programacion: no enmascarar como HTTP


def _presentation_description(job) -> str | None:
    """Plain, useful card text for historical rows lacking normalized prose."""
    description = strip_html_tags(job.description_snippet or "").strip()
    if description:
        return description
    facts = []
    if job.location:
        facts.append(f"Location: {job.location}")
    tags = [str(tag).strip() for tag in (job.tags or []) if str(tag).strip()]
    if tags:
        facts.append("Skills: " + ", ".join(tags[:8]))
    return ". ".join(facts) + "." if facts else None


def _to_match_response(
    item: dict,
    languages: dict[str, str] | None = None,
) -> MatchResultResponse:
    """Mapea un resultado del servicio a MatchResultResponse.

    Traducción e idioma llegan YA CALCULADOS. Esta función no detecta ni
    traduce nada: detectar aquí costaba 50,1 ms por oferta servida y ~90 s
    por petición (punto 5). El idioma lo resuelve `tasks.language_tasks` en
    segundo plano; el título en inglés lo traduce el CORE una vez por título
    (D10) y viaja como `VacancyDTO.title_en`."""
    match = item["match"]
    job = item["job"]

    original_title = job.title or ""
    # D10: sin `title_en` del core (aún no traducido, EN/ES, o fila local
    # del archivo `jobs`) se sirve el original. Nunca se llama al LLM aquí.
    translated = getattr(job, "title_en", None)
    is_translated = bool(translated and translated.strip() != original_title.strip())
    job_title_en = translated if is_translated else None
    # Indicador de idioma de la UI. Dos orígenes, en este orden:
    #   1. el que sirve el core desde la canónica (`VacancyDTO.language`);
    #   2. el DERIVADO ya resuelto para ese título (`job_title_languages`).
    # Si ninguno lo tiene, el indicador no se muestra y la tarea de fondo lo
    # resolverá para la próxima carga. Ausente significa «todavía no lo sé»,
    # nunca «no hay idioma»: es lo que hace innecesario detectar al servir.
    job_language = (
        job.language
        or (languages or {}).get(language_store.normalise(original_title))
        or None
    )

    # Resolver school metadata si el job es de la watchlist (tag = school.id)
    school = item.get("school")
    if (
        "school" not in item
    ):  # Local authority only; never fall back after the school cutover.
        for tag in job.tags or []:
            school = get_school(tag)
            if school:
                break

    return MatchResultResponse(
        id=match.id,
        job_hash=match.job_hash,
        score_final=match.score_final,
        scores=MatchScoreBreakdown(
            embedding=match.score_embedding,
            salary=match.score_salary,
            location=match.score_location,
            recency=match.score_recency,
            llm=match.score_llm,
        ),
        explanation=match.explanation,
        matching_skills=match.matching_skills,
        missing_skills=match.missing_skills,
        feedback=match.feedback,
        application_status=match.application_status,
        urgency_score=match.urgency_score,
        has_draft=bool(match.draft_letter),
        school_id=school.id if school else None,
        school_policy=school.policy if school else None,
        created_at=match.created_at,
        job_title=job.title,
        job_title_en=job_title_en,
        job_language=job_language,
        job_company=job.company,
        job_location=job.location,
        job_url=job.url,
        job_description=_presentation_description(job),
        job_salary_min=job.salary_min_chf,
        job_salary_max=job.salary_max_chf,
        job_tags=job.tags or [],
        job_source=job.source,
        job_category=job.category,
    )


async def _build_results_response(
    results: list[dict],
    total: int,
    weights: dict,
    db: AsyncSession | None = None,
):
    """Build MatchResultsResponse from service results.

    D10: el parámetro `translate` de las rutas se retiró. FastAPI ignora un
    query param desconocido, así que `?translate=true|false` del frontend
    sigue respondiendo 200: las dos formas sirven ya el `title_en` del core.
    """
    # Idioma ya resuelto, en UNA consulta por página. Los títulos que aún no
    # tengan fila se encolan para la tarea de fondo: encolar es un INSERT
    # idempotente que en régimen permanente no inserta nada, no una detección.
    languages: dict[str, str] = {}
    if db is not None:
        titulos = [item["job"].title or "" for item in results]
        languages, vistos = await language_store.lookup(db, titulos)
        # Sólo lo que NUNCA se ha visto. Lo ya encolado (`language IS NULL`)
        # está esperando a la tarea de fondo: reencolarlo era un INSERT por
        # página que no cambiaba nada.
        pendientes = [t for t in titulos if language_store.normalise(t) not in vistos]
        if pendientes:
            await language_store.record_pending(pendientes)

    data = [_to_match_response(item, languages) for item in results]

    return MatchResultsResponse(
        data=data,
        total=total,
        weights_used=weights,
    )


@router.get("/facets", response_model=MatchFacetsResponse)
async def get_match_facets(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not settings.CORE_MATCH_FACETS_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await mark_active(_redis(request), current_user.id)
    try:
        return await CoreMatching(db).facets(current_user.id)
    except MatchingError as exc:
        raise _matching_http_error(exc) from exc


@router.get("/results", response_model=MatchResultsResponse)
async def get_match_results(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, ge=1, le=3000),
    offset: int = Query(0, ge=0),
    category: str | None = Query(None, pattern=r"^(?:[A-M]|otros|__watchlist)$"),
):
    """Get latest AI match results for the current user."""
    if category is not None and not settings.CORE_MATCH_FACETS_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await mark_active(_redis(request), current_user.id)
    matching = (
        CoreMatching(db)
        if category is not None
        else await resolve_matching(db, current_user.id)
    )
    try:
        if category is None:
            results, total = await matching.results(
                current_user.id,
                limit=limit,
                offset=offset,
            )
        else:
            results, total = await matching.results(
                current_user.id,
                limit=limit,
                offset=offset,
                category=category,
            )
    except MatchingError as exc:
        raise _matching_http_error(exc) from exc

    await db.refresh(current_user, ["profile"])
    weights = (
        current_user.profile.score_weights
        if current_user.profile and current_user.profile.score_weights
        else DEFAULT_WEIGHTS
    )

    results = await overlay_school_results(db, current_user.id, results)
    return await _build_results_response(results, total, weights, db)


@router.get("/history", response_model=MatchResultsResponse)
async def get_match_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Get full match history for the current user (all past results).

    Hoy el legacy sirve history con la MISMA lectura que /results; la
    costura conserva esa igualdad (una sola operacion `results` del puerto).
    """
    matching = await resolve_matching(db, current_user.id)
    try:
        results, total = await matching.results(
            current_user.id,
            limit=limit,
            offset=offset,
        )
    except MatchingError as exc:
        raise _matching_http_error(exc) from exc

    await db.refresh(current_user, ["profile"])
    weights = (
        current_user.profile.score_weights
        if current_user.profile and current_user.profile.score_weights
        else DEFAULT_WEIGHTS
    )

    results = await overlay_school_results(db, current_user.id, results)
    return await _build_results_response(results, total, weights, db)


@router.post("/{job_hash}/feedback", response_model=MatchFeedbackResponse)
async def submit_feedback(
    job_hash: str,
    body: MatchFeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Submit explicit feedback (thumbs_up/thumbs_down/applied/dismissed)."""
    try:
        result = await feedback_writer(db).submit_feedback(
            user_id=current_user.id,
            job_hash=job_hash,
            feedback=body.feedback,
        )
    except MatchingError as exc:
        raise _matching_http_error(exc) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Match result not found for this job",
        )

    return MatchFeedbackResponse(
        status="success",
        job_hash=job_hash,
        feedback=body.feedback,
    )


@router.delete("/{job_hash}/feedback", response_model=MatchFeedbackResponse)
async def clear_feedback(
    job_hash: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Elimina el feedback explícito de un resultado (deselección)."""
    try:
        result = await feedback_writer(db).clear_feedback(
            user_id=current_user.id,
            job_hash=job_hash,
        )
    except MatchingError as exc:
        raise _matching_http_error(exc) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Match result not found for this job",
        )

    return MatchFeedbackResponse(
        status="success",
        job_hash=job_hash,
        feedback=None,
    )


@router.get("/saved", response_model=MatchResultsResponse)
async def get_saved_jobs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Devuelve los empleos marcados como 'Good' (thumbs_up o applied)."""
    matching = (
        feedback_writer(db)
        if settings.CORE_FEEDBACK_ENABLED
        else await resolve_matching(db, current_user.id)
    )
    try:
        results, total = await matching.saved(
            current_user.id,
            limit=limit,
            offset=offset,
        )
    except MatchingError as exc:
        raise _matching_http_error(exc) from exc

    await db.refresh(current_user, ["profile"])
    weights = (
        current_user.profile.score_weights
        if current_user.profile and current_user.profile.score_weights
        else DEFAULT_WEIGHTS
    )

    return await _build_results_response(results, total, weights, db)


@router.post("/{job_hash}/implicit", response_model=ImplicitFeedbackResponse)
async def submit_implicit_feedback(
    job_hash: str,
    body: ImplicitFeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record implicit feedback signal (opened, view_time, saved, applied, dismissed, skipped)."""
    try:
        result = await feedback_writer(db).record_implicit_feedback(
            user_id=current_user.id,
            job_hash=job_hash,
            action=body.action,
            duration_ms=body.duration_ms,
        )
    except MatchingError as exc:
        raise _matching_http_error(exc) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Match result not found for this job",
        )

    return ImplicitFeedbackResponse(
        status="success",
        job_hash=job_hash,
        action=body.action,
    )
