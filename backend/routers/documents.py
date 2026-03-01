"""Document generation endpoints — AI-tailored CV and cover letter."""

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.security import get_current_user
from database import get_db
from models.generated_document import GeneratedDocument
from models.job import Job
from models.match_result import MatchResult
from models.user import User
from schemas.documents import (
    DocType,
    DocumentListResponse,
    GenerateDocumentRequest,
    GeneratedDocumentResponse,
)
from services.document_generator import DocumentGeneratorService
from services.groq_service import GroqService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


def _get_groq(request: Request) -> GroqService:
    """Build GroqService with Redis from app state."""
    redis_client = getattr(request.app.state, "redis_client", None)
    return GroqService(redis_client=redis_client)


def _to_response(
    doc: GeneratedDocument,
    job_title: str | None = None,
    job_company: str | None = None,
) -> GeneratedDocumentResponse:
    return GeneratedDocumentResponse(
        id=doc.id,
        job_hash=doc.job_hash,
        doc_type=doc.doc_type,
        content=doc.content,
        language=doc.language,
        created_at=doc.created_at,
        job_title=job_title,
        job_company=job_company,
    )


@router.post("/generate", response_model=GeneratedDocumentResponse)
async def generate_document(
    request: Request,
    body: GenerateDocumentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a tailored CV or cover letter for a specific job."""
    groq = _get_groq(request)
    if not groq.is_available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI service unavailable. GROQ_API_KEY not configured.",
        )

    # Load user profile with CV text
    await db.refresh(current_user, ["profile"])
    profile = current_user.profile
    if not profile or not profile.cv_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload your CV first before generating documents.",
        )

    # Load job
    job = (
        await db.execute(select(Job).where(Job.hash == body.job_hash))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found.",
        )

    # Check Redis cache
    redis = getattr(request.app.state, "redis_client", None)
    cache_key = DocumentGeneratorService.cache_key(
        str(current_user.id), body.job_hash, body.doc_type.value, body.language
    )
    if redis:
        try:
            cached = await redis.get(cache_key)
            if cached:
                cached_data = json.loads(cached)
                return GeneratedDocumentResponse(**cached_data)
        except Exception:
            logger.debug("Redis cache read failed for %s", cache_key)

    # Load match data if available (for matching/missing skills)
    match_result = (
        await db.execute(
            select(MatchResult).where(
                MatchResult.user_id == current_user.id,
                MatchResult.job_hash == body.job_hash,
            )
        )
    ).scalar_one_or_none()

    matching_skills = match_result.matching_skills if match_result else None
    missing_skills = match_result.missing_skills if match_result else None

    # Generate document
    generator = DocumentGeneratorService(groq)
    if body.doc_type == DocType.cv:
        content = await generator.generate_cv(
            cv_text=profile.cv_text,
            skills=profile.skills or [],
            job_title=job.title,
            job_company=job.company,
            job_description=job.description or "",
            job_tags=job.tags or [],
            matching_skills=matching_skills,
            missing_skills=missing_skills,
            language=body.language,
        )
    else:
        content = await generator.generate_cover_letter(
            cv_text=profile.cv_text,
            skills=profile.skills or [],
            job_title=job.title,
            job_company=job.company,
            job_description=job.description or "",
            job_tags=job.tags or [],
            matching_skills=matching_skills,
            missing_skills=missing_skills,
            language=body.language,
        )

    # Save to DB
    doc = GeneratedDocument(
        user_id=current_user.id,
        job_hash=body.job_hash,
        doc_type=body.doc_type.value,
        content=content,
        language=body.language,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    response = _to_response(doc, job_title=job.title, job_company=job.company)

    # Cache in Redis
    if redis:
        try:
            ttl = settings.GROQ_DOC_CACHE_TTL_HOURS * 3600
            await redis.set(
                cache_key,
                json.dumps(response.model_dump(), default=str),
                ex=ttl,
            )
        except Exception:
            logger.debug("Redis cache write failed for %s", cache_key)

    return response


@router.get("/{job_hash}", response_model=DocumentListResponse)
async def list_documents(
    job_hash: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    doc_type: str | None = Query(None),
):
    """List generated documents for a specific job."""
    conditions = [
        GeneratedDocument.user_id == current_user.id,
        GeneratedDocument.job_hash == job_hash,
    ]
    if doc_type:
        conditions.append(GeneratedDocument.doc_type == doc_type)

    stmt = (
        select(GeneratedDocument, Job)
        .outerjoin(Job, GeneratedDocument.job_hash == Job.hash)
        .where(*conditions)
        .order_by(GeneratedDocument.created_at.desc())
    )
    rows = (await db.execute(stmt)).all()

    data = [
        _to_response(
            doc,
            job_title=job.title if job else None,
            job_company=job.company if job else None,
        )
        for doc, job in rows
    ]

    return DocumentListResponse(data=data, total=len(data))


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a generated document."""
    doc = (
        await db.execute(
            select(GeneratedDocument).where(
                GeneratedDocument.id == document_id,
                GeneratedDocument.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )
    await db.delete(doc)
    await db.commit()
