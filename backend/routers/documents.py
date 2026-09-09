"""Document generation and owner-scoped recovery.

Local modes keep the local writer; core modes require migrated state and a stable
caller operation UUID. Prepared output commits before HTTP; retry never calls the
LLM. Freeze/drain is mandatory before a routing flip or rollback.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from config import settings
from core.security import get_current_user
from database import get_db
from models.job import Job
from models.document_delivery import DocumentDelivery
from models.match_result import MatchResult
from models.user import User
from schemas.documents import (
    DocType,
    DocumentOperationResponse,
    DocumentPageResponse,
    DocumentListResponse,
    GenerateDocumentRequest,
    GeneratedDocumentResponse,
)
from services.catalog import resolve_catalog, CoreUnavailableError as CatalogUnavailableError, CatalogUnsupportedError
from services.document_generator import DocumentGeneratorService
from services.documents import CoreDocuments, resolve_documents
from services.documents.delivery import deliver, enqueue, operation_status, request_hash
from services.documents.freeze import block_document_writes
from services.gemini_service import GeminiService
from services.groq_service import GroqService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/documents", tags=["documents"],
    dependencies=[Depends(block_document_writes)],
)


def _get_groq(request: Request) -> GroqService:
    """Build GroqService with Redis from app state."""
    redis_client = getattr(request.app.state, "redis_client", None)
    return GroqService(redis_client=redis_client)


def _get_gemini() -> GeminiService:
    """Build GeminiService — proveedor primario de documentos (calidad)."""
    return GeminiService()


@router.post("/generate", response_model=GeneratedDocumentResponse | DocumentOperationResponse)
async def generate_document(
    request: Request,
    body: GenerateDocumentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate once, or resume the previously prepared operation."""
    user_id = current_user.id
    if body.operation_id is not None:
        existing = await operation_status(
            db, body.operation_id, user_id,
            expected_request_hash=request_hash(body.job_hash, body.doc_type.value, body.language),
        )
        if existing is not None:
            await db.commit()
            outcome = await deliver(async_sessionmaker(db.bind, expire_on_commit=False), body.operation_id, user_id)
            return JSONResponse(status_code=202, content=jsonable_encoder(outcome))
    documents = await resolve_documents(db, user_id)
    if isinstance(documents, CoreDocuments) and body.operation_id is None:
        raise HTTPException(status_code=422, detail="Core generation requires a stable operation_id.")
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

    # The catalog can serve core UUIDs with no local Job. Preserve that
    # reference; generation must not create a second copy of the corpus.
    if len(body.job_hash) > 32:
        catalog = await resolve_catalog(db)
        await db.commit()  # release the profile read transaction before HTTP
        try:
            job = await catalog.get(body.job_hash)
        except CatalogUnavailableError:
            raise HTTPException(status_code=503, detail="Catalog temporarily unavailable.") from None
        except CatalogUnsupportedError:
            raise HTTPException(status_code=501, detail="Offer unavailable on the active catalog.") from None
    else:
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
    await db.commit()  # input snapshot complete; Redis/core HTTP never retains this transaction
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
            user_id,
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

    # Materialized inputs survive this short read transaction. No provider call
    # holds a connection/transaction while waiting for inference.
    await db.commit()
    generator = DocumentGeneratorService(groq, gemini)
    generate = (
        generator.generate_cv
        if body.doc_type == DocType.cv
        else generator.generate_cover_letter
    )
    content = await generate(**inputs)

    # Re-resolve after inference: never retain a pre-cutover writer decision.
    documents = await resolve_documents(db, user_id, write=True)
    live_owner = await db.scalar(select(User.id).where(
        User.id == user_id, User.is_active.is_(True)).with_for_update(read=True))
    if live_owner is None:
        raise HTTPException(status_code=403, detail="Document owner is no longer active.")
    if isinstance(documents, CoreDocuments):
        if body.operation_id is None:
            raise HTTPException(status_code=409, detail="Document authority changed; retry with an operation_id.")
        await enqueue(
            db, operation_id=body.operation_id, user_id=user_id, profile_id=documents.profile_id,
            job_hash=body.job_hash, doc_type=body.doc_type.value, language=body.language,
            content=content, job_title=inputs["job_title"], job_company=inputs["job_company"],
        )
        await db.commit()
        outcome = await deliver(async_sessionmaker(db.bind, expire_on_commit=False), body.operation_id, user_id)
        return JSONResponse(status_code=202, content=jsonable_encoder(outcome))
    response = await documents.create(
        user_id,
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


@router.get("", response_model=DocumentPageResponse)
async def document_library(
    cursor: str | None = Query(None, max_length=512),
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    user_id = current_user.id
    documents = await resolve_documents(db, user_id)
    if isinstance(documents, CoreDocuments):
        await db.commit()
    return await documents.page(user_id, cursor=cursor)


@router.get("/operations")
async def pending_document_operations(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    conditions = (DocumentDelivery.user_id == current_user.id, DocumentDelivery.delivered_at.is_(None))
    total = await db.scalar(select(func.count()).select_from(DocumentDelivery).where(*conditions))
    rows = (await db.scalars(select(DocumentDelivery).where(*conditions).order_by(
        DocumentDelivery.created_at, DocumentDelivery.operation_id).limit(20))).all()
    return {"data": [{"operation_id": row.operation_id, "status": "pending",
                      "document_id": None, "error": row.last_error} for row in rows],
            "total": total}


@router.get("/operations/{operation_id}", response_model=DocumentOperationResponse)
async def document_operation(
    operation_id: uuid.UUID, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    state = await operation_status(db, operation_id, current_user.id)
    if state is None:
        raise HTTPException(status_code=404, detail="Document operation not found.")
    return state


@router.post("/operations/{operation_id}/retry", response_model=DocumentOperationResponse)
async def retry_document_operation(
    operation_id: uuid.UUID, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = current_user.id
    if await operation_status(db, operation_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Document operation not found.")
    await db.commit()
    return await deliver(async_sessionmaker(db.bind, expire_on_commit=False), operation_id, user_id)


@router.get("/item/{document_id}", response_model=GeneratedDocumentResponse)
async def get_document(
    document_id: uuid.UUID, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = current_user.id
    documents = await resolve_documents(db, user_id)
    if isinstance(documents, CoreDocuments):
        await db.commit()
    item = await documents.get(user_id, document_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return item


@router.get("/{job_hash}", response_model=DocumentListResponse)
async def list_documents(
    job_hash: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    doc_type: str | None = Query(None),
):
    """List generated documents for a specific job."""
    user_id = current_user.id
    documents = await resolve_documents(db, user_id)
    if isinstance(documents, CoreDocuments):
        await db.commit()
    return await documents.list(user_id, job_hash, doc_type=doc_type)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a generated document."""
    user_id = current_user.id
    documents = await resolve_documents(db, user_id, write=True)
    if isinstance(documents, CoreDocuments):
        await db.commit()
    deleted = await documents.delete(user_id, document_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )
