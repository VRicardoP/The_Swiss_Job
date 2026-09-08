"""Document generation endpoints — AI-tailored CV and cover letter.

A.SEAM (plan §15bis): el ALMACEN de documentos generados consume la
capacidad DOCUMENTOS a traves de la costura (services/documents) — la
implementacion la decide `jobhunt_routing` por perfil+capacidad, con default
'local'. Con routing 'local' el comportamiento es byte-identico al previo
(la logica de almacen vive movida verbatim en services/documents/local.py).
La API documental core existe (E.1–E.3), pero el estado no ha migrado:
el UNICO escritor sigue siendo LOCAL hasta el corte E. El adaptador canary
continua sin vincular; no se activa el core por cambiar esta cache.
La orquestacion de la generacion (Gemini/Groq, cache Redis, insumos de
perfil/oferta/match) sigue en el router: no es estado de esta capacidad.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.security import get_current_user
from database import get_db
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
from services.documents import resolve_documents
from services.gemini_service import GeminiService
from services.groq_service import GroqService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


def _get_groq(request: Request) -> GroqService:
    """Build GroqService with Redis from app state."""
    redis_client = getattr(request.app.state, "redis_client", None)
    return GroqService(redis_client=redis_client)


def _get_gemini() -> GeminiService:
    """Build GeminiService — proveedor primario de documentos (calidad)."""
    return GeminiService()


@router.post("/generate", response_model=GeneratedDocumentResponse)
async def generate_document(
    request: Request,
    body: GenerateDocumentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a tailored CV or cover letter for a specific job."""
    groq = _get_groq(request)
    gemini = _get_gemini()
    if not (gemini.is_available or groq.is_available):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI service unavailable. Configure GEMINI_API_KEY or GROQ_API_KEY.",
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

    # One snapshot drives both the cache identity and the actual LLM call.
    inputs = dict(
        cv_text=profile.cv_text,
        skills=list(profile.skills or []),
        job_title=job.title,
        job_company=job.company,
        job_description=job.description or "",
        job_tags=list(job.tags or []),
        matching_skills=list(matching_skills) if matching_skills is not None else None,
        missing_skills=list(missing_skills) if missing_skills is not None else None,
        language=body.language,
    )
    redis = getattr(request.app.state, "redis_client", None)
    cache_key = DocumentGeneratorService.cache_key(
        str(current_user.id),
        body.job_hash,
        body.doc_type.value,
        body.language,
        inputs={
            **inputs,
            "providers_available": [gemini.is_available, groq.is_available],
        },
    )
    documents = await resolve_documents(db, current_user.id)
    cached_id = None
    if redis:
        try:
            cached = await redis.get(cache_key)
            if cached:
                cached_id = uuid.UUID(
                    cached.decode() if isinstance(cached, bytes) else cached
                )
        except Exception:
            logger.debug("Redis document hint unavailable or invalid")
    if cached_id is not None:
        # Reuse the scoped port: a late Redis SET after deletion cannot resurrect
        # a document. Storage failures must propagate, not trigger a second writer.
        stored = await documents.list(
            current_user.id,
            body.job_hash,
            doc_type=body.doc_type.value,
        )
        for doc in stored.data:
            if (
                doc.id == cached_id
                and doc.job_hash == body.job_hash
                and doc.doc_type == body.doc_type
                and doc.language == body.language
            ):
                return doc

    generator = DocumentGeneratorService(groq, gemini)
    generate = (
        generator.generate_cv
        if body.doc_type == DocType.cv
        else generator.generate_cover_letter
    )
    content = await generate(**inputs)

    # Save through the same authority used to validate the cache hint.
    response = await documents.create(
        current_user.id,
        body.job_hash,
        body.doc_type.value,
        content,
        body.language,
        job_title=inputs["job_title"],
        job_company=inputs["job_company"],
    )

    # Cache in Redis
    if redis:
        try:
            ttl = settings.GROQ_DOC_CACHE_TTL_HOURS * 3600
            await redis.set(
                cache_key,
                str(response.id),
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
    documents = await resolve_documents(db, current_user.id)
    return await documents.list(current_user.id, job_hash, doc_type=doc_type)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a generated document."""
    documents = await resolve_documents(db, current_user.id)
    deleted = await documents.delete(current_user.id, document_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )
