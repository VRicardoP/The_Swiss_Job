"""AI Match endpoints — analyze, results, history, feedback, implicit.

A.SEAM (plan §15bis): las LECTURAS DEL FEED (/results, /history, /saved)
consumen la capacidad MATCHING a traves de la costura (services/matching) —
la implementacion (local|core) la decide `jobhunt_routing` POR PERFIL
(users.id), con default 'local' (comportamiento byte-identico al previo:
LocalMatching delega verbatim en MatchResultService).

El DISPARO DEL PIPELINE (/analyze) respeta el routing desde Fase D (gate
anti-doble-motor D.2, complemento interactivo del D.1 de los schedulers):
con el matching del perfil gobernado por el core
(core_read/core_primary/rollback_pending) responde 409 SIN instanciar
servicios LLM ni tocar el motor local. Antes del corte F, feedback y guardados
siguen en local. CORE_FEEDBACK_ENABLED cambia ambos al core, con escritura
síncrona e idempotencia, sin fallback local. No habilitarlo hasta reconciliar
la migración bajo FEEDBACK_WRITES_FROZEN y comprobar el routing autoritativo.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.rate_limit import limiter
from core.security import get_current_user
from database import get_db
from models.user import User
from schemas.match import (
    ImplicitFeedbackRequest,
    ImplicitFeedbackResponse,
    MatchAnalyzeRequest,
    MatchAnalyzeResponse,
    MatchFeedbackRequest,
    MatchFeedbackResponse,
    MatchResultResponse,
    MatchResultsResponse,
    MatchScoreBreakdown,
)
from scrapers.swiss_schools_config import get_school
from services.schools.presentation import overlay_school_results
from services.gemini_service import GeminiService
from services.groq_service import GroqService
from services.job_matcher import DEFAULT_WEIGHTS
from services import language_store
from services.matching.feedback import feedback_writer
from services.match_service import MatchService
from services.matching import (
    CoreUnavailableError,
    MatchingError,
    MatchingUnsupportedError,
    resolve_matching,
)
from services.routing import CAPABILITY_MATCHING, legacy_owns, resolve_mode
from services.translation_service import TranslationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/match", tags=["match"])


def _get_groq(request: Request) -> GroqService:
    """Build GroqService with Redis from app state."""
    redis_client = getattr(request.app.state, "redis_client", None)
    return GroqService(redis_client=redis_client)


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


@router.post("/analyze", response_model=MatchAnalyzeResponse)
@limiter.limit("3/minute")
async def analyze_matches(
    request: Request,
    body: MatchAnalyzeRequest = MatchAnalyzeRequest(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger AI matching pipeline for the current user.

    Stage 1: pgvector cosine similarity on ALL active jobs
    Stage 2: Multi-factor scoring (embedding + salary + location + recency)
    Stage 3: LLM re-ranking via Groq (if API key configured) — top-K only
    """
    # Gate anti-doble-motor D.2 (plan §15bis): descartar el perfil migrado
    # ANTES de instanciar servicios LLM o tocar el motor, con UNA consulta de
    # routing (resolve_mode, con cache en proceso) — cero coste para el
    # migrado.
    mode = await resolve_mode(db, CAPABILITY_MATCHING, current_user.id)
    if not legacy_owns(mode):
        # 409 y no 501: 501 (via _matching_http_error) significa "el backend
        # activo no sabe servir esta operacion" (cota de contrato del /v1).
        # Aqui la operacion es valida, pero ejecutarla chocaria con el estado
        # del recurso: el ESCRITOR autoritativo del matching de este perfil
        # es el core (matriz de escritor §15bis) y un recalculo local seria
        # el doble motor que D.1 cerro en los schedulers. Ese conflicto con
        # el estado actual del recurso es la semantica de 409 Conflict; y no
        # es una carencia permanente (tras un rollback completo el perfil
        # vuelve a ser analizable en local).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Matching for this profile is governed by the core engine; "
                "results recompute automatically when your CV or the job "
                "corpus changes. Local analysis is disabled."
            ),
        )

    groq = _get_groq(request)
    service = MatchService(db, groq=groq, gemini=GeminiService())
    result = await service.run_matching(
        user_id=current_user.id,
        min_score=body.min_score,
    )

    if result.get("status") == "no_embedding":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profile has no CV embedding. Upload a CV first.",
        )

    if result.get("status") == "error":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result.get("reason", "Unknown error"),
        )

    return MatchAnalyzeResponse(
        status=result["status"],
        total_candidates=result.get("total_candidates", 0),
        results_count=result.get("results_count", 0),
    )


def _to_match_response(
    item: dict,
    translations: dict[str, str],
    languages: dict[str, str] | None = None,
) -> MatchResultResponse:
    """Mapea un resultado del servicio a MatchResultResponse.

    Traducción e idioma llegan YA CALCULADOS. Esta función no detecta nada:
    detectar aquí costaba 50,1 ms por oferta servida y ~90 s por petición
    (punto 5). Quien detecta es `tasks.language_tasks`, en segundo plano."""
    match = item["match"]
    job = item["job"]

    original_title = job.title or ""
    translated = translations.get(original_title)
    # Mostrar traducción si el LLM produjo algo diferente al original.
    # No usar job.language para bloquear: puede ser erróneo (langdetect falla en
    # títulos cortos o mixtos alemán-inglés → job.language = "en" incorrecto).
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
        job_description=job.description_snippet,
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
    groq: GroqService | None = None,
    db: AsyncSession | None = None,
):
    """Build MatchResultsResponse from service results, with title translations."""
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

    # Batch-translate non-EN/ES titles
    translations: dict[str, str] = {}
    if groq:
        titles_with_lang = [
            {"title": item["job"].title or "", "language": item["job"].language or ""}
            for item in results
        ]
        translator = TranslationService(groq)
        translations = await translator.translate_titles(
            titles_with_lang, languages=languages
        )

    data = [_to_match_response(item, translations, languages) for item in results]

    return MatchResultsResponse(
        data=data,
        total=total,
        weights_used=weights,
    )


@router.get("/results", response_model=MatchResultsResponse)
async def get_match_results(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, ge=1, le=3000),
    offset: int = Query(0, ge=0),
    translate: bool = Query(True),
):
    """Get latest AI match results for the current user.

    translate=false omite la traducción de títulos (útil para carga masiva
    de categorización donde las traducciones no son necesarias).
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
    groq = _get_groq(request) if translate else None
    return await _build_results_response(results, total, weights, groq, db)


@router.get("/history", response_model=MatchResultsResponse)
async def get_match_history(
    request: Request,
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
    groq = _get_groq(request)
    return await _build_results_response(results, total, weights, groq, db)


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
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Devuelve los empleos marcados como 'Good' (thumbs_up o applied)."""
    matching = await resolve_matching(db, current_user.id)
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

    groq = _get_groq(request)
    return await _build_results_response(results, total, weights, groq, db)


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
