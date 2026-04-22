"""AI Match endpoints — analyze, results, history, feedback, implicit."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

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
from services.groq_service import GroqService
from services.job_matcher import DEFAULT_WEIGHTS
from services.match_service import MatchService
from services.translation_service import SKIP_LANGUAGES, TranslationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/match", tags=["match"])


def _get_groq(request: Request) -> GroqService:
    """Build GroqService with Redis from app state."""
    redis_client = getattr(request.app.state, "redis_client", None)
    return GroqService(redis_client=redis_client)


@router.post("/analyze", response_model=MatchAnalyzeResponse)
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
    groq = _get_groq(request)
    service = MatchService(db, groq=groq)
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


async def _build_results_response(
    results: list[dict],
    total: int,
    weights: dict,
    groq: GroqService | None = None,
):
    """Build MatchResultsResponse from service results, with title translations."""
    # Batch-translate non-EN/ES titles
    translations: dict[str, str] = {}
    if groq:
        titles_with_lang = [
            {"title": item["job"].title or "", "language": item["job"].language or ""}
            for item in results
        ]
        translator = TranslationService(groq)
        translations = await translator.translate_titles(titles_with_lang)

    data = []
    for item in results:
        match = item["match"]
        job = item["job"]

        original_title = job.title or ""
        lang = (job.language or "").lower()
        translated = translations.get(original_title)
        # Only set job_title_en when translation actually differs from original
        is_translated = (
            translated
            and translated != original_title
            and lang not in SKIP_LANGUAGES
        )
        job_title_en = translated if is_translated else None
        # Detect language for indicator when DB field is empty
        job_language = job.language
        if is_translated and not job_language:
            job_language = TranslationService._detect_language(original_title) or None

        data.append(
            MatchResultResponse(
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
                created_at=match.created_at,
                job_title=job.title,
                job_title_en=job_title_en,
                job_language=job_language,
                job_company=job.company,
                job_location=job.location,
                job_url=job.url,
                job_salary_min=job.salary_min_chf,
                job_salary_max=job.salary_max_chf,
                job_tags=job.tags or [],
                job_source=job.source,
            )
        )

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
    service = MatchService(db)
    results, total = await service.get_results(
        user_id=current_user.id,
        limit=limit,
        offset=offset,
    )

    await db.refresh(current_user, ["profile"])
    weights = (
        current_user.profile.score_weights
        if current_user.profile and current_user.profile.score_weights
        else DEFAULT_WEIGHTS
    )

    groq = _get_groq(request) if translate else None
    return await _build_results_response(results, total, weights, groq)


@router.get("/history", response_model=MatchResultsResponse)
async def get_match_history(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Get full match history for the current user (all past results)."""
    service = MatchService(db)
    results, total = await service.get_results(
        user_id=current_user.id,
        limit=limit,
        offset=offset,
    )

    await db.refresh(current_user, ["profile"])
    weights = (
        current_user.profile.score_weights
        if current_user.profile and current_user.profile.score_weights
        else DEFAULT_WEIGHTS
    )

    groq = _get_groq(request)
    return await _build_results_response(results, total, weights, groq)


@router.post("/{job_hash}/feedback", response_model=MatchFeedbackResponse)
async def submit_feedback(
    job_hash: str,
    body: MatchFeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Submit explicit feedback (thumbs_up/thumbs_down/applied/dismissed)."""
    service = MatchService(db)
    result = await service.submit_feedback(
        user_id=current_user.id,
        job_hash=job_hash,
        feedback=body.feedback,
    )

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
    service = MatchService(db)
    result = await service.clear_feedback(
        user_id=current_user.id,
        job_hash=job_hash,
    )

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
    service = MatchService(db)
    results, total = await service.get_saved_jobs(
        user_id=current_user.id,
        limit=limit,
        offset=offset,
    )

    await db.refresh(current_user, ["profile"])
    weights = (
        current_user.profile.score_weights
        if current_user.profile and current_user.profile.score_weights
        else DEFAULT_WEIGHTS
    )

    groq = _get_groq(request)
    return await _build_results_response(results, total, weights, groq)


@router.post("/{job_hash}/implicit", response_model=ImplicitFeedbackResponse)
async def submit_implicit_feedback(
    job_hash: str,
    body: ImplicitFeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record implicit feedback signal (opened, view_time, saved, applied, dismissed, skipped)."""
    service = MatchService(db)
    result = await service.record_implicit_feedback(
        user_id=current_user.id,
        job_hash=job_hash,
        action=body.action,
        duration_ms=body.duration_ms,
    )

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
